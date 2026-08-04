"""OpenAI-compatible chat client for vivo tasks and selectable planning models.

The client owns the real vivo call (process-level singleton, per-call
`request_id` via `extra_query`). Planning may instead select DeepSeek's OpenAI
Chat Completions endpoint. Provider-specific parameters stay in this adapter.
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
from app.ai.model_selection import DEFAULT_PLANNING_MODEL, PlanningModel

logger = logging.getLogger("travelplanet")

# vivo Doubao-Seed: depth-thinking knobs for current non-planning tasks and the
# default planning model. DeepSeek is always explicit high-effort thinking.
_VIVO_REASONING_EFFORT = "medium"
_VIVO_THINKING_EXTRA_BODY: dict[str, Any] = {"thinking": {"type": "enabled"}}
_DEEPSEEK_REASONING_EFFORT = "high"
_DEEPSEEK_THINKING_EXTRA_BODY: dict[str, Any] = {
    "thinking": {"type": "enabled"}
}


@dataclass(frozen=True)
class ChatRuntime:
    """Resolved provider settings for one selected planning model."""

    planning_model: PlanningModel
    provider: str
    api_model: str
    api_key: str
    base_url: str
    reasoning_effort: str
    extra_body: dict[str, Any]
    uses_temperature: bool
    token_parameter: str
    uses_vivo_request_query: bool


def _resolve_runtime(planning_model: PlanningModel | None = None) -> ChatRuntime:
    selected = planning_model or DEFAULT_PLANNING_MODEL
    if selected == "doubao-seed-2.0-pro":
        return ChatRuntime(
            planning_model=selected,
            provider="vivo",
            api_model=settings.VIVO_CHAT_MODEL,
            api_key=settings.VIVO_APP_KEY,
            base_url=settings.VIVO_CHAT_BASE_URL,
            reasoning_effort=_VIVO_REASONING_EFFORT,
            extra_body=_VIVO_THINKING_EXTRA_BODY,
            uses_temperature=True,
            token_parameter="max_completion_tokens",
            uses_vivo_request_query=True,
        )
    api_model = (
        settings.DEEPSEEK_FLASH_MODEL
        if selected == "deepseek-v4-flash"
        else settings.DEEPSEEK_PRO_MODEL
    )
    return ChatRuntime(
        planning_model=selected,
        provider="deepseek",
        api_model=api_model,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_CHAT_BASE_URL,
        reasoning_effort=_DEEPSEEK_REASONING_EFFORT,
        extra_body=_DEEPSEEK_THINKING_EXTRA_BODY,
        # DeepSeek documents temperature as unsupported in thinking mode.
        uses_temperature=False,
        token_parameter="max_tokens",
        uses_vivo_request_query=False,
    )


# Process-level OpenAI clients, one per effective provider configuration.
_clients: dict[tuple[str, str, str], Any] = {}


def _get_client(runtime: ChatRuntime):  # noqa: ANN202
    """Return a process-wide OpenAI-compatible client for the runtime."""
    if not runtime.api_key:
        key_name = "DEEPSEEK_API_KEY" if runtime.provider == "deepseek" else "VIVO_APP_KEY"
        raise InternalError(f"模型服务未配置 {key_name}")
    cache_key = (runtime.provider, runtime.base_url, runtime.api_key)
    if cache_key not in _clients:
        from openai import OpenAI

        _clients[cache_key] = OpenAI(
            api_key=runtime.api_key,
            base_url=runtime.base_url,
            max_retries=0,
        )
    return _clients[cache_key]

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
    planning_model: PlanningModel | None = None,
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
        planning_model=planning_model,
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
    # Never persisted or returned to the frontend. DeepSeek tool conversations
    # must replay this field verbatim in subsequent provider requests.
    reasoning_content: str | None = None


def chat_messages(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    stage: str = "planning",
    temperature: float = 0.2,
    max_completion_tokens: int = 16000,
    timeout_seconds: int | None = None,
    max_attempts: int | None = None,
    planning_model: PlanningModel = DEFAULT_PLANNING_MODEL,
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

    runtime = _resolve_runtime(planning_model)
    client = _get_client(runtime)
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
        runtime=runtime,
    )

    for attempt in range(attempts):
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        try:
            request_kwargs = _completion_request_kwargs(
                runtime=runtime,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                timeout=timeout,
                request_id=request_id,
                extra=extra,
            )
            resp = client.chat.completions.create(**request_kwargs)
        except (AuthenticationError, PermissionDeniedError) as exc:
            logger.error("model chat auth/permission error (request_id=%s)", request_id)
            log_event(
                "model_chat",
                status="failed",
                request_id=request_id,
                attempt=attempt + 1,
                reason="auth_or_permission",
                **call_details,
                **_api_error_details(exc),
            )
            raise InternalError("模型服务鉴权或权限错误") from exc
        except BadRequestError as exc:
            logger.warning("model chat bad request (request_id=%s)", request_id)
            log_event(
                "model_chat",
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
                "model chat transient error (request_id=%s, attempt=%d/%d): %s",
                request_id, attempt + 1, attempts, type(exc).__name__,
            )
            log_event(
                "model_chat",
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
            logger.exception("model chat unexpected error (request_id=%s)", request_id)
            log_event(
                "model_chat",
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
            "model chat ok (fc) request_id=%s latency_ms=%d tool_calls=%d",
            request_id, latency_ms, len(tool_calls),
        )
        log_event(
            "model_chat",
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
            reasoning_content_chars=len(getattr(message, "reasoning_content", None) or ""),
            reasoning_content_present=bool(getattr(message, "reasoning_content", None)),
            **call_details,
            **_usage_details(resp),
        )
        return ChatTurn(
            content=message.content,
            tool_calls=tool_calls,
            reasoning_content=getattr(message, "reasoning_content", None),
        )

    logger.error(
        "model chat exhausted retries: %s",
        type(last_transient).__name__ if last_transient else "?",
    )
    log_event(
        "model_chat",
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
    planning_model: PlanningModel | None = None,
) -> str:
    """Real OpenAI-compatible chat call with retry + error mapping.

    Non-planning tasks use the vivo runtime. Planning intake/plain calls may use
    the selected DeepSeek runtime. Only final `message.content` is returned.
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

    runtime = _resolve_runtime(planning_model)
    client = _get_client(runtime)
    messages = _build_messages(system_prompt, user_text, image_data_urls)
    attempts = max(1, settings.VIVO_MAX_RETRY + 1)
    last_transient: Exception | None = None
    empty_content_retries_left = 2
    attempt = 0
    timeout = _text_timeout_seconds(task=task, override=timeout_seconds)
    call_details = {
        **_runtime_log_details(runtime),
        "system_prompt_chars": len(system_prompt),
        "user_text_chars": len(user_text),
        "image_count": len(image_data_urls or []),
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "timeout_seconds": timeout,
        "max_attempts": attempts,
    }

    while attempt < attempts:
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        try:
            request_kwargs = _completion_request_kwargs(
                runtime=runtime,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                timeout=timeout,
                request_id=request_id,
            )
            resp = client.chat.completions.create(**request_kwargs)
        except (AuthenticationError, PermissionDeniedError) as exc:
            # AppKey/permission/config → 1003, never retry.
            logger.error("model chat auth/permission error (request_id=%s)", request_id)
            log_event(
                "model_chat_json",
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
            logger.warning("model chat bad request (request_id=%s)", request_id)
            log_event(
                "model_chat_json",
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
                "model chat transient error (request_id=%s, attempt=%d/%d): %s",
                request_id, attempt + 1, attempts, type(exc).__name__,
            )
            log_event(
                "model_chat_json",
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
            logger.exception("model chat unexpected error (request_id=%s)", request_id)
            log_event(
                "model_chat_json",
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
            "model_chat_json",
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
            reasoning_content_chars=_response_reasoning_chars(resp),
            reasoning_content_present=_response_reasoning_chars(resp) > 0,
            **call_details,
            **_usage_details(resp),
        )
        if not content or not content.strip():
            log_event(
                "model_chat_json",
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
                    "model_chat_json",
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
    logger.error("model chat exhausted retries: %s", type(last_transient).__name__ if last_transient else "?")
    log_event(
        "model_chat_json",
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
    runtime: ChatRuntime,
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
        **_runtime_log_details(runtime),
        "message_count": len(messages),
        "message_role_counts": role_counts,
        "message_content_chars": content_chars,
        "tool_schema_count": len(tools or []),
        "tool_schema_names": tool_names,
        "temperature": temperature,
        "max_completion_tokens": max_completion_tokens,
        "timeout_seconds": timeout_seconds,
        "max_attempts": attempts,
    }


def _runtime_log_details(runtime: ChatRuntime) -> dict[str, Any]:
    return {
        "planning_model": runtime.planning_model,
        "provider": runtime.provider,
        "model": runtime.api_model,
        "base_url_host": runtime.base_url.split("//", 1)[-1].split("/", 1)[0],
        "thinking_enabled": True,
        "reasoning_effort": runtime.reasoning_effort,
        "token_parameter": runtime.token_parameter,
        "temperature_sent": runtime.uses_temperature,
    }


def _completion_request_kwargs(
    *,
    runtime: ChatRuntime,
    messages: list[dict[str, Any]],
    temperature: float,
    max_completion_tokens: int,
    timeout: int,
    request_id: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build provider-specific OpenAI SDK kwargs without leaking credentials."""
    kwargs: dict[str, Any] = {
        "model": runtime.api_model,
        "messages": messages,
        "stream": False,
        "reasoning_effort": runtime.reasoning_effort,
        "extra_body": runtime.extra_body,
        "timeout": timeout,
        **(extra or {}),
    }
    kwargs[runtime.token_parameter] = max_completion_tokens
    if runtime.uses_temperature:
        kwargs["temperature"] = temperature
    if runtime.uses_vivo_request_query:
        kwargs["extra_query"] = {"request_id": request_id}
    return kwargs


def _response_reasoning_chars(response: Any) -> int:
    try:
        return len(getattr(response.choices[0].message, "reasoning_content", None) or "")
    except (AttributeError, IndexError):
        return 0


def _usage_details(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    details = {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
    completion_details = getattr(usage, "completion_tokens_details", None)
    if completion_details is not None:
        details["reasoning_tokens"] = getattr(
            completion_details, "reasoning_tokens", None
        )
    return details


def _api_error_details(exc: Exception) -> dict[str, Any]:
    response = getattr(exc, "response", None)
    return {
        "error_type": type(exc).__name__,
        "error_message": _bounded_text(str(exc), 2000),
        "error_cause_chain": _exception_cause_chain(exc),
        "http_status": getattr(exc, "status_code", None)
        or getattr(response, "status_code", None),
        "error_code": getattr(exc, "code", None),
        "error_request_id": getattr(exc, "request_id", None),
    }


def _exception_cause_chain(exc: Exception, limit: int = 6) -> list[dict[str, str | None]]:
    """Return a bounded, cycle-safe cause chain without request headers or secrets."""
    chain: list[dict[str, str | None]] = []
    seen = {id(exc)}
    current = exc.__cause__ or exc.__context__
    while current is not None and len(chain) < limit and id(current) not in seen:
        seen.add(id(current))
        chain.append(
            {
                "type": type(current).__name__,
                "message": _bounded_text(str(current), 1000),
            }
        )
        current = current.__cause__ or current.__context__
    return chain


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
