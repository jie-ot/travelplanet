"""Business exception types mapped to the project error code set.

These let service-layer code raise typed errors that the API layer (or the
global handlers in `app.main`) converts into the standard `{code, message,
data}` envelope. Error code semantics: 《数据结构与通信接口规范》0.6.
"""

from __future__ import annotations

from app.core.responses import (
    CODE_AI_FAILED,
    CODE_INTERNAL_ERROR,
    CODE_INVALID_PARAM,
    CODE_NOT_FOUND,
)


class BusinessError(Exception):
    """Base business error carrying a project error code and message."""

    code: int = CODE_INTERNAL_ERROR

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class InvalidParamError(BusinessError):
    """Parameter / path / ownership validation failure → 1002."""

    code = CODE_INVALID_PARAM


class AIGenerationError(BusinessError):
    """AI generation / content / JSON validation failure → 1001."""

    code = CODE_AI_FAILED


class ImageInputPolicyError(AIGenerationError):
    """Image generation input was explicitly rejected by provider policy."""


class InternalError(BusinessError):
    """Service config / model permission / download / disk failure → 1003."""

    code = CODE_INTERNAL_ERROR


class NotFoundError(BusinessError):
    """Business main record not found → 1004."""

    code = CODE_NOT_FOUND
