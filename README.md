# URL 重写规则调试台

基于 **FastAPI + 原生 JavaScript**（零前端构建、零 npm 依赖）的 URL Rewrite 规则调试工具。
用户用简单的行语法定义**按顺序执行**的路径匹配、捕获组替换、重定向 / 内部改写规则，
输入一条请求路径后，服务端逐步模拟整条规则链，展示：

- 每一条规则的**命中 / 未命中**、命中的文本片段与位置 `[start, end)`；
- 每一步的**捕获组变量**（`$1`、命名组 `${name}`，含未参与匹配的 `null`）；
- 每一步的**输入 / 输出 URL、URL 是否变化、执行动作**；
- 最终 URL、响应状态码（200 / 301–308 / 508）与终止原因；
- **循环检测**：`last` 重启规则链后 URL 回到历史状态即停止；同时受**步数上限**保护；
- 规则**语法错误精确定位**（行号 + 列号，前端点击错误可跳到对应行）；
- 5 个**一键载入示例**；
- 一键**复制文本调试报告 / 复制 JSON**；
- `Ctrl + Enter` 快捷执行。

## 端口与单命令启动

服务监听容器内 **8000** 端口，映射到宿主机 **http://localhost:8000**。

### 方式一：Docker Compose（推荐，单命令）

```bash
docker compose up --build
```

启动后浏览器打开 **http://localhost:8000** 即可使用。
后台运行加 `-d`，停止用 `docker compose down`。

### 方式二：Docker

```bash
docker build -t url-rewrite-debugger .
docker run --rm -p 8000:8000 url-rewrite-debugger
```

### 方式三：本地 Python（3.11+）

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 规则语法

每行一条规则，字段之间用空白分隔（正则中需要空白时请写 `\s`）：

```
<正则表达式> <替换内容> [标记]
```

- 空行与以 `#` 开头的行是注释。
- 正则为 **Python `re` 语法**，默认**非锚定**（在路径任意位置命中，等同 `re.search`）；
  需要完整匹配请显式书写 `^` 与 `$`。
- 每条规则只替换**第一处**匹配（`re.sub(..., count=1)`）。

### 捕获组引用

| 写法 | 含义 |
| --- | --- |
| `$0` | 整个匹配文本 |
| `$1`、`${1}`、`\1`、`\g<1>` | 第 1 个数字捕获组 |
| `$name`、`${name}`、`\g<name>` | 命名捕获组 |

未参与匹配的可选组在变量面板中显示为 `None`，替换时按空串处理（Python 语义）。
孤立的 `$` 按字面量输出；引用不存在的组会在解析期报错并定位到列。

### 动作标记（大小写不敏感，逗号或空白分隔）

| 标记 | 行为 |
| --- | --- |
| `next`（**缺省**） | 替换成功后携带新 URL 继续执行**下一条**规则 |
| `last` | 替换成功后**回到第一条规则**重新匹配（开始新一轮） |
| `break` | 终止规则链，结果视为**内部改写**，返回 **HTTP 200** |
| `redirect` | 终止规则链，返回 **302** 重定向（`Location` 为新 URL） |
| `redirect=307` | 指定状态码，允许范围 **300–308**（也可用 `:` 或缩写 `r=303`） |
| `permanent` | `redirect=301` 的别名 |

一条规则只能指定一个动作标记，冲突（如 `break,last`）会报语法错误。

### 循环与步数上限

- 每一轮规则链开始时记录当前 URL；`last` 重启后若 URL 回到任何先前轮次出现过的状态，
  立即判定为循环，返回 **HTTP 508 Loop Detected**。
- 规则评估总次数（每评估一条规则计一步）还受请求参数 `max_steps` 约束，
  默认 **50**，前端可修改，服务端硬上限 **500**；超限同样以 508 停止，
  用于捕获「URL 一直变化但不重复」的长循环。

## 示例

```
# 去掉尾斜杠，然后 301 到归档目录
^(.+?)/+$  $1
^/old/(.*)$  /archive/$1  permanent
```

请求 `/old/page/` → 第一步改写为 `/old/page` → 第二步 **301** 到 `/archive/page`。

下拉框中还内置了：内部改写链（`last` 重启 + `break`）、A↔B 循环检测、
命名捕获组重排、语法错误定位，共 5 个示例。

## HTTP API

交互式文档：启动后访问 **http://localhost:8000/docs**（Swagger）。

### `POST /api/debug`

请求：

```json
{
  "path": "/v2/users/42",
  "rules": "^/v\\d+(?=/)/ / last\n^/users/(\\d+)$ /api/user?id=$1 break\n",
  "max_steps": 50
}
```

成功响应（节选）：

```json
{
  "initial_url": "/v2/users/42",
  "final_url": "/api/user?id=42",
  "status_code": 200,
  "terminated_by": "break",
  "loop_detected": false,
  "evaluations": 3,
  "passes": 2,
  "steps": [
    {
      "pass": 1, "rule_line": 1,
      "pattern": "^/v\\d+(?=/)/", "replacement": "/", "flags": "last",
      "input_url": "/v2/users/42", "matched": true,
      "match_span": [0, 4], "matched_text": "/v2/",
      "groups": {}, "output_url": "/users/42", "changed": true,
      "action": "last", "note": "替换成功，携带新 URL 从第一条规则重新开始"
    }
  ]
}
```

`terminated_by` 取值：`exhausted`（链走完）、`break`、`redirect`、
`loop`（重复 URL）、`step_limit`（超限）、`error`。

规则存在语法错误时返回 **422**：

```json
{
  "error": "rule_syntax",
  "message": "规则文本包含 2 处语法错误",
  "errors": [
    {"line": 1, "column": 8, "message": "正则表达式语法错误：missing ), unterminated subpattern", "snippet": "..."},
    {"line": 2, "column": 27, "message": "替换内容引用了不存在的第 2 个捕获组（该正则共有 1 个组）", "snippet": "..."}
  ]
}
```

其它端点：`GET /api/examples`（示例列表）、`GET /api/health`（健康检查）。

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI 应用：/api/debug、/api/examples、静态页
│   ├── engine.py        # 规则解析器 + 逐步模拟引擎（纯标准库，可独立测试）
│   ├── examples.py      # 内置示例
│   └── static/
│       ├── index.html   # 单页界面
│       ├── style.css
│       └── app.js       # 原生 JS：编辑器、错误定位、时间线、复制
├── tests/               # pytest：引擎单测 + API 冒烟测试
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 测试

```bash
pip install -r requirements.txt pytest httpx
python -m pytest -q
```

覆盖：替换模板转换、捕获组校验、标记冲突、行列错误定位、`next/last/break/redirect`
语义、循环检测、步数上限、422 错误结构与页面 / 静态资源可访问性（共 33 个用例）。
