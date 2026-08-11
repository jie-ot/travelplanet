"""Per-business-request logging helpers.

Each frontend action creates one readable log file under ``logs/``. Business
code writes stage events through a contextvar-backed logger, so nested services
and model/tool clients do not need file-handler plumbing in their signatures.
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, TypeVar

_current_log: contextvars.ContextVar["BusinessLog | None"] = contextvars.ContextVar(
    "business_log",
    default=None,
)

T = TypeVar("T")


class BusinessLog:
    """Small JSON-lines writer for one user-triggered business task."""

    def __init__(self, task_name: str, **fields: Any) -> None:
        self.task_name = _safe_slug(task_name)
        self.request_id = str(fields.pop("request_id", "") or uuid.uuid4())[:8]
        self.started = time.monotonic()
        self._lock = threading.RLock()
        self._sequence = 0
        self.path = _build_log_path(self.task_name, self.request_id)
        self.write("task_start", status="start", **fields)

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def write(
        self,
        event: str,
        *,
        status: str = "info",
        message: str | None = None,
        **fields: Any,
    ) -> None:
        payload = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "elapsed_ms": self.elapsed_ms(),
            "task": self.task_name,
            "request_id": self.request_id,
            "event": event,
            "status": status,
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
        }
        if message:
            payload["message"] = message
        for key, value in fields.items():
            if value is None:
                continue
            target = f"detail_{key}" if key in payload else key
            payload[target] = value
        with self._lock:
            self._sequence += 1
            payload["sequence"] = self._sequence
            line = json.dumps(payload, ensure_ascii=False, default=str)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


@contextmanager
def business_task(task_name: str, **fields: Any) -> Iterator[BusinessLog]:
    """Create one log file for a top-level frontend-triggered action."""
    log = BusinessLog(task_name, **fields)
    token = _current_log.set(log)
    try:
        yield log
    except Exception as exc:
        error_details = _exception_details(exc)
        log.write(
            "task_finish",
            status="failed",
            message=str(exc),
            total_ms=log.elapsed_ms(),
            **error_details,
        )
        raise
    else:
        log.write("task_finish", status="success", total_ms=log.elapsed_ms())
    finally:
        _current_log.reset(token)


def log_event(
    event: str,
    *,
    status: str = "info",
    message: str | None = None,
    **fields: Any,
) -> None:
    """Write an event to the active business log, if any."""
    log = _current_log.get()
    if log is not None:
        log.write(event, status=status, message=message, **fields)


@contextmanager
def timed_stage(stage: str, **fields: Any) -> Iterator[None]:
    """Log start/success/failure and duration for a business stage."""
    log = _current_log.get()
    started = time.monotonic()
    if log is not None:
        log.write(stage, status="start", **fields)
    try:
        yield
    except Exception as exc:
        if log is not None:
            error_details = _exception_details(exc)
            log.write(
                stage,
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                message=str(exc),
                **error_details,
                **fields,
            )
        raise
    else:
        if log is not None:
            log.write(
                stage,
                status="success",
                duration_ms=int((time.monotonic() - started) * 1000),
                **fields,
            )


def call_in_current_context(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> Callable[[], T]:
    """Return a zero-arg callable that keeps the active log in worker threads."""
    ctx = contextvars.copy_context()
    return lambda: ctx.run(func, *args, **kwargs)


def _build_log_path(task_name: str, request_id: str) -> str:
    root = os.path.abspath("logs")
    os.makedirs(root, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(root, f"{stamp}_{task_name}_{request_id}.log")


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    parts = [part for part in cleaned.split("_") if part]
    return "_".join(parts)[:60] or "business_task"


def _exception_details(exc: Exception) -> dict[str, Any]:
    """Return bounded, local-only diagnostics without changing log timing."""
    cause = exc.__cause__
    context = exc.__context__ if cause is None else None
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return {
        "error_type": type(exc).__name__,
        "error_module": type(exc).__module__,
        "error_repr": repr(exc)[:2000],
        "cause_type": type(cause).__name__ if cause is not None else None,
        "cause_message": str(cause)[:2000] if cause is not None else None,
        "context_type": type(context).__name__ if context is not None else None,
        "context_message": str(context)[:2000] if context is not None else None,
        "traceback": formatted[-12000:],
    }
