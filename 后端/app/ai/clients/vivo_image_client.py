"""vivo image-generation client (图生图).

The client calls vivo `image_generation?module=aigc&request_id=&system_time=`
with base64 image input and reads only `data.images[]`. The returned temporary
URL is handed to `storage_service` by the caller; this client never persists
files or manages FileAsset lifecycle.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

import httpx

from app.core.business_logging import log_event
from app.core.config import settings
from app.core.exceptions import AIGenerationError, ImageInputPolicyError, InternalError

logger = logging.getLogger("travelplanet")


@dataclass
class ImageGenerationResult:
    """Remote temp image URL returned by the model gateway."""

    image_url: str | None = None
    ext: str = "jpg"


def generate_image(*, prompt: str, image_data_urls: list[str]) -> ImageGenerationResult:
    """Generate one postcard image (image-to-image). Returns a transport result."""
    if not settings.VIVO_APP_KEY:
        raise InternalError("图片模型服务未配置 VIVO_APP_KEY")
    return _real_generate_image(prompt=prompt, image_data_urls=image_data_urls)


def _real_generate_image(*, prompt: str, image_data_urls: list[str]) -> ImageGenerationResult:
    """Real vivo image-to-image call.

    POSTs to `{VIVO_IMAGE_GENERATION_URL}?module=aigc&request_id=&system_time=`
    with base64 image input; reads only `data.images[]` (the first temp URL).
    The returned temp URL is handed to `storage_service` for download by the
    caller; this client never persists files.
    """
    if not image_data_urls:
        raise AIGenerationError("AI 生成失败：图生图缺少参考图")

    # A HTTP-200 response without a usable image is a transient gateway result
    # in practice. Always allow one retry for that case even when the generic
    # retry setting is disabled.
    attempts = max(2, settings.VIVO_MAX_RETRY + 1)
    last_transient: Exception | None = None
    body = {
        "model": settings.VIVO_IMAGE_MODEL,
        "prompt": prompt,
        "image": image_data_urls if len(image_data_urls) > 1 else image_data_urls[0],
        "parameters": {
            "size": "2K",
            "watermark": False,
            "sequential_image_generation": settings.VIVO_IMAGE_SEQUENTIAL_GENERATION,
        },
    }

    for attempt in range(attempts):
        request_id = str(uuid.uuid4())
        params = {
            "module": "aigc",
            "request_id": request_id,
            "system_time": str(int(time.time())),
        }
        headers = {
            "Authorization": f"Bearer {settings.VIVO_APP_KEY}",
            "Content-Type": "application/json; charset=utf-8",
        }
        started = time.monotonic()
        try:
            with httpx.Client(timeout=settings.VIVO_IMAGE_TIMEOUT_SECONDS) as client:
                resp = client.post(
                    settings.VIVO_IMAGE_GENERATION_URL,
                    params=params,
                    headers=headers,
                    json=body,
                )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            last_transient = exc
            logger.warning(
                "vivo image transient error (request_id=%s, attempt=%d/%d): %s",
                request_id, attempt + 1, attempts, type(exc).__name__,
            )
            log_event(
                "vivo_image",
                status="retry",
                request_id=request_id,
                attempt=attempt + 1,
                attempts=attempts,
                reason=type(exc).__name__,
            )
            _sleep_before_retry(attempt, attempts)
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("vivo image unexpected error (request_id=%s)", request_id)
            log_event(
                "vivo_image",
                status="failed",
                request_id=request_id,
                attempt=attempt + 1,
                reason=type(exc).__name__,
            )
            raise InternalError("图片模型服务内部错误") from exc

        if resp.status_code in (401, 403):
            logger.error("vivo image auth/permission error (request_id=%s)", request_id)
            log_event(
                "vivo_image",
                status="failed",
                request_id=request_id,
                attempt=attempt + 1,
                reason="auth_or_permission",
                http_status=resp.status_code,
            )
            raise InternalError("图片模型服务鉴权或权限错误")
        if resp.status_code == 429:
            last_transient = httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
            logger.warning("vivo image rate limited (request_id=%s)", request_id)
            log_event(
                "vivo_image",
                status="retry",
                request_id=request_id,
                attempt=attempt + 1,
                attempts=attempts,
                reason="rate_limited",
                http_status=resp.status_code,
            )
            _sleep_before_retry(attempt, attempts)
            continue
        if resp.status_code >= 500:
            last_transient = httpx.HTTPStatusError("5xx", request=resp.request, response=resp)
            logger.warning("vivo image 5xx (request_id=%s)", request_id)
            log_event(
                "vivo_image",
                status="retry",
                request_id=request_id,
                attempt=attempt + 1,
                attempts=attempts,
                reason="server_error",
                http_status=resp.status_code,
            )
            _sleep_before_retry(attempt, attempts)
            continue
        if resp.status_code != 200:
            logger.warning("vivo image bad request (request_id=%s status=%d)", request_id, resp.status_code)
            log_event(
                "vivo_image",
                status="failed",
                request_id=request_id,
                attempt=attempt + 1,
                reason="bad_status",
                http_status=resp.status_code,
            )
            raise AIGenerationError("AI 生成失败：图片模型返回错误")

        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            payload = resp.json()
            empty_reason = "empty_image"
        except ValueError:
            payload = None
            empty_reason = "invalid_json"
        image_url = _extract_image_url(payload)
        if not image_url:
            response_summary = _summarize_response(payload, resp)
            if _is_input_policy_violation(payload):
                logger.warning(
                    "vivo image input policy rejection (request_id=%s attempt=%d/%d)",
                    request_id,
                    attempt + 1,
                    attempts,
                )
                log_event(
                    "vivo_image",
                    status="rejected",
                    request_id=request_id,
                    attempt=attempt + 1,
                    attempts=attempts,
                    latency_ms=latency_ms,
                    reason="input_policy_violation",
                    http_status=resp.status_code,
                    response_summary=response_summary,
                )
                raise ImageInputPolicyError("图片模型输入内容审核未通过")
            can_retry = attempt + 1 < attempts
            logger.warning(
                "vivo image %s (request_id=%s attempt=%d/%d)",
                empty_reason,
                request_id,
                attempt + 1,
                attempts,
            )
            log_event(
                "vivo_image",
                status="retry" if can_retry else "failed",
                request_id=request_id,
                attempt=attempt + 1,
                attempts=attempts,
                latency_ms=latency_ms,
                reason=empty_reason,
                http_status=resp.status_code,
                response_summary=response_summary,
            )
            if can_retry:
                _sleep_before_retry(attempt, attempts)
                continue
            raise AIGenerationError("AI 生成失败：图片模型未返回图片")
        logger.info("vivo image ok request_id=%s latency_ms=%d", request_id, latency_ms)
        log_event(
            "vivo_image",
            status="success",
            request_id=request_id,
            attempt=attempt + 1,
            latency_ms=latency_ms,
        )
        return ImageGenerationResult(image_url=image_url, ext=_guess_ext(image_url))

    logger.error("vivo image exhausted retries: %s", type(last_transient).__name__ if last_transient else "?")
    log_event(
        "vivo_image",
        status="failed",
        reason="exhausted_retries",
        last_error=type(last_transient).__name__ if last_transient else None,
    )
    raise AIGenerationError("AI 生成失败：图片模型服务暂时不可用，请重试")


def _sleep_before_retry(attempt: int, attempts: int) -> None:
    if attempt + 1 >= attempts:
        return
    delay = max(0.0, settings.VIVO_RETRY_BACKOFF_SECONDS) * (attempt + 1)
    if delay:
        time.sleep(delay)


def _extract_image_url(payload: object) -> str | None:
    """Read only `data.images[]`; ignore the deprecated `data.image`."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return None
    images = data.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("url")
    return None


