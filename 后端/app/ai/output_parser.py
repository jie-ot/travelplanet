"""Model output parsing & validation (《后端与大模型通信接口规范》八).

Strips ```json fences, extracts the first complete JSON object, and validates
against a Pydantic model. Empty content / non-JSON / missing fields / type
errors / validation failures all raise `AIGenerationError` (1001). Tasks that
explicitly support a JSON retry handle it in the orchestrator.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.exceptions import AIGenerationError

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def extract_first_json(text: str) -> str:
    """Return the first balanced JSON object/array substring in `text`."""
    if not text or not text.strip():
        raise AIGenerationError("模型返回内容为空")
    cleaned = _strip_fences(text)

    # Fast path: already a clean JSON document.
    stripped = cleaned.strip()
    if stripped.startswith(("{", "[")):
        candidate = _balanced_slice(stripped)
        if candidate is not None:
            return candidate

    # Otherwise scan for the first opening brace/bracket.
    for idx, ch in enumerate(cleaned):
        if ch in "{[":
            candidate = _balanced_slice(cleaned[idx:])
            if candidate is not None:
                return candidate
    raise AIGenerationError("模型未返回有效 JSON")


def _balanced_slice(text: str) -> str | None:
    """Return the balanced JSON slice starting at index 0, or None."""
    if not text or text[0] not in "{[":
        return None
    open_ch = text[0]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for idx, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[: idx + 1]
    return None


def parse_model_json(text: str, model: type[T]) -> T:
    """Extract JSON from `text` and validate it into `model`. Raises 1001."""
    raw = extract_first_json(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIGenerationError("模型返回的 JSON 无法解析") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise AIGenerationError("模型返回结构不符合要求") from exc
