"""规则引擎单元测试（不依赖 FastAPI，纯引擎层）。"""

import pytest

from app.engine import (
    DEFAULT_MAX_STEPS,
    LOOP_STATUS,
    RewriteSyntaxError,
    parse_rules,
    simulate,
)


def parse(text):
    return parse_rules(text)


# ---------- 解析与替换 ----------


def test_comments_and_blank_lines_ignored():
    rules = parse("# 这是注释\n\n   \n^/a$ /b\n")
    assert len(rules) == 1
    assert rules[0].line_no == 4


def test_dollar_and_backslash_capture_syntaxes():
    rules = parse(
        r"^/x/(?P<n>\d+)/(.)$ /a/$1-${n}-\2-\g<2> break"
    )
    rep = rules[0].replacement
    assert rep == r"/a/\g<1>-\g<n>-\g<2>-\g<2>"


def test_whole_match_group_zero():
    rules = parse(r"(.*) /p$0 break")
    rep = simulate(rules, "/hello")
    assert rep["final_url"] == "/p/hello"


def test_bad_regex_reports_line_and_column():
    with pytest.raises(RewriteSyntaxError) as exc:
        parse("^/a$ /b break\n^/oops/([a-z]+$ /x\n")
    err = exc.value.errors[0]
    assert err.line == 2
    assert err.column >= 1
    assert "正则" in err.message


def test_missing_capture_group_number():
    with pytest.raises(RewriteSyntaxError) as exc:
        parse("^/x$ /$2 break")
    assert "第 2 个捕获组" in exc.value.errors[0].message


def test_missing_named_group():
    with pytest.raises(RewriteSyntaxError) as exc:
        parse("^/x$ /${who} break")
    assert "who" in exc.value.errors[0].message


def test_unknown_flag():
    with pytest.raises(RewriteSyntaxError) as exc:
        parse("^/x$ /y brk")
    assert "未知标记" in exc.value.errors[0].message


def test_conflicting_flags():
    with pytest.raises(RewriteSyntaxError) as exc:
        parse("^/x$ /y break,last")
    assert any("冲突" in e.message for e in exc.value.errors)


def test_redirect_code_range():
    with pytest.raises(RewriteSyntaxError):
        parse("^/x$ /y redirect=200")
    rules = parse("^/x$ /y redirect=307")
    assert rules[0].action == "redirect"
    assert rules[0].status == 307


def test_too_few_fields():
    with pytest.raises(RewriteSyntaxError) as exc:
        parse("^/only-pattern$")
    assert "至少需要两个字段" in exc.value.errors[0].message


def test_multiple_errors_collected():
    with pytest.raises(RewriteSyntaxError) as exc:
        parse("[bad /x\n^/a$ /$9\n")
    assert len(exc.value.errors) == 2


def test_column_precise_under_extra_whitespace():
    # pattern(7) + 3 个空格 + replacement(3) + 2 个空格 + 错误标记
    text = "^/a/$   /$2   badflag\n"
    with pytest.raises(RewriteSyntaxError) as exc:
        parse(text)
    by_msg = {e.message: e.column for e in exc.value.errors}
    # "/$2" 起始列 = 10（0-based 偏移 9），$2 的 $ 位于替换串第 1 列 => 第 10 列
    assert by_msg[
        "替换内容引用了不存在的第 2 个捕获组（该正则共有 0 个组；$0 表示整个匹配）"
    ] == 10
    # 标记区起始列 = 15（0-based 14），badflag 位于第 15 列
    assert by_msg[
        "未知标记 'badflag'；可用标记：next、last、break、redirect[=307]、permanent"
    ] == 15


# ---------- 模拟执行 ----------


def test_no_match_preserves_url_200():
    rep = simulate(parse("^/z$ /zz"), "/a")
    assert rep["final_url"] == "/a"
    assert rep["status_code"] == 200
    assert rep["terminated_by"] == "exhausted"
    assert rep["steps"][0]["matched"] is False


def test_redirect_permanent_301():
    rep = simulate(parse("^/old/(.*)$ /new/$1 permanent"), "/old/x")
    assert rep["status_code"] == 301
    assert rep["terminated_by"] == "redirect"
    assert rep["final_url"] == "/new/x"
    assert rep["steps"][0]["groups"]["1"] == "x"


def test_break_internal_rewrite_200():
    rep = simulate(parse("^/u/(\\d+)$ /api?id=$1 break"), "/u/42")
    assert rep["status_code"] == 200
    assert rep["terminated_by"] == "break"
    assert rep["final_url"] == "/api?id=42"


def test_last_restarts_chain():
    rules = parse(
        "^/v\\d+(?=/)/ / last\n"
        "^/users/(\\d+)$ /user?id=$1 break\n"
    )
    rep = simulate(rules, "/v2/users/7")
    assert rep["final_url"] == "/user?id=7"
    assert rep["terminated_by"] == "break"
    assert rep["passes"] == 2
    # 重启前记录了 last 动作
    assert any(s["action"] == "last" for s in rep["steps"])


def test_next_carries_new_url_to_following_rule():
    rules = parse(
        "/a/ /b/\n"
        "/b$ /done break\n"
    )
    rep = simulate(rules, "/a/")
    # 第一条 next 改成 /b/（带斜杠），第二条锚定 /b$ 不命中，链耗尽
    assert rep["final_url"] == "/b/"
    assert rep["terminated_by"] == "exhausted"


def test_loop_detected_between_two_rules():
    rules = parse("^/a$ /b last\n^/b$ /a last\n")
    rep = simulate(rules, "/a")
    assert rep["loop_detected"] is True
    assert rep["terminated_by"] == "loop"
    assert rep["status_code"] == LOOP_STATUS
    assert "循环" in rep["stop_detail"]


def test_self_loop_detected():
    rep = simulate(parse("^/a$ /a last"), "/a")
    assert rep["loop_detected"] is True


def test_step_limit_for_state_changing_chain():
    # 每次 last 都产生新状态（/x -> /xx -> /xxx …），不会重复，由步数上限兜底
    rules = parse("^/x+$ $0x last")
    rep = simulate(rules, "/x", max_steps=5)
    assert rep["terminated_by"] == "step_limit"
    assert rep["status_code"] == LOOP_STATUS
    assert rep["evaluations"] == 5
    assert "上限 5" in rep["stop_detail"]


def test_step_limit_clamped():
    rules = parse("^/a$ /a")
    rep = simulate(rules, "/a", max_steps=10_000)
    # 无动作且只匹配一次后链即耗尽；主要验证上限被钳制不报错
    assert rep["max_steps"] <= 500


def test_unanchored_pattern_matches_path_segment():
    # 非锚定正则在路径任意位置命中；替换串不自带分隔符
    rep = simulate(parse("old new"), "/old/page")
    assert rep["final_url"] == "/new/page"
    # 替换串自带斜杠时，前导分隔符保留，这是 re.sub 的标准语义
    rep2 = simulate(parse("old /new"), "/old/page")
    assert rep2["final_url"] == "//new/page"


def test_unmatched_optional_group_is_none():
    rep = simulate(parse("^/a(\\d+)?$ /v$1 break"), "/a")
    assert rep["steps"][0]["groups"]["1"] is None
    assert rep["final_url"] == "/v"


def test_default_max_steps_constant():
    assert DEFAULT_MAX_STEPS == 50


def test_match_span_recorded():
    rep = simulate(parse("old new"), "old")
    assert rep["steps"][0]["match_span"] == [0, 3]
