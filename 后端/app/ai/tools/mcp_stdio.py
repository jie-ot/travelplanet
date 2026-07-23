"""Synchronous, non-blocking access to MCP stdio servers (A′ rail / flight).

Why this exists (《外部事实源与工具调用规范》三·补充): the project endpoints and
services are synchronous, but the community MCP servers are spawned via
`npx`/`uvx` over stdio and their **first** cold start (npm/PyPI download + node /
python boot) can take far longer than `TOOL_TIMEOUT_SECONDS`. Spawning a fresh
subprocess per tool call inside the request thread (and tearing it down on
timeout) is what made `/api/ai/planning` appear to "hang" — several cold starts
chained together blocked the response for tens of seconds.

This module fixes that with a runtime that:

1. Runs ONE long-lived asyncio event loop on a dedicated daemon thread.
2. Keeps a **persistent** MCP session per endpoint, so each server is cold
   started at most once per process (in the background), then reused.
3. Establishes/initializes sessions **lazily and in the background**: the very
   first call for a not-yet-ready endpoint returns ``None`` immediately
   (degrade to the B-class entry guide) instead of blocking; once the session
   is warm, subsequent calls get real reference facts fast.
4. Bounds every tool call by ``TOOL_TIMEOUT_SECONDS`` and never lets the request
   thread block past it — even if MCP cleanup stalls.
5. Caches unavailability (`MCP_UNAVAILABLE_TTL_SECONDS`) so a failing/slow
   endpoint is skipped quickly instead of being cold-started over and over.

Any probe/timeout/exception/empty result yields ``None`` so the caller silently
degrades to the B-class entry guide — A′ exceptions must never bubble to the
business layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import threading
import time
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

logger = logging.getLogger("travelplanet")

# Known runners; a leading flag (e.g. "-y") implies npx (node ecosystem).
_KNOWN_RUNNERS = {"npx", "uvx", "pipx", "uv", "python", "python3", "node"}

# Child MCP processes (e.g. flight-ticket-mcp-server) may print emoji; on Windows the
# default console encoding is GBK unless these are set.
_SUBPROCESS_UTF8_ENV: dict[str, str] = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


def build_subprocess_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build env overrides merged by MCP stdio with its safe inherited defaults."""
    env = dict(_SUBPROCESS_UTF8_ENV)
    if extra_env:
        env.update(extra_env)
    return env


def parse_stdio_endpoint(endpoint: str) -> tuple[str, list[str]] | None:
    """Parse a stdio endpoint into (command, args).

    Recommended form is `stdio:<command...>` (colon). Whitespace after `stdio`
    is also accepted for backward compatibility:
      "stdio:uvx mcp-server-12306"               -> ("uvx", ["mcp-server-12306"])
      "stdio:npx -y 12306-mcp"                   -> ("npx", ["-y", "12306-mcp"])
      "stdio:uvx flight-ticket-mcp-server@latest" -> ("uvx", ["flight-ticket-mcp-server@latest"])
      "stdio -y 12306-mcp"                       -> ("npx", ["-y", "12306-mcp"])  (compat)
    Returns None when the endpoint is empty or unparseable.
    """
    if not endpoint:
        return None
    text = endpoint.strip()
    low = text.lower()
    if low.startswith("stdio:"):
        text = text[len("stdio:"):].strip()
    elif low == "stdio":
        return None
    elif low.startswith("stdio ") or low.startswith("stdio\t"):
        text = text[len("stdio"):].strip()
    if not text:
        return None
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    if not tokens:
        return None
    if tokens[0] in _KNOWN_RUNNERS:
        return tokens[0], tokens[1:]
    if tokens[0].startswith("-"):
        return "npx", tokens
    return "uvx", tokens


