"""vivo OpenAI-compatible chat client (text / image understanding / JSON).

The client owns the real vivo call (process-level singleton, per-call
`request_id` via `extra_query`, `stream=False`, `reasoning_effort="medium"` +
thinking enabled, read only `message.content`). It always returns a RAW JSON
string; the orchestrator runs it through `output_parser` + Pydantic.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.business_logging import log_event
from app.core.config import settings
from app.core.exceptions import AIGenerationError, InternalError

logger = logging.getLogger("travelplanet")

# vivo Doubao-Seed: depth-thinking knobs for every real chat.completions call.
_VIVO_REASONING_EFFORT = "medium"
_VIVO_THINKING_EXTRA_BODY: dict[str, Any] = {"thinking": {"type": "enabled"}}

# Process-level singleton OpenAI client (built lazily on first real call).
_client = None


def _get_client():  # noqa: ANN202
    """Return the process-wide OpenAI-compatible client (lazy singleton)."""
    if not settings.VIVO_APP_KEY:
        raise InternalError("模型服务未配置 VIVO_APP_KEY")
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            api_key=settings.VIVO_APP_KEY,
            base_url=settings.VIVO_CHAT_BASE_URL,
            max_retries=0,
        )
    return _client

# Structured task identifiers used in model logs and dispatch.
TASK_PHOTO_ANALYZE = "photo_analyze"
TASK_POSTCARD_SELECTION = "postcard_selection"
TASK_POSTCARD_CREATIVE = "postcard_creative"
TASK_REPORT_DRAFT = "report_draft"
TASK_PLANNING = "planning"
TASK_PLANNING_INTAKE = "planning_intake"
TASK_MEMORY_UPDATE = "memory_update"


def _text_timeout_seconds(*, task: str | None = None, override: int | None = None) -> int:
    """Resolve per-call HTTP timeout for vivo chat.completions."""
    if override is not None:
        return override
    if task == TASK_PLANNING:
        return settings.VIVO_PLANNING_TIMEOUT_SECONDS
    return settings.VIVO_TEXT_TIMEOUT_SECONDS


def chat_json(
    *,
    task: str,
    system_prompt: str,
    user_text: str,
    temperature: float = 0.2,
    max_completion_tokens: int = 12000,
    image_data_urls: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> str:
    """Return raw model JSON text for a structured task."""
    return _real_chat_json(
        task=task,
        system_prompt=system_prompt,
        user_text=user_text,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
        image_data_urls=image_data_urls,
        timeout_seconds=timeout_seconds,
    )


@dataclass
class ToolCall:
    """A single model-proposed function call (whitelist tool)."""

    id: str
    name: str
    arguments: str  # raw JSON string from the model


@dataclass
class ChatTurn:
    """One assistant turn: either final content or a set of tool calls."""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


def chat_messages(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    stage: str = "planning",
    temperature: float = 0.2,
    max_completion_tokens: int = 16000,
    timeout_seconds: int | None = None,
    max_attempts: int | None = None,
) -> ChatTurn:
    """Run ONE real model round over a caller-managed `messages` list.

    Used by the planning function-calling loop: the orchestrator owns the
    `messages` history (system → user → assistant/tool turns) and calls this per
    round. When `tools` is provided the model may return tool calls; otherwise
    it returns final content.
    """
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        PermissionDeniedError,
        RateLimitError,
    )

    client = _get_client()
    attempts = max(
        1,
        max_attempts if max_attempts is not None else settings.VIVO_MAX_RETRY + 1,
    )
    last_transient: Exception | None = None
    timeout = _text_timeout_seconds(task=TASK_PLANNING, override=timeout_seconds)

    extra: dict[str, Any] = {}
    if tools:
        extra["tools"] = tools
        extra["tool_choice"] = "auto"
    call_details = _chat_call_details(
        stage=stage,
        messages=messages,
        tools=tools,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
        timeout_seconds=timeout,
        attempts=attempts,
    )

    for attempt in range(attempts):
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        try:
            resp = client.chat.completions.create(
                model=settings.VIVO_CHAT_MODEL,
                messages=messages,
                stream=False,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                reasoning_effort=_VIVO_REASONING_EFFORT,
                extra_body=_VIVO_THINKING_EXTRA_BODY,
                extra_query={"request_id": request_id},
                timeout=timeout,
                **extra,
            )
        except (AuthenticationError, PermissionDeniedError) as exc:
            logger.error("vivo chat auth/permission error (request_id=%s)", request_id)
            log_event(
                "vivo_chat",
                status="failed",
                request_id=request_id,
                attempt=attempt + 1,
                reason="auth_or_permission",
                **call_details,
                **_api_error_details(exc),
            )
            raise InternalError("模型服务鉴权或权限错误") from exc
        except BadRequestError as exc:
            logger.warning("vivo chat bad request (request_id=%s)", request_id)
            log_event(
                "vivo_chat",
                status="failed",
                request_id=request_id,
                attempt=attempt + 1,
                reason="bad_request",
                **call_details,
                **_api_error_details(exc),
            )
            raise AIGenerationError("AI 生成失败：请求被模型拒绝") from exc
        except (APITimeoutError, APIConnectionError, InternalServerError, RateLimitError) as exc:
            last_transient = exc
            logger.warning(
                "vivo chat transient error (request_id=%s, attempt=%d/%d): %s",
                request_id, attempt + 1, attempts, type(exc).__name__,
            )
            log_event(
                "vivo_chat",
                status="retry",
                request_id=request_id,
                attempt=attempt + 1,
                attempts=attempts,
                reason=type(exc).__name__,
                **call_details,
                **_api_error_details(exc),
            )
            _sleep_before_retry(attempt, attempts)
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("vivo chat unexpected error (request_id=%s)", request_id)
            log_event(
                "vivo_chat",
                status="failed",
                request_id=request_id,
                attempt=attempt + 1,
                reason=type(exc).__name__,
                **call_details,
                **_api_error_details(exc),
            )
            raise InternalError("模型服务内部错误") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        message = resp.choices[0].message
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        tool_calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=tc.function.arguments or "{}",
            )
            for tc in raw_tool_calls
            if getattr(tc, "function", None) is not None
        ]
        logger.info(
            "vivo chat ok (fc) request_id=%s latency_ms=%d tool_calls=%d",
            request_id, latency_ms, len(tool_calls),
        )
        log_event(
            "vivo_chat",
            status="success",
            request_id=request_id,
            latency_ms=latency_ms,
            tool_calls=len(tool_calls),
            content_chars=len(message.content or ""),
            content_excerpt=_bounded_text(message.content, 500),
            returned_tool_names=[call.name for call in tool_calls],
            tool_argument_chars=sum(len(call.arguments) for call in tool_calls),
            finish_reason=getattr(resp.choices[0], "finish_reason", None),
            response_id=getattr(resp, "id", None),
            **call_details,
            **_usage_details(resp),
        )
        return ChatTurn(content=message.content, tool_calls=tool_calls)

    logger.error(
        "vivo chat exhausted retries: %s",
        type(last_transient).__name__ if last_transient else "?",
    )
    log_event(
        "vivo_chat",
        status="failed",
        reason="exhausted_retries",
        last_error=type(last_transient).__name__ if last_transient else None,
        **call_details,
        **(_api_error_details(last_transient) if last_transient else {}),
    )
    raise AIGenerationError("AI 生成失败：模型服务暂时不可用，请重试")


def _build_messages(
    system_prompt: str, user_text: str, image_data_urls: list[str] | None
) -> list[dict[str, Any]]:
    """Compose messages: system first, then a single user message.

    When images are present they are attached as `image_url` parts (local
    base64 data URLs only — never a `/static/...` path or any public URL).
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if image_data_urls:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for url in image_data_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user_text})
    return messages


