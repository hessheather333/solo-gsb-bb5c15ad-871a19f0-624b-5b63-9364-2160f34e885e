"""URL 重写规则引擎（纯标准库实现，可独立于 FastAPI 测试）。

规则行语法
==========

    <正则表达式> <替换内容> [标记]

- 空行与以 ``#`` 开头的行被视为注释。
- 一条规则占一行，字段之间用空白分隔；正则内部需要空白时请使用 ``\\s``。
- 正则为 Python :mod:`re` 语法，默认**非锚定**（需要整串匹配请显式写 ``^`` / ``$``）。
- 替换内容支持捕获组引用：

    * ``$1`` / ``${1}`` / ``\\1`` / ``\\g<1>`` —— 数字捕获组，``$0`` 表示整个匹配；
    * ``$name`` / ``${name}`` / ``\\g<name>`` —— 命名捕获组。

- 标记（逗号或空白分隔，大小写不敏感）：

    * ``next``（默认） 替换后继续执行下一条规则；
    * ``last``         替换后从第一条规则重新开始（用新 URL）；
    * ``break``        终止规则链，结果视为内部改写（HTTP 200）；
    * ``redirect``     302 临时重定向；``redirect=307`` 可指定 300–308 状态码；
    * ``permanent``    301 永久重定向。

循环检测：在每轮规则链开始时记录当前 URL；``last`` 重启后若 URL 回到任何
先前出现过的状态，立即判定为循环（HTTP 508）。规则评估总次数还受
``max_steps`` 上限保护，超限同样以 508 停止。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---- 常量 ----------------------------------------------------------------

MAX_STEPS_HARD_LIMIT = 500
DEFAULT_MAX_STEPS = 50
LOOP_STATUS = 508  # RFC 5842: Loop Detected

_NEXT = "next"
_LAST = "last"
_BREAK = "break"
_REDIRECT = "redirect"

_CHAIN_ACTIONS = {_NEXT, _LAST, _BREAK}
_STATUS_TEXT = {
    200: "OK",
    301: "MOVED PERMANENTLY",
    302: "FOUND",
    303: "SEE OTHER",
    307: "TEMPORARY REDIRECT",
    308: "PERMANENT REDIRECT",
}

_ACTION_NOTES = {
    _NEXT: "替换成功，继续执行下一条规则",
    _LAST: "替换成功，携带新 URL 从第一条规则重新开始",
    _BREAK: "break：终止规则链，按内部改写处理",
    _REDIRECT: "重定向：终止规则链并向客户端返回跳转响应",
}


# ---- 错误模型 -------------------------------------------------------------


@dataclass
class RuleError:
    """一条规则语法错误，行列号均为 1-based，便于前端定位。"""

    line: int
    column: int
    message: str
    snippet: str = ""


class RewriteSyntaxError(Exception):
    """规则文本包含一处或多处语法错误。"""

    def __init__(self, errors: list[RuleError]):
        self.errors = errors
        super().__init__(f"{len(errors)} rule syntax error(s)")


# ---- 规则模型 -------------------------------------------------------------


@dataclass
class Rule:
    line_no: int
    pattern_src: str
    replacement_src: str
    flags_src: str
    pattern: re.Pattern[str]
    replacement: str  # 已转换为 Python re.sub 模板
    action: str = _NEXT  # next | last | break | redirect
    status: int | None = None


# ---- 替换模板处理 ----------------------------------------------------------


def _scan_replacement(raw: str) -> tuple[str, list[dict[str, Any]]]:
    """扫描替换模板。

    把 ``$1`` / ``${x}`` / ``$name`` 统一转换成 Python 原生的 ``\\g<...>``
    形式（``\\1`` / ``\\g<name>`` 原样保留），同时收集所有捕获组引用，
    供编译期校验。返回 ``(转换后的模板, 引用列表)``。

    引用项：``{"start": int, "end": int, "value": str, "numeric": bool}``。
    """

    out: list[str] = []
    refs: list[dict[str, Any]] = []
    i, n = 0, len(raw)

    while i < n:
        c = raw[i]

        # \1、\g<name>、\\、\$ 等反斜杠转义
        if c == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "g" and i + 2 < n and raw[i + 2] == "<":
                end = raw.find(">", i + 3)
                if end != -1:
                    content = raw[i + 3 : end]
                    refs.append(
                        {
                            "start": i,
                            "end": end + 1,
                            "value": content,
                            "numeric": content.isdigit(),
                        }
                    )
                    out.append(raw[i : end + 1])  # Python 原生支持 \g<...>
                    i = end + 1
                    continue
            if nxt.isdigit():
                j = i + 1
                while j < n and raw[j].isdigit():
                    j += 1
                digits = raw[i + 1 : j]
                refs.append(
                    {"start": i, "end": j, "value": digits, "numeric": True}
                )
                out.append(f"\\g<{digits}>")  # 避免 \10 被当成八进制
                i = j
                continue
            out.append(raw[i : i + 2])  # 其它转义对原样保留
            i += 2
            continue

        # $1、${1}、$name、${name}
        if c == "$":
            if i + 1 < n and raw[i + 1] == "{":
                end = raw.find("}", i + 2)
                if end != -1:
                    content = raw[i + 2 : end]
                    refs.append(
                        {
                            "start": i,
                            "end": end + 1,
                            "value": content,
                            "numeric": content.isdigit(),
                        }
                    )
                    out.append(f"\\g<{content}>")
                    i = end + 1
                    continue
            if i + 1 < n and (raw[i + 1].isdigit() or raw[i + 1].isalpha() or raw[i + 1] == "_"):
                j = i + 1
                if raw[j].isdigit():
                    while j < n and raw[j].isdigit():
                        j += 1
                else:
                    while j < n and (raw[j].isalnum() or raw[j] == "_"):
                        j += 1
                value = raw[i + 1 : j]
                refs.append(
                    {"start": i, "end": j, "value": value, "numeric": value.isdigit()}
                )
                out.append(f"\\g<{value}>")
                i = j
                continue
            out.append("$")  # 孤立的 $ 按字面量处理
            i += 1
            continue

        out.append(c)
        i += 1

    return "".join(out), refs


# ---- 标记解析 -------------------------------------------------------------


def _parse_flags(
    flags_src: str, base_offset: int
) -> tuple[list[RuleError], str, int | None]:
    """解析规则标记。

    ``base_offset`` 为标记区在规则行内的 0-based 起始偏移；
    返回的错误列号为 1-based。
    """

    errors: list[RuleError] = []
    chosen: list[tuple[str, int | None]] = []

    for tok in re.finditer(r"[^\s,]+", flags_src):
        token = tok.group(0)
        column = base_offset + tok.start() + 1
        low = token.lower()

        if low in _CHAIN_ACTIONS:
            chosen.append((low, None))
        elif low == "permanent":
            chosen.append((_REDIRECT, 301))
        elif low == "redirect":
            chosen.append((_REDIRECT, 302))
        else:
            m = re.fullmatch(r"(?:redirect|r)[:=](\d{3})", low)
            if m:
                code = int(m.group(1))
                if 300 <= code <= 308:
                    chosen.append((_REDIRECT, code))
                else:
                    errors.append(
                        RuleError(
                            0,
                            column,
                            f"重定向状态码 {code} 不在允许范围 300–308 内",
                        )
                    )
            else:
                errors.append(
                    RuleError(
                        0,
                        column,
                        f"未知标记 {token!r}；可用标记：next、last、break、"
                        "redirect[=307]、permanent",
                    )
                )

    if len(chosen) > 1:
        names = [("redirect" if a == _REDIRECT else a) for a, _ in chosen]
        errors.append(
            RuleError(
                0,
                base_offset + 1,
                f"动作标记互相冲突：{'、'.join(names)}，一条规则只能有一种动作",
            )
        )

    if errors:
        return errors, _NEXT, None
    if not chosen:
        return [], _NEXT, None
    action, status = chosen[0]
    return [], action, status


# ---- 规则解析 -------------------------------------------------------------


def parse_rules(text: str) -> list[Rule]:
    """解析整段规则文本，收集全部行的错误后一次性抛出。"""

    rules: list[Rule] = []
    errors: list[RuleError] = []

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # 用带位置的分词，保证多空格 / Tab 下错误列号仍然精确
        tokens = list(re.finditer(r"\S+", line))
        if len(tokens) < 2:
            errors.append(
                RuleError(
                    line_no,
                    1,
                    "规则格式应为：<正则表达式> <替换内容> [标记]，至少需要两个字段",
                    line,
                )
            )
            continue

        pat_tok, repl_tok = tokens[0], tokens[1]
        pattern_src = pat_tok.group(0)
        replacement_src = repl_tok.group(0)
        if len(tokens) >= 3:
            flags_start = tokens[2].start()
            flags_src = line[flags_start:]
        else:
            flags_start = len(line)
            flags_src = ""

        # 1) 编译正则
        try:
            pattern = re.compile(pattern_src)
        except re.error as exc:
            pos = getattr(exc, "pos", None)
            column = pat_tok.start() + pos + 1 if isinstance(pos, int) else pat_tok.start() + 1
            errors.append(
                RuleError(
                    line_no,
                    column,
                    f"正则表达式语法错误：{exc.msg}",
                    line,
                )
            )
            continue

        # 2) 转换并校验替换模板中的捕获组引用
        replacement, refs = _scan_replacement(replacement_src)
        repl_column = repl_tok.start()  # 1-based：0-based 起始偏移 + 1
        bad_reference = False
        for ref in refs:
            value = ref["value"]
            column = repl_column + ref["start"] + 1
            if ref["numeric"]:
                num = int(value) if value else -1
                if not 0 <= num <= pattern.groups:
                    errors.append(
                        RuleError(
                            line_no,
                            column,
                            f"替换内容引用了不存在的第 {num} 个捕获组"
                            f"（该正则共有 {pattern.groups} 个组；$0 表示整个匹配）",
                            line,
                        )
                    )
                    bad_reference = True
            else:
                if value not in pattern.groupindex:
                    errors.append(
                        RuleError(
                            line_no,
                            column,
                            f"替换内容引用了不存在的命名捕获组 {value!r}",
                            line,
                        )
                    )
                    bad_reference = True

        # 3) 解析标记（base_column 为标记区的 0-based 偏移，解析器内部 +1 转列号）
        flag_errors, action, status = _parse_flags(flags_src, flags_start)
        for err in flag_errors:
            errors.append(RuleError(line_no, err.column, err.message, line))

        if bad_reference or flag_errors:
            continue

        rules.append(
            Rule(
                line_no=line_no,
                pattern_src=pattern_src,
                replacement_src=replacement_src,
                flags_src=flags_src,
                pattern=pattern,
                replacement=replacement,
                action=action,
                status=status,
            )
        )

    if errors:
        raise RewriteSyntaxError(errors)
    return rules


# ---- 逐步模拟 -------------------------------------------------------------


def _groups_of(match: re.Match[str]) -> dict[str, str | None]:
    groups: dict[str, str | None] = {
        str(i): match.group(i) for i in range(1, match.re.groups + 1)
    }
    for name, idx in match.re.groupindex.items():
        groups[name] = match.group(idx)
    return groups


def _error_step(rule: Rule, pass_no: int, url: str, message: str) -> dict[str, Any]:
    return {
        "pass": pass_no,
        "rule_line": rule.line_no,
        "pattern": rule.pattern_src,
        "replacement": rule.replacement_src,
        "flags": rule.flags_src,
        "input_url": url,
        "matched": False,
        "match_span": None,
        "matched_text": None,
        "groups": {},
        "output_url": url,
        "changed": False,
        "action": None,
        "note": message,
    }


def simulate(
    rules: list[Rule], initial_url: str, max_steps: int = DEFAULT_MAX_STEPS
) -> dict[str, Any]:
    """按顺序逐步执行规则链，返回包含每一步快照的调试报告。"""

    max_steps = max(1, min(int(max_steps), MAX_STEPS_HARD_LIMIT))

    steps: list[dict[str, Any]] = []
    seen: set[str] = {initial_url}  # 每轮链开始处的 URL
    url = initial_url
    rule_index = 0
    pass_no = 1

    terminated_by = "exhausted"
    status_code = 200
    detail = "规则链已全部执行完毕，未发生重定向（按内部改写返回 200）"

    while len(steps) < max_steps:
        if rule_index >= len(rules):
            break  # 一轮走完，没有 last/break/redirect 拦截

        rule = rules[rule_index]
        step: dict[str, Any] = {
            "pass": pass_no,
            "rule_line": rule.line_no,
            "pattern": rule.pattern_src,
            "replacement": rule.replacement_src,
            "flags": rule.flags_src,
            "input_url": url,
            "matched": False,
            "match_span": None,
            "matched_text": None,
            "groups": {},
            "output_url": url,
            "changed": False,
            "action": None,
            "note": "当前 URL 未匹配该规则，跳过",
        }

        match = rule.pattern.search(url)
        if match is None:
            steps.append(step)
            rule_index += 1
            continue

        step["matched"] = True
        step["match_span"] = list(match.span(0))
        step["matched_text"] = match.group(0)
        step["groups"] = _groups_of(match)

        try:
            new_url = rule.pattern.sub(rule.replacement, url, count=1)
        except re.error as exc:  # 理论上解析期已拦截，这里兜底
            step["note"] = f"执行替换时出错：{exc.msg}"
            steps.append(step)
            terminated_by = "error"
            status_code = 500
            detail = f"第 {rule.line_no} 行规则替换失败：{exc.msg}"
            break

        step["output_url"] = new_url
        step["changed"] = new_url != url
        step["action"] = rule.action

        if rule.action == _REDIRECT:
            status_code = rule.status or 302
            terminated_by = "redirect"
            detail = (
                f"第 {rule.line_no} 行规则触发 {status_code} "
                f"{_STATUS_TEXT.get(status_code, 'REDIRECT')}，Location: {new_url}"
            )
            step["note"] = _ACTION_NOTES[_REDIRECT]
            steps.append(step)
            url = new_url
            break

        if rule.action == _BREAK:
            terminated_by = "break"
            status_code = 200
            detail = f"第 {rule.line_no} 行规则标记 break，规则链终止，内部改写为 {new_url}"
            step["note"] = _ACTION_NOTES[_BREAK]
            steps.append(step)
            url = new_url
            break

        if rule.action == _LAST:
            step["note"] = _ACTION_NOTES[_LAST]
            steps.append(step)
            url = new_url
            rule_index = 0
            if url in seen:
                terminated_by = "loop"
                status_code = LOOP_STATUS
                detail = (
                    f"第 {pass_no + 1} 轮开始时 URL {url!r} 回到先前出现过的状态，"
                    "判定为重写循环并停止"
                )
                break
            seen.add(url)
            pass_no += 1
            continue

        # next（默认）：携带新 URL 继续下一条
        step["note"] = _ACTION_NOTES[_NEXT]
        steps.append(step)
        url = new_url
        rule_index += 1

    else:
        terminated_by = "step_limit"
        status_code = LOOP_STATUS
        detail = f"规则评估已达步数上限 {max_steps}，强制停止（可能存在不重复状态的长循环）"

    if terminated_by == "exhausted" and url == initial_url:
        detail = "规则链已全部执行完毕，没有任何规则命中，URL 保持不变（200）"

    return {
        "initial_url": initial_url,
        "final_url": url,
        "status_code": status_code,
        "status_text": _STATUS_TEXT.get(status_code, ""),
        "terminated_by": terminated_by,
        "stop_detail": detail,
        "loop_detected": terminated_by == "loop",
        "evaluations": len(steps),
        "passes": pass_no,
        "max_steps": max_steps,
        "steps": steps,
    }
