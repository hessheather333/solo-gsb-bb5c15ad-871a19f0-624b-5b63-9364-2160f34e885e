"""FastAPI 后端：规则调试 API、示例接口与静态页面。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import (
    DEFAULT_MAX_STEPS,
    MAX_STEPS_HARD_LIMIT,
    RewriteSyntaxError,
    parse_rules,
    simulate,
)
from .examples import EXAMPLES

app = FastAPI(
    title="URL Rewrite Rule Debugger",
    description="逐步模拟顺序执行的 URL 重写规则链，展示匹配、变量与终止状态。",
    version="1.0.0",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


class DebugRequest(BaseModel):
    path: str = Field(..., description="待调试的请求路径，例如 /old/page")
    rules: str = Field("", description="规则文本，每行 <正则> <替换> [标记]")
    max_steps: int = Field(DEFAULT_MAX_STEPS, ge=1, le=MAX_STEPS_HARD_LIMIT)


@app.exception_handler(RewriteSyntaxError)
async def rewrite_syntax_error_handler(_request, exc: RewriteSyntaxError):
    """规则语法错误统一返回 422，并携带结构化的行列定位信息。"""

    return JSONResponse(
        status_code=422,
        content={
            "error": "rule_syntax",
            "message": f"规则文本包含 {len(exc.errors)} 处语法错误",
            "errors": [
                {
                    "line": e.line,
                    "column": e.column,
                    "message": e.message,
                    "snippet": e.snippet,
                }
                for e in exc.errors
            ],
        },
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/examples")
async def list_examples() -> list[dict[str, Any]]:
    return EXAMPLES


@app.post("/api/debug")
async def debug(req: DebugRequest) -> dict[str, Any]:
    path = req.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="请求路径不能为空")
    if not path.startswith("/"):
        path = "/" + path  # 宽容处理：自动补全前导斜杠

    rules = parse_rules(req.rules)  # 语法错误由异常处理器转成 422
    return simulate(rules, path, max_steps=req.max_steps)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


# 静态资源（CSS / JS）
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
