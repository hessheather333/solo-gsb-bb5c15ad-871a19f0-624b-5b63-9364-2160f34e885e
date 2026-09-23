"""内置示例：下拉框一键载入。"""

from __future__ import annotations

EXAMPLES: list[dict[str, object]] = [
    {
        "id": "redirect-https",
        "name": "1. 强制 HTTPS + 去尾斜杠（301）",
        "description": "老链接 /old/page/ 会被去掉尾斜杠后 301 跳转到新地址。",
        "path": "/old/page/",
        "max_steps": 50,
        "rules": (
            "# 去掉末尾的斜杠（保留根路径 /），替换后继续向下执行\n"
            "^(.+?)/+$  $1\n"
            "\n"
            "# /old/xxx 永久重定向到 /archive/xxx\n"
            "^/old/(.*)$  /archive/$1  permanent\n"
        ),
    },
    {
        "id": "internal-chain",
        "name": "2. 内部改写链（last 重启，最终 break）",
        "description": "模拟网关多轮改写：先归一化版本前缀，再映射到后端内部路由。",
        "path": "/v2/users/42",
        "max_steps": 50,
        "rules": (
            "# 去掉版本前缀，last 携带新 URL 从第一条规则重新开始\n"
            "^/v\\d+(?=/)/  /  last\n"
            "\n"
            "# 把 /users/<id> 映射为内部 /api/user?id=<id>，break 结束\n"
            "^/users/(\\d+)$  /api/user?id=$1  break\n"
        ),
    },
    {
        "id": "loop-detected",
        "name": "3. 循环检测（A↔B 无限改写）",
        "description": "两条 last 规则互相改写，第二轮开始时引擎识别重复 URL 并以 508 停止。",
        "path": "/a",
        "max_steps": 50,
        "rules": (
            "^/a$  /b  last\n"
            "^/b$  /a  last\n"
        ),
    },
    {
        "id": "named-groups",
        "name": "4. 命名捕获组与参数重排",
        "description": "把 /lang/en/post/123 重写为 /index?lang=en&id=123（内部 200）。",
        "path": "/lang/en/post/123",
        "max_steps": 50,
        "rules": (
            "^/lang/(?P<lang>[a-z]{2})/post/(?P<id>\\d+)$"
            "  /index?lang=${lang}&id=$id  break\n"
        ),
    },
    {
        "id": "syntax-errors",
        "name": "5. 语法错误定位（点击错误跳转）",
        "description": "故意包含未闭合字符类、不存在的捕获组和未知标记，观察逐行报错。",
        "path": "/blog/x",
        "max_steps": 50,
        "rules": (
            "^/blog/([a-z]+$  /news/$1  redirect=404\n"
            "^/news/([a-z]+)$  /article/$2  pernament\n"
        ),
    },
]


def get_example(example_id: str) -> dict[str, object] | None:
    return next((e for e in EXAMPLES if e["id"] == example_id), None)