def _summarize_response(payload: object, response: httpx.Response) -> dict[str, object]:
    """Return a diagnostic response shape without image URLs or response bodies."""
    summary: dict[str, object] = {
        "payload_type": type(payload).__name__ if payload is not None else "invalid_json",
        "content_type": response.headers.get("content-type"),
        "body_bytes": len(response.content),
    }
    if not isinstance(payload, dict):
        return summary

    summary["top_level_keys"] = sorted(str(key) for key in payload)[:20]
    for field in ("code", "status", "request_id", "trace_id"):
        value = payload.get(field)
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[f"business_{field}"] = value
    message = payload.get("message") or payload.get("msg")
    if isinstance(message, str):
        summary["business_message"] = message[:200]

    data = payload.get("data")
    if isinstance(data, dict):
        summary["data_keys"] = sorted(str(key) for key in data)[:20]
        images = data.get("images")
        summary["images_type"] = type(images).__name__
        if isinstance(images, list):
            summary["images_count"] = len(images)
        for field in ("code", "status"):
            value = data.get(field)
            if isinstance(value, (str, int, float, bool)) or value is None:
                summary[f"data_{field}"] = value
    return summary


def _is_input_policy_violation(payload: object) -> bool:
    """Match only the provider's explicit input-policy rejection response."""
    if not isinstance(payload, dict):
        return False
    code = payload.get("code")
    message = payload.get("message") or payload.get("msg")
    return str(code) == "1004" and isinstance(message, str) and (
        "input content violates policy" in message.lower()
    )


def _guess_ext(url: str) -> str:
    lower = url.lower().split("?")[0]
    for ext in ("png", "jpeg", "jpg", "webp"):
        if lower.endswith(f".{ext}"):
            return "jpg" if ext == "jpeg" else ext
    return "jpg"