def _real_chat_json(
    *,
    task: str,
    system_prompt: str,
    user_text: str,
    temperature: float,
    max_completion_tokens: int,
    image_data_urls: list[str] | None,
    timeout_seconds: int | None = None,
) -> str:
    """Real vivo OpenAI-compatible chat call with retry + error mapping.

    All structured JSON tasks use depth thinking (`reasoning_effort="medium"` +
    `extra_body={"thinking": {"type": "enabled"}}`) and read ONLY
    `message.content` (never `reasoning_content`). A fresh `request_id` is sent
    per call via `extra_query`.
    """
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        PermissionDeniedError,
        RateLimitError,
    )

    client = _get_client()
    messages = _build_messages(system_prompt, user_text, image_data_urls)
    attempts = max(1, settings.VIVO_MAX_RETRY + 1)
    last_transient: Exception | None = None
    empty_content_retries_left = 2
    attempt = 0
    timeout = _text_timeout_seconds(task=task, override=timeout_seconds)
    call_details = {
        "model": settings.VIVO_CHAT_MODEL,
        "system_prompt_chars": len(system_prompt),
        "user_text_chars": len(user_text),
        "image_count": len(image_data_urls or []),
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "timeout_seconds": timeout,
        "max_attempts": attempts,
        "reasoning_effort": _VIVO_REASONING_EFFORT,
    }

    while attempt < attempts:
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        try:
            resp = client.chat.completions.create(
                model=settings.VIVO_CHAT_MODEL,
                messages=messages,
                stream=False,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                reasoning_effort=_VIVO_REASONING_EFFORT,
                extra_body=_VIVO_THINKING_EXTRA_BODY,
                extra_query={"request_id": request_id},
                timeout=timeout,
            )
        except (AuthenticationError, PermissionDeniedError) as exc:
            # AppKey/permission/config → 1003, never retry.
            logger.error("vivo chat auth/permission error (request_id=%s)", request_id)
            log_event(
                "vivo_chat_json",
                status="failed",
                task=task,
                request_id=request_id,
                attempt=attempt + 1,
                reason="auth_or_permission",
                **call_details,
                **_api_error_details(exc),
            )
            raise InternalError("模型服务鉴权或权限错误") from exc
        except BadRequestError as exc:
            # Content moderation / bad request → 1001, never retry.
            logger.warning("vivo chat bad request (request_id=%s)", request_id)
            log_event(
                "vivo_chat_json",
                status="failed",
                task=task,
                request_id=request_id,
                attempt=attempt + 1,
                reason="bad_request",
                **call_details,
                **_api_error_details(exc),
            )
            raise AIGenerationError("AI 生成失败：请求被模型拒绝") from exc
        except (APITimeoutError, APIConnectionError, InternalServerError, RateLimitError) as exc:
            # Transient: retry up to VIVO_MAX_RETRY.
            last_transient = exc
            logger.warning(
                "vivo chat transient error (request_id=%s, attempt=%d/%d): %s",
                request_id, attempt + 1, attempts, type(exc).__name__,
            )
            log_event(
                "vivo_chat_json",
                status="retry",
                task=task,
                request_id=request_id,
                attempt=attempt + 1,
                attempts=attempts,
                reason=type(exc).__name__,
                **call_details,
                **_api_error_details(exc),
            )
            attempt += 1
            _sleep_before_retry(attempt - 1, attempts)
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("vivo chat unexpected error (request_id=%s)", request_id)
            log_event(
                "vivo_chat_json",
                status="failed",
                task=task,
                request_id=request_id,
                attempt=attempt + 1,
                reason=type(exc).__name__,
                **call_details,
                **_api_error_details(exc),
            )
            raise InternalError("模型服务内部错误") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        content = _extract_content(resp)
        logger.info(
            "vivo chat ok task=%s request_id=%s latency_ms=%d",
            task, request_id, latency_ms,
        )
        log_event(
            "vivo_chat_json",
            status="success",
            task=task,
            request_id=request_id,
            latency_ms=latency_ms,
            content_chars=len(content or ""),
            content_excerpt=_bounded_text(content, 500),
            finish_reason=(
                getattr(resp.choices[0], "finish_reason", None)
                if getattr(resp, "choices", None)
                else None
            ),
            response_id=getattr(resp, "id", None),
            **call_details,
            **_usage_details(resp),
        )
        if not content or not content.strip():
            log_event(
                "vivo_chat_json",
                status="failed",
                task=task,
                request_id=request_id,
                latency_ms=latency_ms,
                reason="empty_content",
                **call_details,
                **_usage_details(resp),
            )
            if empty_content_retries_left > 0:
                empty_content_retries_left -= 1
                log_event(
                    "vivo_chat_json",
                    status="retry",
                    task=task,
                    request_id=request_id,
                    reason="empty_content",
                    retries_left=empty_content_retries_left,
                    **call_details,
                )
                _sleep_before_retry(0, 2)
                continue
            raise AIGenerationError("AI 生成失败：模型返回空内容")
        return content

    # Exhausted retries on transient errors.
    logger.error("vivo chat exhausted retries: %s", type(last_transient).__name__ if last_transient else "?")
    log_event(
        "vivo_chat_json",
        status="failed",
        task=task,
        reason="exhausted_retries",
        last_error=type(last_transient).__name__ if last_transient else None,
        **call_details,
        **(_api_error_details(last_transient) if last_transient else {}),
    )
    raise AIGenerationError("AI 生成失败：模型服务暂时不可用，请重试")


