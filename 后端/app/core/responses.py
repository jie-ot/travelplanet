"""Standard response envelope and the project-wide error code set.

Master definition: 《数据结构与通信接口规范》0.5、0.6. Every endpoint must use
this single `{code, message, data}` structure; no endpoint may invent another.
All business-predictable outcomes return HTTP 200 with the business state
carried by `code`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# —— Project-wide error codes (唯一错误码表) ——
CODE_SUCCESS = 0
CODE_AI_FAILED = 1001  # AI 生成失败
CODE_INVALID_PARAM = 1002  # 参数校验失败
CODE_INTERNAL_ERROR = 1003  # 服务内部错误
CODE_NOT_FOUND = 1004  # 资源不存在

MESSAGE_SUCCESS = "success"


class StandardResponse(BaseModel):
    """The unique outward response structure."""

    code: int
    message: str
    data: Any = None


def success(data: Any = None) -> dict[str, Any]:
    """Build a success envelope. `data` may be a dict/list/None."""
    return {"code": CODE_SUCCESS, "message": MESSAGE_SUCCESS, "data": data}


def error(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """Build a failure envelope with a human-readable message."""
    return {"code": code, "message": message, "data": data}