def _content_to_dict(result: Any) -> dict | None:
    """Best-effort extraction of a dict from an MCP call_tool result.

    Prefers structured content, then a JSON dict parsed from a text block. Some
    community servers (e.g. node `12306-mcp` `get-tickets`) return plain,
    human-readable text rather than JSON; in that case the combined text is
    returned under the `_raw_text` key so adapters can parse it themselves.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    content = getattr(result, "content", None) or []
    texts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if not text:
            continue
        texts.append(text)
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
    if texts:
        return {"_raw_text": "\n".join(texts)}
    return None


# ============================================================
# Persistent MCP runtime (single background loop + per-endpoint sessions)
# ============================================================


@dataclass
class _Server:
    """Per-endpoint runtime state, only mutated on the runtime loop thread."""

    status: str = "absent"  # absent -> starting -> ready / failed
    session: Any = None
    exit_stack: Any = None
    failed_at: float = 0.0


class _McpRuntime:
    """Owns a dedicated event loop thread and persistent MCP sessions."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._servers: dict[str, _Server] = {}
        self._env: dict[str, dict[str, str] | None] = {}

    # —— loop bootstrap ——

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            loop = asyncio.new_event_loop()

            def _run() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thread = threading.Thread(target=_run, name="mcp-runtime", daemon=True)
            thread.start()
            self._loop = loop
            self._thread = thread
            return loop

    # —— public sync entry ——

    def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: dict,
        *,
        extra_env: dict[str, str] | None,
        timeout: float,
    ) -> dict | None:
        """Call one MCP tool synchronously; never blocks past ``timeout``.

        Returns ``None`` (→ caller degrades to B) when the endpoint is not yet
        warm, is within its unavailability window, or the call fails/empties.
        Triggers a background warm-up for not-yet-started endpoints.
        """
        if parse_stdio_endpoint(endpoint) is None:
            return None
        loop = self._ensure_loop()

        with self._lock:
            srv = self._servers.get(endpoint)
            if srv is None:
                srv = _Server()
                self._servers[endpoint] = srv
            self._env[endpoint] = extra_env
            status = srv.status
            within_ttl = (
                status == "failed"
                and (time.monotonic() - srv.failed_at) < settings.MCP_UNAVAILABLE_TTL_SECONDS
            )

        # Not ready: kick a background warm-up (unless cooling down) and degrade now.
        if status != "ready":
            if status in ("absent", "failed") and not within_ttl:
                self._kick_warmup(loop, endpoint)
            return None

        # Ready: run the tool call, bounded by ``timeout``.
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._call_on_ready(endpoint, tool_name, arguments), loop
            )
            return future.result(timeout=timeout)
        except Exception:  # noqa: BLE001  (TimeoutError / cancelled / protocol error)
            logger.warning("MCP tool call failed/timeout (tool=%s); degrading to B", tool_name)
            self._mark_failed(endpoint)
            try:
                future.cancel()
            except Exception:  # noqa: BLE001
                pass
            return None

    # —— internal coroutines (run on the runtime loop) ——

    def _kick_warmup(self, loop: asyncio.AbstractEventLoop, endpoint: str) -> None:
        """Schedule a non-blocking background establishment of the session."""
        with self._lock:
            srv = self._servers.setdefault(endpoint, _Server())
            if srv.status == "starting":
                return
            srv.status = "starting"
        asyncio.run_coroutine_threadsafe(self._establish(endpoint), loop)
        logger.info("MCP warm-up started (endpoint=%s); first call degrades to B", endpoint)

    async def _establish(self, endpoint: str) -> None:
        """Cold start + initialize a persistent session in the background."""
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        parsed = parse_stdio_endpoint(endpoint)
        if parsed is None:
            self._mark_failed(endpoint)
            return
        command, args = parsed
        extra_env = self._env.get(endpoint)
        stack = AsyncExitStack()
        try:
            server = StdioServerParameters(
                command=command, args=args, env=build_subprocess_env(extra_env)
            )
            # One deadline covers process spawn, transport setup and initialize.
            # Previously each phase received the full timeout independently.
            async with asyncio.timeout(settings.MCP_STARTUP_TIMEOUT_SECONDS):
                read, write = await stack.enter_async_context(stdio_client(server))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
        except Exception:  # noqa: BLE001
            logger.exception(
                "MCP session establish failed (endpoint=%s); cooling down", endpoint
            )
            # Publish the terminal state before cleanup: closing a half-open
            # stdio transport can itself stall, but startup waiters must finish.
            self._mark_failed(endpoint)
            try:
                await asyncio.wait_for(stack.aclose(), timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            return
        with self._lock:
            srv = self._servers.setdefault(endpoint, _Server())
            srv.session = session
            srv.exit_stack = stack
            srv.status = "ready"
        logger.info("MCP session ready (endpoint=%s)", endpoint)

    async def _call_on_ready(self, endpoint: str, tool_name: str, arguments: dict) -> dict | None:
        srv = self._servers.get(endpoint)
        if srv is None or srv.status != "ready" or srv.session is None:
            return None
        result = await srv.session.call_tool(tool_name, arguments)
        return _content_to_dict(result)

    def _mark_failed(self, endpoint: str) -> None:
        """Mark an endpoint unavailable and schedule teardown of its session."""
        loop = self._loop
        with self._lock:
            srv = self._servers.setdefault(endpoint, _Server())
            stack = srv.exit_stack
            srv.session = None
            srv.exit_stack = None
            srv.status = "failed"
            srv.failed_at = time.monotonic()
        if stack is not None and loop is not None:
            asyncio.run_coroutine_threadsafe(_safe_aclose(stack), loop)


async def _safe_aclose(stack: Any) -> None:
    try:
        await stack.aclose()
    except Exception:  # noqa: BLE001
        pass


_runtime = _McpRuntime()


def get_runtime_status(endpoint: str) -> str:
    """Return the current endpoint state for readiness checks and diagnostics."""
    if parse_stdio_endpoint(endpoint) is None:
        return "invalid"
    with _runtime._lock:
        server = _runtime._servers.get(endpoint)
        return server.status if server is not None else "absent"


def wait_until_ready(endpoint: str, timeout: float) -> str:
    """Wait for an endpoint to become ready or fail, bounded by ``timeout``.

    This is intended for application startup and must not be called on the MCP
    runtime thread. It returns ``ready``, ``failed``, ``timeout`` or ``invalid``.
    """
    if parse_stdio_endpoint(endpoint) is None:
        return "invalid"

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        status = get_runtime_status(endpoint)
        if status in {"ready", "failed", "invalid"}:
            return status
        if status == "absent":
            prewarm(endpoint)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout"
        time.sleep(min(0.05, remaining))


def call_stdio_tool_sync(
    endpoint: str,
    tool_name: str,
    arguments: dict,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> dict | None:
    """Call one MCP tool over a persistent stdio session, synchronously.

    Never blocks the request thread past ``timeout`` (defaults to
    ``TOOL_TIMEOUT_SECONDS``) and never raises. Returns ``None`` on any failure
    or while the session is still warming up, so the caller degrades to B.
    """
    budget = timeout if timeout is not None else settings.TOOL_TIMEOUT_SECONDS
    try:
        return _runtime.call_tool(
            endpoint, tool_name, arguments, extra_env=extra_env, timeout=float(budget)
        )
    except Exception:  # noqa: BLE001
        logger.warning("MCP stdio call failed (tool=%s); degrading to B", tool_name)
        return None


def prewarm(endpoint: str, *, extra_env: dict[str, str] | None = None) -> None:
    """Best-effort, non-blocking warm-up of an endpoint's session.

    Safe to call at startup; if the endpoint is empty/unparseable or already
    warming/ready it is a no-op. Never blocks and never raises.
    """
    try:
        if parse_stdio_endpoint(endpoint) is None:
            return
        loop = _runtime._ensure_loop()
        with _runtime._lock:
            srv = _runtime._servers.get(endpoint)
            _runtime._env[endpoint] = extra_env
            status = srv.status if srv is not None else "absent"
        if status in ("ready", "starting"):
            return
        _runtime._kick_warmup(loop, endpoint)
    except Exception:  # noqa: BLE001
        logger.warning("MCP prewarm failed (endpoint=%s)", endpoint)