def _chat_call_details(
    *,
    stage: str,
    messages: list[dict[str, Any]],
    tools: list[dict] | None,
    temperature: float,
    max_completion_tokens: int,
    timeout_seconds: int,
    attempts: int,
) -> dict[str, Any]:
    role_counts: dict[str, int] = {}
    content_chars = 0
    for message in messages:
        role = str(message.get("role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        content = message.get("content")
        if isinstance(content, str):
            content_chars += len(content)
        elif content is not None:
            content_chars += len(str(content))
    tool_names = []
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and function.get("name"):
            tool_names.append(str(function["name"]))
    return {
        "stage": stage,
        "model": settings.VIVO_CHAT_MODEL,
        "message_count": len(messages),
        "message_role_counts": role_counts,
        "message_content_chars": content_chars,
        "tool_schema_count": len(tools or []),
        "tool_schema_names": tool_names,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "timeout_seconds": timeout_seconds,
        "max_attempts": attempts,
        "reasoning_effort": _VIVO_REASONING_EFFORT,
    }


def _usage_details(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _api_error_details(exc: Exception) -> dict[str, Any]:
    response = getattr(exc, "response", None)
    return {
        "error_type": type(exc).__name__,
        "error_message": _bounded_text(str(exc), 2000),
        "http_status": getattr(exc, "status_code", None)
        or getattr(response, "status_code", None),
        "error_code": getattr(exc, "code", None),
        "error_request_id": getattr(exc, "request_id", None),
    }


def _bounded_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _sleep_before_retry(attempt: int, attempts: int) -> None:
    if attempt + 1 >= attempts:
        return
    delay = max(0.0, settings.VIVO_RETRY_BACKOFF_SECONDS) * (attempt + 1)
    if delay:
        time.sleep(delay)


def _extract_content(resp: Any) -> str:
    """Read only `choices[0].message.content`; never `reasoning_content`."""
    try:
        return resp.choices[0].message.content or ""
    except (AttributeError, IndexError):
        return ""
