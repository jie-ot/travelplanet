"""Model orchestration (《后端与大模型通信接口规范》三、九).

Organizes model requests, reads prompt templates, calls clients, and parses
output into validated schemas. It NEVER writes business tables, manages
FileAsset lifecycle, maps paths, or holds DB transactions — callers in
`app/services` own all of that. Base64 image inputs are prepared by the calling
service and passed in.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.ai import output_parser, planning_feasibility
from app.ai.clients import vivo_chat_client, vivo_image_client
from app.ai.clients.vivo_image_client import ImageGenerationResult
from app.ai.memory import context_builder
from app.ai.model_selection import (
    DEFAULT_PLANNING_MODEL,
    DEEPSEEK_PLANNING_MODELS,
    PlanningModel,
    requires_reasoning_replay,
)
from app.ai.prompts import load_prompt
from app.ai.schemas import (
    MemoryUpdateResult,
    PhotoAnalysisItem,
    PhotoAnalysisResult,
    PlanningIntakeResult,
    PostcardPlanItem,
    PostcardPlanResult,
    PostcardSelectionResult,
    ReportDraftResult,
)
from app.ai.tools import tool_specs
from app.core import planning_progress
from app.core.business_logging import call_in_current_context, log_event, timed_stage
from app.core.config import settings
from app.core.exceptions import AIGenerationError, ImageInputPolicyError
from app.models.itinerary import ItineraryData
from app.models.dto import PlanningBrief, PlanningChatMessage

logger = logging.getLogger("travelplanet")

# Planning function-calling loop bounds (defensive; tool execution is fast and
# non-blocking, but the model must always converge to a final JSON answer).
_MAX_TOOL_ROUNDS = 8
_TARGET_TOOL_ROUNDS = 5
_MAX_TOOL_CALLS_TOTAL = 75
_MAX_INTAKE_TOOL_ROUNDS = 3
_MAX_INTAKE_TOOL_CALLS_TOTAL = 8
# A full itinerary is ~12k tokens of JSON and a thinking model spends as much
# again on reasoning first. At 16k the 2026-08-07 flash run twice hit
# finish_reason=length with an empty message and had to be retried without
# thinking, losing ~190s. The cap only bounds the response, so headroom is free.
_MAX_GENERATION_TOKENS = 32000
# Raw tool payloads from older rounds are the bulk of the research context, and
# they are what made each round slower than the last (32s → 134s across six
# rounds). They are also redundant: every fact is kept in `fact_registry` and
# re-supplied in full for the final synthesis, so the history only has to record
# what was already asked. The two most recent rounds stay verbatim, because the
# model reasons over those results to choose its next queries.
_VERBATIM_TOOL_HISTORY_ROUNDS = 2
_TOOL_HISTORY_DIGEST_KEY = "compactedToolResult"
_TOOL_HISTORY_DISCARDED_KEY = "historyDiscarded"
# Marker embedded in confirmed_requirement_text; compaction must never rewrite
# the user message that carries the confirmation checklist.
_CONFIRMATION_CHECKLIST_MARKER = "【确认清单】"
# Eleven enabled AMap endpoint buckets × the console-confirmed 3 QPS each. This
# fills the purchased aggregate capacity without spawning one thread per call.
_MAX_PARALLEL_EXTERNAL_TOOLS = 33
_JSON_REPAIR_TIMEOUT_SECONDS = 60
_PLAIN_FALLBACK_TIMEOUT_SECONDS = 90
_MAX_FINAL_FACTS = 96
_MAX_FINAL_FACTS_PER_QUERY = {
    "amap_poi_search": 4,
    "amap_poi_around": 4,
    "amap_poi_detail": 10,
    "amap_route": 1,
    "amap_weather_range": 8,
    "query_rail_tickets": 8,
    "searchFlightItineraries": 8,
    "searchFlightsTransferinfo": 6,
    "searchFlightandTrainTransferinfo": 6,
}
_FINAL_FACT_DROP_KEYS = frozenset(
    {
        "polyline",
        "photo_url",
        "provider_payload",
        "provider_raw_text",
        "provider_parsed_content",
    }
)
# Provider evidence that is worth keeping once per tool result but must not be
# copied into every sibling candidate fact.
_BULKY_CONTEXT_KEYS = frozenset(
    {
        "provider_summary_text",
        "available_departure_datetimes",
    }
)
PHOTO_ANALYZE_BATCH_SIZE = 5
PHOTO_ANALYZE_SEMANTIC_MAX_RETRIES = 1

_UNKNOWN_LOCATIONS = frozenset({"未知地点", "未知目的地", ""})


def photo_analyze_batch_count(photo_count: int) -> int:
    """How many model calls photo understanding needs."""
    if photo_count <= 0:
        return 0
    return (photo_count + PHOTO_ANALYZE_BATCH_SIZE - 1) // PHOTO_ANALYZE_BATCH_SIZE


def photo_analyze_parallelism(batch_count: int | None = None) -> int:
    """Configured concurrency cap for photo-understanding model calls."""
    configured = max(1, settings.PHOTO_ANALYZE_PARALLELISM)
    if batch_count is None:
        return configured
    return max(1, min(batch_count, configured))


def photo_analyze_parallel_wave_count(photo_count: int) -> int:
    """How many waves are needed after applying the concurrency cap."""
    batch_count = photo_analyze_batch_count(photo_count)
    if batch_count <= 0:
        return 0
    workers = photo_analyze_parallelism(batch_count)
    return (batch_count + workers - 1) // workers


def photo_analyze_parallel_wall_budget_seconds(photo_count: int | None = None) -> float:
    """Worst-case wall time for photo understanding under the concurrency cap."""
    per_wave = settings.VIVO_TEXT_TIMEOUT_SECONDS * max(1, settings.VIVO_MAX_RETRY + 1)
    if photo_count is None:
        return per_wave
    return photo_analyze_parallel_wave_count(photo_count) * per_wave


# A callable injected by the service layer: (tool_name, arguments) -> result
# dict. The orchestrator intercepts the model's function calls and routes them
# here; the service (travel_fact_service) validates + executes them. The
# orchestrator never imports a Provider or travel_fact_service directly.
ToolExecutor = Callable[[str, dict], dict]
StructuredResultT = TypeVar("StructuredResultT", bound=BaseModel)


class _PlanningFallbackExhausted(AIGenerationError):
    """The bounded same-context JSON recovery path has been exhausted."""


def _execute_external_batch(
    requests: dict[str, tuple[str, dict[str, Any]]],
    execute_tool: ToolExecutor,
    *,
    max_total_attempts: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Run independent queries concurrently with per-query transient retries.

    The key is normally the normalized cache key. AMap and independent
    Tripmatch calls share the general worker pool; 12306 calls stay serial
    because its persistent MCP session is not documented as concurrency-safe,
    while still overlapping with the other providers. Retry budget is reserved
    only for the query that failed; unrelated queries are never replayed.
    """
    if not requests:
        return {}

    max_attempts = max(1, settings.TOOL_MAX_RETRY + 1)
    extra_attempts = (
        None
        if max_total_attempts is None
        else max(0, max_total_attempts - len(requests))
    )
    retry_budget_lock = threading.Lock()

    def reserve_retry() -> bool:
        nonlocal extra_attempts
        if extra_attempts is None:
            return True
        with retry_budget_lock:
            if extra_attempts <= 0:
                return False
            extra_attempts -= 1
            return True

    def run_one(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        attempts = 0
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                result = execute_tool(tool_name, arguments)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "parallel planning tool failed (non-fatal): %s", tool_name
                )
                result = {
                    "tool": tool_name,
                    "status": "error",
                    "message": "工具执行失败，请以官方渠道为准",
                    "error_type": type(exc).__name__,
                    "error_code": "tool_executor_exception",
                    "retryable": _is_transient_exception(exc),
                }
            retryable, retry_reason = _classify_tool_retry(result)
            result["retryable"] = retryable
            if not retryable or attempt >= max_attempts or not reserve_retry():
                break
            log_event(
                "planning_tool_point_retry",
                status="scheduled",
                tool_name=tool_name,
                arguments=_summarize_planning_value(arguments),
                failed_attempt=attempt,
                max_attempts=max_attempts,
                retry_reason=retry_reason,
            )
            time.sleep(min(0.5, 0.2 * attempt))
        result["_attempt_count"] = attempts
        planning_progress.add_tool_activity(
            _tool_activity_label(tool_name, arguments, result),
            count=attempts,
        )
        return result

    general = {
        key: item
        for key, item in requests.items()
        if item[0] != tool_specs.TOOL_QUERY_RAIL
    }
    rail = {
        key: item
        for key, item in requests.items()
        if item[0] == tool_specs.TOOL_QUERY_RAIL
    }
    results: dict[str, dict[str, Any]] = {}
    workers = max(1, min(_MAX_PARALLEL_EXTERNAL_TOOLS, len(general)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            key: pool.submit(call_in_current_context(run_one, tool_name, arguments))
            for key, (tool_name, arguments) in general.items()
        }
        # Keep the MCP lane serial but execute it while AMap futures are running.
        for key, (tool_name, arguments) in rail.items():
            results[key] = run_one(tool_name, arguments)
        for key, future in futures.items():
            results[key] = future.result()
    return results


_ACTIVITY_TOOL_LABELS: dict[str, str] = {
    tool_specs.TOOL_AMAP_WEATHER_RANGE: "天气",
    tool_specs.TOOL_AMAP_POI_SEARCH: "地点",
    tool_specs.TOOL_AMAP_POI_AROUND: "周边",
    tool_specs.TOOL_AMAP_POI_DETAIL: "地点详情",
    tool_specs.TOOL_AMAP_ROUTE: "路线",
    tool_specs.TOOL_QUERY_RAIL: "火车票",
    tool_specs.TOOL_SEARCH_FLIGHT_ITINERARIES: "航班",
    tool_specs.TOOL_SEARCH_FLIGHT_TRANSFER: "航班中转",
    tool_specs.TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER: "空铁联运",
}


def _tool_activity_label(
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> str:
    """One short human sentence describing a finished query, for the UI."""
    kind = _ACTIVITY_TOOL_LABELS.get(tool_name, tool_name)
    origin = (
        arguments.get("origin")
        or arguments.get("depCityCode")
        or arguments.get("depcity")
    )
    destination = (
        arguments.get("destination")
        or arguments.get("arrCityCode")
        or arguments.get("arrcity")
    )
    if origin and destination:
        subject = f"{origin}→{destination}"
    else:
        subject = str(
            arguments.get("keyword")
            or arguments.get("city")
            or arguments.get("location")
            or ""
        )
    when = str(
        arguments.get("date")
        or arguments.get("depDate")
        or arguments.get("depdate")
        or arguments.get("startDate")
        or ""
    )
    status = str(result.get("status") or "")
    suffix = "" if status in {"ok", "partial", ""} else "（未取到）"
    parts = [part for part in (when[5:] if when else "", subject) if part]
    return f"查询{kind} {' '.join(parts)}{suffix}".strip()


def _is_transient_exception(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(
        marker in name or marker in text
        for marker in (
            "timeout",
            "connect",
            "connection",
            "ratelimit",
            "rate_limit",
            "temporar",
            "429",
            "502",
            "503",
            "504",
        )
    )


def _classify_tool_retry(result: dict[str, Any]) -> tuple[bool, str]:
    """Classify one structured tool result without retrying business misses."""
    explicit = result.get("retryable")
    error_code = str(result.get("error_code") or "").lower()
    status = str(result.get("status") or "").lower()
    http_status = result.get("http_status")
    if explicit is not None:
        return bool(explicit), error_code or status or "explicit"
    if status == "timeout" or http_status == 429:
        return True, error_code or status
    if isinstance(http_status, int) and http_status >= 500:
        return True, error_code or f"http_{http_status}"
    transient_markers = (
        "timeout",
        "rate_limited",
        "upstream_error",
        "connection",
        "temporar",
        "warming_up",
        "call_failed",
        "startup_or_call_failed",
    )
    if any(marker in error_code for marker in transient_markers):
        return True, error_code
    return False, error_code or status or "not_retryable"


def _cacheable_tool_result(result: dict[str, Any]) -> bool:
    """Transient failures must not poison the same-request query cache."""
    retryable, _ = _classify_tool_retry(result)
    return not retryable


def _tool_result_has_usable_fact(result: dict[str, Any]) -> bool:
    context = result.get("result_context")
    context_status = context.get("status") if isinstance(context, dict) else None
    return str(result.get("status") or context_status or "").lower() in {
        "ok",
        "needs_official_confirmation",
    }


def analyze_photos(
    *,
    photo_metas: list[dict[str, Any]],
    image_data_urls: list[str],
    requirements: str,
    memory_summary: str,
) -> PhotoAnalysisResult:
    """Photo understanding → PhotoAnalysisResult.

    Large uploads are split into batches of up to ``PHOTO_ANALYZE_BATCH_SIZE``
    photos per model call. Batches run under a separate concurrency cap so
    near-limit uploads do not fan out too many model requests at once. Batch
    outputs are merged and re-ordered to match the input manifest so downstream
    postcard/report steps see one complete ``PhotoAnalysisResult``.
    """
    if len(photo_metas) != len(image_data_urls):
        raise AIGenerationError("AI 生成失败：照片元数据与图片数量不一致")
    if not photo_metas:
        raise AIGenerationError("AI 生成失败：无照片可分析")

    system_prompt = load_prompt("photo_analyze_system.md")
    ordered_asset_ids = [str(m["asset_id"]) for m in photo_metas]
    batch_total = photo_analyze_batch_count(len(photo_metas))

    def _run_batch(batch_idx: int) -> PhotoAnalysisResult:
        start = batch_idx * PHOTO_ANALYZE_BATCH_SIZE
        end = start + PHOTO_ANALYZE_BATCH_SIZE
        batch_metas = photo_metas[start:end]
        batch_urls = image_data_urls[start:end]
        batch_asset_ids = [str(m["asset_id"]) for m in batch_metas]
        user_text = context_builder.build_photo_analysis_user_text(
            photo_metas=batch_metas,
            requirements=requirements,
            memory_summary=memory_summary,
            batch_index=batch_idx + 1,
            batch_total=batch_total,
        )
        if batch_total > 1:
            logger.info(
                "photo analyze batch %d/%d (%d photos)",
                batch_idx + 1,
                batch_total,
                len(batch_metas),
            )
        attempt_user_text = user_text
        max_attempts = PHOTO_ANALYZE_SEMANTIC_MAX_RETRIES + 1
        for attempt in range(1, max_attempts + 1):
            is_semantic_retry = attempt > 1
            if is_semantic_retry:
                log_event(
                    "orchestrator_photo_batch_retry",
                    status="start",
                    batch_index=batch_idx + 1,
                    batch_total=batch_total,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    asset_ids=batch_asset_ids,
                    reason="asset_id_mismatch",
                )

            with timed_stage(
                "orchestrator_photo_batch",
                batch_index=batch_idx + 1,
                batch_total=batch_total,
                photo_count=len(batch_metas),
                asset_ids=batch_asset_ids,
                attempt=attempt,
                semantic_retry=is_semantic_retry,
            ):
                call_kwargs: dict[str, Any] = {
                    "task": vivo_chat_client.TASK_PHOTO_ANALYZE,
                    "system_prompt": system_prompt,
                    "user_text": attempt_user_text,
                    "image_data_urls": batch_urls,
                }
                if is_semantic_retry:
                    call_kwargs["temperature"] = 0
                raw = vivo_chat_client.chat_json(**call_kwargs)
                result = output_parser.parse_model_json(raw, PhotoAnalysisResult)

            missing_ids, unexpected_ids, duplicate_ids = _photo_analysis_id_diff(
                result, batch_asset_ids
            )
            returned_photo_count = len(result.photos)
            if (
                not missing_ids
                and not unexpected_ids
                and not duplicate_ids
                and returned_photo_count == len(batch_asset_ids)
            ):
                log_event(
                    "orchestrator_photo_batch_validation",
                    status="success",
                    batch_index=batch_idx + 1,
                    batch_total=batch_total,
                    attempt=attempt,
                    expected_photo_count=len(batch_asset_ids),
                    returned_photo_count=returned_photo_count,
                )
                return result

            log_event(
                "orchestrator_photo_batch_validation",
                status="failed",
                batch_index=batch_idx + 1,
                batch_total=batch_total,
                attempt=attempt,
                expected_photo_count=len(batch_asset_ids),
                returned_photo_count=returned_photo_count,
                missing_ids=missing_ids,
                unexpected_ids=unexpected_ids,
                duplicate_ids=duplicate_ids,
            )
            if attempt >= max_attempts:
                raise AIGenerationError(
                    "AI 生成失败："
                    f"第 {batch_idx + 1}/{batch_total} 批照片分析结果与输入清单不一致"
                )
            attempt_user_text = _build_photo_analysis_retry_text(
                user_text=user_text,
                expected_asset_ids=batch_asset_ids,
                missing_ids=missing_ids,
                unexpected_ids=unexpected_ids,
                duplicate_ids=duplicate_ids,
                returned_photo_count=returned_photo_count,
            )

        raise AssertionError("unreachable photo analysis retry state")

    if batch_total == 1:
        return _run_batch(0)

    workers = photo_analyze_parallelism(batch_total)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(call_in_current_context(_run_batch, batch_idx))
            for batch_idx in range(batch_total)
        ]
        batch_results = [future.result() for future in futures]
    return _merge_photo_analysis_results(batch_results, ordered_asset_ids)


def _photo_analysis_id_diff(
    result: PhotoAnalysisResult,
    expected_asset_ids: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Return stable manifest differences for one photo-analysis batch."""
    expected = set(expected_asset_ids)
    returned_ids = [item.asset_id for item in result.photos]
    returned = set(returned_ids)
    counts = Counter(returned_ids)
    missing_ids = [
        asset_id for asset_id in expected_asset_ids if asset_id not in returned
    ]
    unexpected_ids = sorted(returned - expected)
    duplicate_ids = sorted(asset_id for asset_id, count in counts.items() if count > 1)
    return missing_ids, unexpected_ids, duplicate_ids


def _build_photo_analysis_retry_text(
    *,
    user_text: str,
    expected_asset_ids: list[str],
    missing_ids: list[str],
    unexpected_ids: list[str],
    duplicate_ids: list[str],
    returned_photo_count: int,
) -> str:
    """Add a bounded, explicit manifest correction for the single retry."""
    expected_json = json.dumps(expected_asset_ids, ensure_ascii=False)
    missing_json = json.dumps(missing_ids, ensure_ascii=False)
    unexpected_json = json.dumps(unexpected_ids, ensure_ascii=False)
    duplicate_json = json.dumps(duplicate_ids, ensure_ascii=False)
    return (
        user_text
        + "\n\n【上一次输出校验失败，请重新分析本批图片】\n"
        + f"上次返回 {returned_photo_count} 条照片结果；"
        + f"缺少 {missing_json}，多出 {unexpected_json}，重复 {duplicate_json}。\n"
        + f"photos 必须恰好返回 {len(expected_asset_ids)} 条，"
        + f"必须恰好包含以下 asset_id：{expected_json}。\n"
        + "asset_id 必须逐字符原样复制，不得遗漏、重复、修改或添加其他 ID。"
        + "只输出修正后的完整 JSON 对象。"
    )


def _merge_photo_analysis_results(
    batches: list[PhotoAnalysisResult],
    ordered_asset_ids: list[str],
) -> PhotoAnalysisResult:
    """Merge per-batch photo analysis into one result for downstream tasks."""
    by_id: dict[str, PhotoAnalysisItem] = {}
    for batch in batches:
        for item in batch.photos:
            if item.asset_id in by_id:
                raise AIGenerationError("AI 生成失败：照片分析结果 asset_id 重复")
            by_id[item.asset_id] = item

    photos: list[PhotoAnalysisItem] = []
    for asset_id in ordered_asset_ids:
        item = by_id.get(asset_id)
        if item is None:
            raise AIGenerationError("AI 生成失败：照片分析结果缺少本批照片")
        photos.append(item)

    return PhotoAnalysisResult(
        photos=photos,
        overall_location=_merge_overall_location(batches),
        start_date=_merge_start_date(batches, photos),
        end_date=_merge_end_date(batches, photos),
    )


def _merge_overall_location(batches: list[PhotoAnalysisResult]) -> str | None:
    fallback: str | None = None
    for batch in batches:
        loc = (batch.overall_location or "").strip()
        if not loc:
            continue
        if loc not in _UNKNOWN_LOCATIONS:
            return loc
        if fallback is None:
            fallback = loc
    return fallback or "未知目的地"


def _collect_date_candidates(
    batches: list[PhotoAnalysisResult], photos: list[PhotoAnalysisItem]
) -> list[str]:
    dates: list[str] = []
    for batch in batches:
        if batch.start_date:
            dates.append(batch.start_date)
        if batch.end_date:
            dates.append(batch.end_date)
    for item in photos:
        if item.taken_date_guess:
            dates.append(item.taken_date_guess)
    return dates


def _merge_start_date(
    batches: list[PhotoAnalysisResult], photos: list[PhotoAnalysisItem]
) -> str | None:
    dates = _collect_date_candidates(batches, photos)
    return min(dates) if dates else None


def _merge_end_date(
    batches: list[PhotoAnalysisResult], photos: list[PhotoAnalysisItem]
) -> str | None:
    dates = _collect_date_candidates(batches, photos)
    return max(dates) if dates else None


def select_postcard_photos(
    *,
    analysis: PhotoAnalysisResult,
    requirements: str,
    memory_summary: str,
) -> PostcardSelectionResult:
    """Choose postcard count and source photos without inventing visual ideas.

    The postcard count is decided entirely by the model from the requirement
    text per the prompt rules (≤5 → that many; >5 → 5; unspecified → 3). The
    backend passes no count and never parses it from natural language.
    """
    system_prompt = load_prompt("postcard_prompt_system.md")
    usable = [p for p in analysis.photos if p.suitability != "unsuitable"] or list(
        analysis.photos
    )
    filtered_analysis = PhotoAnalysisResult(
        photos=usable,
        overall_location=analysis.overall_location,
        start_date=analysis.start_date,
        end_date=analysis.end_date,
    )
    user_text = context_builder.build_postcard_selection_user_text(
        analysis=filtered_analysis,
        requirements=requirements,
        memory_summary=memory_summary,
    )
    allowed_asset_ids = {item.asset_id for item in usable}

    def _validate(result: PostcardSelectionResult) -> None:
        if not 1 <= len(result.items) <= 5:
            raise AIGenerationError("AI 生成失败：明信片选图数量不合法")
        for item in result.items:
            if not 1 <= len(item.source_asset_ids) <= 2:
                raise AIGenerationError("AI 生成失败：明信片参考照片数量不合法")
            if len(set(item.source_asset_ids)) != len(item.source_asset_ids):
                raise AIGenerationError("AI 生成失败：明信片参考照片重复")
            if any(asset_id not in allowed_asset_ids for asset_id in item.source_asset_ids):
                raise AIGenerationError("AI 生成失败：明信片参考照片非候选照片")

    with timed_stage("orchestrator_postcard_selection_model", usable_photos=len(usable)):
        return _call_postcard_structured_model(
            task=vivo_chat_client.TASK_POSTCARD_SELECTION,
            system_prompt=system_prompt,
            user_text=user_text,
            result_type=PostcardSelectionResult,
            validate=_validate,
            max_completion_tokens=4000,
        )


def create_postcard_creative_plan(
    *,
    analysis: PhotoAnalysisResult,
    selection: PostcardSelectionResult,
    selected_asset_ids: list[str],
    image_data_urls: list[str],
    requirements: str,
    memory_summary: str,
) -> PostcardPlanResult:
    """Use the selected original images to create Seedream-ready directions."""
    if len(selected_asset_ids) != len(image_data_urls) or not selected_asset_ids:
        raise AIGenerationError("AI 生成失败：明信片创意阶段原图不完整")

    selected_set = set(selected_asset_ids)
    selected_analysis = PhotoAnalysisResult(
        photos=[item for item in analysis.photos if item.asset_id in selected_set],
        overall_location=analysis.overall_location,
        start_date=analysis.start_date,
        end_date=analysis.end_date,
    )
    user_text = context_builder.build_postcard_creative_user_text(
        analysis=selected_analysis,
        selection=selection,
        selected_asset_ids=selected_asset_ids,
        requirements=requirements,
        memory_summary=memory_summary,
    )
    expected_sources = [list(item.source_asset_ids) for item in selection.items]
    system_prompt = load_prompt("postcard_creative_system.md")
    with timed_stage(
        "orchestrator_postcard_creative_model",
        postcard_count=len(selection.items),
        selected_photo_count=len(selected_asset_ids),
    ):
        raw = vivo_chat_client.chat_json(
            task=vivo_chat_client.TASK_POSTCARD_CREATIVE,
            system_prompt=system_prompt,
            user_text=user_text,
            image_data_urls=image_data_urls,
            temperature=0.7,
            max_completion_tokens=6000,
        )
        candidates, errors = _parse_postcard_plan_candidates(
            raw, expected_count=len(expected_sources)
        )
        _collect_postcard_plan_validation_errors(
            candidates=candidates,
            expected_sources=expected_sources,
            errors=errors,
        )
        if errors:
            data_url_by_asset = dict(zip(selected_asset_ids, image_data_urls, strict=True))
            for index in sorted(errors):
                candidates[index] = _retry_postcard_creative_item(
                    index=index,
                    validation_errors=errors[index],
                    analysis=selected_analysis,
                    selection=selection,
                    requirements=requirements,
                    memory_summary=memory_summary,
                    data_url_by_asset=data_url_by_asset,
                    system_prompt=system_prompt,
                )

        result_items = [item for item in candidates if item is not None]
        if len(result_items) != len(expected_sources):
            raise AIGenerationError("明信片创意定向重试后仍有缺失项")
        final_errors: dict[int, list[str]] = {}
        _collect_postcard_plan_validation_errors(
            candidates=result_items,
            expected_sources=expected_sources,
            errors=final_errors,
        )
        if final_errors:
            details = _format_postcard_plan_errors(final_errors)
            raise AIGenerationError(f"明信片创意定向重试后仍不合法：{details}")
        return PostcardPlanResult(items=result_items)


def _parse_postcard_plan_candidates(
    raw: str,
    *,
    expected_count: int,
) -> tuple[list[PostcardPlanItem | None], dict[int, list[str]]]:
    """Parse each creative item independently so valid siblings remain reusable."""
    try:
        data = json.loads(output_parser.extract_first_json(raw))
    except (json.JSONDecodeError, AIGenerationError) as exc:
        raise AIGenerationError("明信片创意整体 JSON 无法解析，无法定向重试") from exc
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        raise AIGenerationError("明信片创意 items 不是数组，无法定向重试")

    candidates: list[PostcardPlanItem | None] = []
    errors: dict[int, list[str]] = {}
    for index in range(expected_count):
        if index >= len(raw_items):
            candidates.append(None)
            errors[index] = ["模型未返回该明信片创意"]
            continue
        try:
            candidates.append(PostcardPlanItem.model_validate(raw_items[index]))
        except ValidationError as exc:
            candidates.append(None)
            errors[index] = [_summarize_validation_error(exc)]

    if len(raw_items) > expected_count:
        log_event(
            "postcard_creative_extra_items",
            status="ignored",
            expected_count=expected_count,
            returned_count=len(raw_items),
            ignored_count=len(raw_items) - expected_count,
        )
    return candidates, errors


def _collect_postcard_plan_validation_errors(
    *,
    candidates: list[PostcardPlanItem | None],
    expected_sources: list[list[str]],
    errors: dict[int, list[str]],
) -> None:
    """Collect item-local and cross-item errors without discarding valid items."""
    seen_titles: dict[str, int] = {}
    for index, expected in enumerate(expected_sources):
        item = candidates[index]
        if item is None:
            continue
        item_errors = _postcard_plan_item_errors(item, expected)
        title = item.title.strip()
        if title in seen_titles:
            item_errors.append(f"标题与第 {seen_titles[title] + 1} 张重复")
        else:
            seen_titles[title] = index
        if item_errors:
            errors.setdefault(index, []).extend(item_errors)


def _postcard_plan_item_errors(
    item: PostcardPlanItem,
    expected_source_asset_ids: list[str],
) -> list[str]:
    errors: list[str] = []
    if item.source_asset_ids != expected_source_asset_ids:
        errors.append(
            "source_asset_ids 必须为 "
            + json.dumps(expected_source_asset_ids, ensure_ascii=False)
        )
    title = item.title.strip()
    if not 2 <= len(title) <= 12:
        errors.append("标题长度必须为 2–12 个字")
    if len(item.extra_texts) > 2 or any(not text.strip() for text in item.extra_texts):
        errors.append("extra_texts 必须为 0–2 条非空文案")
    structured_fields = (
        ("design_concept", item.design_concept, 12, 60),
        ("photo_transformation", item.photo_transformation, 15, 100),
        ("visual_device", item.visual_device, 10, 80),
        ("typography", item.typography, 15, 100),
    )
    for name, value, minimum, maximum in structured_fields:
        if not minimum <= len(value.strip()) <= maximum:
            errors.append(f"{name} 长度必须为 {minimum}–{maximum} 个字")
    image_prompt = item.image_prompt.strip()
    if not 60 <= len(image_prompt) <= 180:
        errors.append("image_prompt 长度必须为 60–180 个字")
    if "明信片设计" not in image_prompt:
        errors.append('image_prompt 必须包含“明信片设计”')
    return errors


def _retry_postcard_creative_item(
    *,
    index: int,
    validation_errors: list[str],
    analysis: PhotoAnalysisResult,
    selection: PostcardSelectionResult,
    requirements: str,
    memory_summary: str,
    data_url_by_asset: dict[str, str],
    system_prompt: str,
) -> PostcardPlanItem:
    """Regenerate one invalid postcard creative exactly once."""
    selected = selection.items[index]
    source_asset_ids = list(selected.source_asset_ids)
    source_set = set(source_asset_ids)
    item_analysis = PhotoAnalysisResult(
        photos=[item for item in analysis.photos if item.asset_id in source_set],
        overall_location=analysis.overall_location,
        start_date=analysis.start_date,
        end_date=analysis.end_date,
    )
    item_selection = PostcardSelectionResult(items=[selected])
    retry_user_text = context_builder.build_postcard_creative_user_text(
        analysis=item_analysis,
        selection=item_selection,
        selected_asset_ids=source_asset_ids,
        requirements=requirements,
        memory_summary=memory_summary,
    )
    error_text = "；".join(validation_errors)
    retry_user_text += (
        f"\n\n【定向纠错】原第 {index + 1} 张明信片创意未通过校验："
        f"{error_text}。请只重新生成这一张，items 必须恰好包含 1 项。"
    )
    with timed_stage(
        "postcard_creative_item_retry",
        index=index,
        source_asset_ids=source_asset_ids,
        validation_errors=validation_errors,
        attempt=2,
        max_attempts=2,
    ):
        raw = vivo_chat_client.chat_json(
            task=vivo_chat_client.TASK_POSTCARD_CREATIVE,
            system_prompt=system_prompt,
            user_text=retry_user_text,
            image_data_urls=[
                data_url_by_asset[asset_id] for asset_id in source_asset_ids
            ],
            temperature=0,
            max_completion_tokens=2000,
        )
        candidates, parse_errors = _parse_postcard_plan_candidates(
            raw, expected_count=1
        )
        item = candidates[0]
        retry_errors = list(parse_errors.get(0, []))
        if item is not None:
            retry_errors.extend(_postcard_plan_item_errors(item, source_asset_ids))
        if item is None or retry_errors:
            raise AIGenerationError(
                f"第 {index + 1} 张明信片创意定向重试失败："
                + "；".join(retry_errors)
            )
        return item


def _summarize_validation_error(exc: ValidationError) -> str:
    summaries: list[str] = []
    for error in exc.errors()[:5]:
        location = ".".join(str(part) for part in error.get("loc", ())) or "item"
        summaries.append(f"{location}: {error.get('msg', '字段不合法')}")
    return "；".join(summaries) or "item 结构不合法"


def _format_postcard_plan_errors(errors: dict[int, list[str]]) -> str:
    return "；".join(
        f"第 {index + 1} 张：" + "、".join(messages)
        for index, messages in sorted(errors.items())
    )


def _call_postcard_structured_model(
    *,
    task: str,
    system_prompt: str,
    user_text: str,
    result_type: type[StructuredResultT],
    validate: Callable[[StructuredResultT], None],
    image_data_urls: list[str] | None = None,
    temperature: float = 0.2,
    max_completion_tokens: int = 8000,
) -> StructuredResultT:
    """Parse and validate a postcard JSON response, retrying it once on error."""
    attempt_user_text = user_text
    for json_attempt in range(2):
        raw = vivo_chat_client.chat_json(
            task=task,
            system_prompt=system_prompt,
            user_text=attempt_user_text,
            image_data_urls=image_data_urls,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
        )
        try:
            result = output_parser.parse_model_json(raw, result_type)
            validate(result)
            return result
        except AIGenerationError as exc:
            if json_attempt == 1:
                raise
            log_event(
                "postcard_structured_json_retry",
                status="retry",
                detail_task=task,
                reason=type(exc).__name__,
            )
            attempt_user_text = (
                user_text
                + "\n\n【返回纠错】上一次输出未通过 JSON 结构或业务字段校验。"
                f"具体错误：{exc}。请重新检查所有字段，只输出完全符合系统要求的 JSON 对象。"
            )
    raise AIGenerationError("AI 生成失败：明信片结构化结果无效")


def draft_report(
    *,
    analysis: PhotoAnalysisResult,
    requirements: str,
    memory_summary: str,
) -> ReportDraftResult:
    """Report draft → ReportDraftResult."""
    system_prompt = load_prompt("report_system.md")
    user_text = context_builder.build_report_draft_user_text(
        analysis=analysis,
        requirements=requirements,
        memory_summary=memory_summary,
    )
    with timed_stage("orchestrator_report_model"):
        raw = vivo_chat_client.chat_json(
            task=vivo_chat_client.TASK_REPORT_DRAFT,
            system_prompt=system_prompt,
            user_text=user_text,
        )
        return output_parser.parse_model_json(raw, ReportDraftResult)


def render_postcard_image(
    *,
    prompt: str,
    image_data_urls: list[str],
    design_concept: str,
    photo_transformation: str,
    visual_device: str,
    typography: str,
    title: str,
    extra_texts: list[str],
) -> ImageGenerationResult:
    """Generate one postcard image (image-to-image)."""
    edit_instruction = prompt.strip()
    image_model_prompt = _compose_postcard_image_prompt(
        prompt=edit_instruction,
        design_concept=design_concept,
        photo_transformation=photo_transformation,
        visual_device=visual_device,
        typography=typography,
        title=title,
        extra_texts=extra_texts,
    )
    log_event(
        "postcard_image_prompt",
        status="ready",
        title=title.strip(),
        extra_texts=[text.strip() for text in extra_texts],
        design_concept=design_concept.strip(),
        photo_transformation=photo_transformation.strip(),
        visual_device=visual_device.strip(),
        typography=typography.strip(),
        edit_instruction=edit_instruction,
        image_model_prompt=image_model_prompt,
        prompt_chars=len(image_model_prompt),
        reference_image_count=len(image_data_urls),
    )
    try:
        return vivo_image_client.generate_image(
            prompt=image_model_prompt, image_data_urls=image_data_urls
        )
    except ImageInputPolicyError:
        sanitized_prompt = _sanitized_postcard_image_prompt()
        log_event(
            "postcard_image_input_policy_retry",
            status="retry",
            title=title.strip(),
            reason="input_policy_violation",
            original_prompt_chars=len(image_model_prompt),
            sanitized_prompt=sanitized_prompt,
            sanitized_prompt_chars=len(sanitized_prompt),
            reference_image_count=len(image_data_urls),
            attempt=2,
            max_attempts=2,
        )
        return vivo_image_client.generate_image(
            prompt=sanitized_prompt,
            image_data_urls=image_data_urls,
        )


def _sanitized_postcard_image_prompt() -> str:
    """Return a minimal fallback prompt for one input-policy retry."""
    return (
        "Create a tasteful travel postcard edit from the provided image. "
        "Preserve the original scene, subjects, composition, and factual content. "
        "Apply only mild color balancing, soft natural light, a very thin border, "
        "and one small circular postal cancellation mark near an outer edge. "
        "Do not add or alter people, identities, symbols, logos, flags, sensitive "
        "content, or readable text. Keep the result natural, restrained, and safe."
    )


def _compose_postcard_image_prompt(
    *,
    prompt: str,
    design_concept: str,
    photo_transformation: str,
    visual_device: str,
    typography: str,
    title: str,
    extra_texts: list[str],
) -> str:
    """Compose a compact, fully art-directed Seedream instruction sheet."""
    quoted_extra_texts = "、".join(f"“{text.strip()}”" for text in extra_texts)
    text_lines = [f"主标题：“{title.strip()}”"]
    if quoted_extra_texts:
        text_lines.append(f"辅助文案：{quoted_extra_texts}")
    return (
        load_prompt("postcard_skill.md").strip()
        + "\n\n【创意方案】"
        + f"\n核心概念：{design_concept.strip()}"
        + f"\n原图改造：{photo_transformation.strip()}"
        + f"\n视觉记忆点：{visual_device.strip()}"
        + f"\n字体与层级：{typography.strip()}"
        + "\n【准确呈现的文字】\n"
        + "\n".join(text_lines)
        + f"\n【Seedream执行指令】\n{prompt.strip()}"
    )


def propose_memory_update(
    *,
    source_task: str,
    location: str | None,
    evidence_summary: str,
) -> MemoryUpdateResult:
    """Generate a structured implicit-traits增量建议."""
    system_prompt = load_prompt("memory_update_system.md")
    with timed_stage("orchestrator_memory_update_model", source_task=source_task):
        raw = vivo_chat_client.chat_json(
            task=vivo_chat_client.TASK_MEMORY_UPDATE,
            system_prompt=system_prompt,
            user_text=evidence_summary,
        )
        return output_parser.parse_model_json(raw, MemoryUpdateResult)


def plan_itinerary(
    *,
    message: str,
    context: ItineraryData | None,
    memory_summary: str,
    fact_pack: dict[str, Any] | None = None,
    execute_tool: ToolExecutor | None = None,
    planning_model: PlanningModel = DEFAULT_PLANNING_MODEL,
) -> ItineraryData:
    """Planning → full ItineraryData.

    With `execute_tool`, run a bounded function-calling loop so the model can
    choose the model-specific whitelist tools and
    synthesize the itinerary from returned facts. The orchestrator intercepts
    each function call and routes it through the injected executor (which
    validates + executes via travel_fact_service). Falls back to the plain JSON
    path with the baseline fact pack on any function-calling failure.
    """
    planning_progress.report("understanding_request", planning_model=planning_model)
    system_prompt = _load_planning_system_prompt(planning_model)
    user_text = context_builder.build_planning_user_text(
        message=message,
        context=context,
        memory_summary=memory_summary,
        fact_pack=fact_pack,
    )
    if execute_tool is not None:
        return _plan_with_tools(
            system_prompt,
            user_text,
            execute_tool,
            planning_model=planning_model,
        )

    return _plan_plain(system_prompt, user_text, planning_model=planning_model)


def _load_planning_system_prompt(planning_model: PlanningModel) -> str:
    """Load planning contract and quality skill as one system prompt."""
    current_date = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    prompt = (
        f"【当前日期】\n今天是 {current_date}（北京时间）。"
        "用户的日期表达可能较为模糊，请根据当前日期和用户需求推断具体行程日期；"
        "需要推断时，推断结果不得早于今天。\n\n"
        "【planning_system.md：必须遵守的契约】\n"
        + load_prompt("planning_system.md")
        + "\n\n【planning_skill.md：好旅行规划的标准】\n"
        + load_prompt("planning_skill.md")
    )
    if planning_model in DEEPSEEK_PLANNING_MODELS:
        prompt += (
            "\n\n【DeepSeek 专属航班工具规则】\n"
            + load_prompt("planning_flight_tools_deepseek.md")
            + "\n\n【DeepSeek 专属每日游玩地图标注】\n"
            + load_prompt("planning_daily_map_deepseek.md")
        )
    return prompt


def _assistant_history_message(
    turn: vivo_chat_client.ChatTurn,
    planning_model: PlanningModel,
) -> dict[str, Any]:
    """Serialize an assistant turn for the next provider request.

    DeepSeek requires the complete `reasoning_content` to be replayed after
    every request carrying tools. The field remains provider-only: it is never
    logged as text, persisted, or returned through application DTOs.
    """
    message: dict[str, Any] = {
        "role": "assistant",
        "content": turn.content,
    }
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in turn.tool_calls
        ]
    if requires_reasoning_replay(planning_model):
        message["reasoning_content"] = turn.reasoning_content or ""
    return message


def _compact_tool_history(
    messages: list[dict[str, Any]],
    cutoff_index: int,
    *,
    retained_fact_ids: set[str] | None = None,
) -> dict[str, int] | None:
    """Digest or discard tool results older than the cutoff, in place.

    - Retained / not-yet-selected facts shrink to a digest (tool + status + ids).
    - Facts the model has already deselected via update_planning_fact_state are
      dropped entirely: their payloads are confirmed unused for the plan.
    - System / user / assistant turns are never rewritten. In particular the
      confirmation checklist user message must survive every research round.
    """
    digested = 0
    discarded = 0
    chars_saved = 0
    for message in messages[:cutoff_index]:
        role = message.get("role")
        if role != "tool":
            # Checklist and other requirement text live on the user turn.
            continue
        content = message.get("content") or ""
        if (
            _TOOL_HISTORY_DIGEST_KEY in content
            or _TOOL_HISTORY_DISCARDED_KEY in content
        ):
            continue
        fact_ids = _fact_ids_from_tool_content(content)
        if retained_fact_ids is not None and fact_ids and not (
            set(fact_ids) & retained_fact_ids
        ):
            replacement = _tool_history_discard(content)
            if replacement is None or len(replacement) >= len(content):
                continue
            discarded += 1
            chars_saved += len(content) - len(replacement)
            message["content"] = replacement
            continue
        digest = _tool_history_digest(content)
        if digest is None or len(digest) >= len(content):
            continue
        digested += 1
        chars_saved += len(content) - len(digest)
        message["content"] = digest
    if not digested and not discarded:
        return None
    return {
        "messages": digested + discarded,
        "digested": digested,
        "discarded": discarded,
        "chars_saved": chars_saved,
    }


def _tool_history_digest(content: str) -> str | None:
    """One line standing in for a still-useful tool result, or None."""
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    fact_ids = _fact_ids_within(payload)
    return json.dumps(
        {
            _TOOL_HISTORY_DIGEST_KEY: True,
            "tool": payload.get("tool"),
            "status": payload.get("status"),
            "factIds": fact_ids[:24],
            "factCount": len(fact_ids),
            "note": "此前轮次的详情已归档，最终编排会提供完整事实，无需重复查询",
        },
        ensure_ascii=False,
    )


def _tool_history_discard(content: str) -> str | None:
    """Stub for a tool result the model has already marked unused."""
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return json.dumps(
        {
            _TOOL_HISTORY_DISCARDED_KEY: True,
            "tool": payload.get("tool"),
            "status": payload.get("status"),
            "note": "该查询结果未被选入保留事实，详情已丢弃",
        },
        ensure_ascii=False,
    )


def _fact_ids_from_tool_content(content: str) -> list[str]:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return []
    return _fact_ids_within(payload) if isinstance(payload, dict) else []


def _fact_ids_within(node: Any) -> list[str]:
    """Every fact id in a tool result, top level or nested in its items."""
    found: list[str] = []
    if isinstance(node, dict):
        fact_id = node.get("fact_id")
        if isinstance(fact_id, str):
            found.append(fact_id)
        for value in node.values():
            found.extend(_fact_ids_within(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_fact_ids_within(item))
    return list(dict.fromkeys(found))


def _ensure_confirmation_checklist_preserved(
    messages: list[dict[str, Any]],
    user_text: str,
) -> None:
    """If compaction ever lost the checklist user turn, put it back.

    Research only digests tool roles today, but the checklist is the one piece of
    context that must survive every round — without it the model plans from
    destinations and dates alone.
    """
    if _CONFIRMATION_CHECKLIST_MARKER not in user_text:
        return
    for message in messages:
        if (
            message.get("role") == "user"
            and _CONFIRMATION_CHECKLIST_MARKER in (message.get("content") or "")
        ):
            return
    messages.insert(1, {"role": "user", "content": user_text})
    log_event(
        "planning_confirmation_checklist_restored",
        status="restored",
        reason="missing_after_compaction",
    )


def _plan_with_tools(
    system_prompt: str,
    user_text: str,
    execute_tool: ToolExecutor,
    *,
    planning_model: PlanningModel = DEFAULT_PLANNING_MODEL,
) -> ItineraryData:
    """Research protocol: declare scope → gather/select facts → finish → final + audit."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
    scope: dict[str, Any] | None = None
    research_summary: dict[str, Any] | None = None
    fact_registry: dict[str, dict[str, Any]] = {}
    retained_facts: dict[str, dict[str, Any]] = {}
    result_cache: dict[str, dict[str, Any]] = {}
    remaining_queries: list[str] = []
    external_calls = 0
    planning_tools = tool_specs.planning_tools_for_model(planning_model)
    allowed_external_tool_names = tool_specs.external_tool_names_for_model(
        planning_model
    )
    # Research depth is identical for every planning model. DeepSeek used to run
    # a 2/4-round "fast path", which force-closed research before the model had
    # verified routes or called finish_research, so the final JSON was
    # synthesized from an incomplete fact set.
    target_rounds = _TARGET_TOOL_ROUNDS
    max_rounds = _MAX_TOOL_ROUNDS
    active_round_limit = target_rounds
    forced_close_reason = "target_rounds_complete"

    round_start_indexes: list[int] = []

    for round_idx in range(max_rounds):
        if round_idx >= active_round_limit:
            break
        round_start_indexes.append(len(messages))
        if round_idx > _VERBATIM_TOOL_HISTORY_ROUNDS:
            compacted = _compact_tool_history(
                messages,
                round_start_indexes[round_idx - _VERBATIM_TOOL_HISTORY_ROUNDS],
                retained_fact_ids=(
                    set(retained_facts) if retained_facts else None
                ),
            )
            if compacted:
                log_event(
                    "planning_tool_history_compacted",
                    status="applied",
                    round=round_idx + 1,
                    digested_messages=compacted["messages"],
                    digested=compacted.get("digested"),
                    discarded=compacted.get("discarded"),
                    chars_saved=compacted["chars_saved"],
                    planning_model=planning_model,
                )
            _ensure_confirmation_checklist_preserved(messages, user_text)
        planning_progress.report(
            "researching",
            research_round=round_idx + 1,
            target_rounds=active_round_limit,
            max_rounds=max_rounds,
            fact_count=len(fact_registry) or None,
            detail=(
                f"第 {round_idx + 1}/{active_round_limit} 轮事实检索"
            ),
        )
        log_event(
            "planning_tool_round",
            round=round_idx + 1,
            target_rounds=target_rounds,
            max_rounds=max_rounds,
            active_round_limit=active_round_limit,
            remaining_rounds_including_current=active_round_limit - round_idx,
            total_calls=external_calls,
            max_external_calls=_MAX_TOOL_CALLS_TOTAL,
            message_count=len(messages),
            scope_declared=scope is not None,
            fact_registry_count=len(fact_registry),
            retained_fact_count=len(retained_facts),
            remaining_query_count=len(remaining_queries),
            remaining_queries=remaining_queries,
            cache_entry_count=len(result_cache),
            research_finished=research_summary is not None,
            planning_model=planning_model,
        )
        turn = vivo_chat_client.chat_messages(
            messages=messages,
            tools=planning_tools,
            stage="planning_research",
            max_completion_tokens=_MAX_GENERATION_TOKENS,
            planning_model=planning_model,
        )
        log_event(
            "planning_tool_round_result",
            status="tool_calls" if turn.tool_calls else "premature_content",
            round=round_idx + 1,
            remaining_rounds=active_round_limit - round_idx - 1,
            tool_call_count=len(turn.tool_calls),
            tool_names=[tc.name for tc in turn.tool_calls],
            internal_tool_count=sum(
                _planning_tool_kind(tc.name, allowed_external_tool_names) == "internal"
                for tc in turn.tool_calls
            ),
            external_tool_count=sum(
                tc.name in allowed_external_tool_names for tc in turn.tool_calls
            ),
            content_chars=len(turn.content or ""),
            content_excerpt=_excerpt(turn.content or ""),
            scope_declared=scope is not None,
            fact_registry_count=len(fact_registry),
            retained_fact_count=len(retained_facts),
            remaining_query_count=len(remaining_queries),
            planning_model=planning_model,
        )
        if not turn.tool_calls:
            messages.append(_assistant_history_message(turn, planning_model))
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "研究尚未通过 finish_research 结束，提前输出的行程不会被接受。"
                        "请继续调用所需工具；信息足够后先调用 finish_research。"
                    ),
                }
            )
            continue

        external_in_turn = any(
            tc.name in allowed_external_tool_names for tc in turn.tool_calls
        )
        messages.append(_assistant_history_message(turn, planning_model))
        parsed_calls = [(tc, _safe_json_args(tc.arguments)) for tc in turn.tool_calls]
        allowed_external_ids: set[str] = set()
        batch_requests: dict[str, tuple[str, dict[str, Any]]] = {}
        new_request_keys: set[str] = set()
        for tc, args in parsed_calls:
            if tc.name not in allowed_external_tool_names:
                continue
            cache_key = _planning_tool_cache_key(tc.name, args)
            if cache_key in result_cache or cache_key in batch_requests:
                allowed_external_ids.add(tc.id)
                continue
            if external_calls + len(new_request_keys) >= _MAX_TOOL_CALLS_TOTAL:
                continue
            allowed_external_ids.add(tc.id)
            new_request_keys.add(cache_key)
            batch_requests[cache_key] = (tc.name, args)
        parallel_results = _execute_external_batch(
            batch_requests,
            execute_tool,
            max_total_attempts=max(0, _MAX_TOOL_CALLS_TOTAL - external_calls),
        )
        external_calls += sum(
            int(result.get("_attempt_count") or 0)
            for result in parallel_results.values()
        )
        called_names: list[str] = []
        for call_index, (tc, args) in enumerate(parsed_calls, start=1):
            called_names.append(tc.name)
            tool_kind = _planning_tool_kind(tc.name, allowed_external_tool_names)
            cache_hit = False
            unknown_ids: list[str] = []
            fact_ids_before = set(fact_registry)
            log_event(
                "planning_execute_tool",
                status="start",
                round=round_idx + 1,
                call_index=call_index,
                tool_name=tc.name,
                tool_kind=tool_kind,
                arguments=_summarize_planning_value(args),
                external_calls_before=external_calls,
                fact_registry_count=len(fact_registry),
                retained_fact_count=len(retained_facts),
            )
            result: dict[str, Any]
            if tc.name == tool_specs.TOOL_DECLARE_TRIP_SCOPE:
                try:
                    parsed_scope = tool_specs.DeclareTripScopeArgs.model_validate(args)
                    scope = parsed_scope.model_dump(by_alias=True)
                    result = {"tool": tc.name, "status": "ok", "saved": scope}
                except ValidationError as exc:
                    result = {
                        "tool": tc.name,
                        "status": "error",
                        "message": f"旅行范围参数不合法：{exc.errors()[:1]}",
                    }
            elif tc.name == tool_specs.TOOL_UPDATE_PLANNING_FACT_STATE:
                try:
                    state = tool_specs.PlanningFactStateArgs.model_validate(args)
                    unknown_ids = [
                        fact_id
                        for fact_id in state.selected_fact_ids
                        if fact_id not in fact_registry
                    ]
                    retained_facts = {
                        fact_id: fact_registry[fact_id]
                        for fact_id in state.selected_fact_ids
                        if fact_id in fact_registry
                    }
                    remaining_queries = state.remaining_queries
                    # Once the model names what it is keeping, everything else is
                    # confirmed unused and can leave the prompt entirely.
                    discarded = _compact_tool_history(
                        messages,
                        len(messages),
                        retained_fact_ids=set(retained_facts),
                    )
                    if discarded and discarded.get("discarded"):
                        log_event(
                            "planning_tool_history_compacted",
                            status="discarded_unused",
                            discarded=discarded["discarded"],
                            chars_saved=discarded["chars_saved"],
                            retained_fact_count=len(retained_facts),
                            planning_model=planning_model,
                        )
                    _ensure_confirmation_checklist_preserved(messages, user_text)
                    result = {
                        "tool": tc.name,
                        "status": "ok" if not unknown_ids else "partial",
                        "retainedFactIds": list(retained_facts),
                        "unknownFactIds": unknown_ids,
                        "remainingQueries": remaining_queries,
                    }
                except ValidationError as exc:
                    result = {
                        "tool": tc.name,
                        "status": "error",
                        "message": f"事实状态参数不合法：{exc.errors()[:1]}",
                    }
            elif tc.name == tool_specs.TOOL_FINISH_RESEARCH:
                try:
                    finish = tool_specs.FinishResearchArgs.model_validate(args)
                    if scope is None:
                        result = {
                            "tool": tc.name,
                            "status": "error",
                            "message": "必须先调用 declare_trip_scope",
                        }
                    elif external_in_turn:
                        result = {
                            "tool": tc.name,
                            "status": "error",
                            "message": "本轮仍有外部查询；请读取结果后下一轮再结束研究",
                        }
                    else:
                        finish_gaps = _critical_research_gaps(
                            scope=scope,
                            remaining_queries=remaining_queries,
                            fact_registry=fact_registry,
                        )
                        if finish_gaps and round_idx + 1 < max_rounds:
                            remaining_queries = list(
                                dict.fromkeys([*remaining_queries, *finish_gaps])
                            )
                            result = {
                                "tool": tc.name,
                                "status": "error",
                                "message": "关键事实仍缺失，请只补查后再结束研究",
                                "criticalGaps": finish_gaps,
                            }
                        else:
                            research_summary = finish.model_dump()
                            result = {
                                "tool": tc.name,
                                "status": "ok",
                                "toolsClosed": True,
                            }
                except ValidationError as exc:
                    result = {
                        "tool": tc.name,
                        "status": "error",
                        "message": f"研究总结参数不合法：{exc.errors()[:1]}",
                    }
            elif tc.name in allowed_external_tool_names:
                if tc.id not in allowed_external_ids:
                    result = {
                        "tool": tc.name,
                        "status": "error",
                        "message": "已达到外部工具调用总上限",
                    }
                else:
                    cache_key = _planning_tool_cache_key(tc.name, args)
                    cached = result_cache.get(cache_key)
                    if cached is not None:
                        cache_hit = True
                        result = deepcopy(cached)
                        result["cached"] = True
                    else:
                        result = deepcopy(parallel_results[cache_key])
                        result.pop("_attempt_count", None)
                        retryable, _ = _classify_tool_retry(result)
                        if _tool_result_has_usable_fact(result) and not retryable:
                            result = _register_planning_facts(
                                tool_name=tc.name,
                                arguments=args,
                                result=result,
                                registry=fact_registry,
                            )
                        if _cacheable_tool_result(result):
                            result_cache[cache_key] = deepcopy(result)
            else:
                result = {
                    "tool": tc.name,
                    "status": "error",
                    "message": "工具不在本轮规划白名单中",
                }

            issued_fact_ids = sorted(set(fact_registry) - fact_ids_before)
            log_event(
                "planning_execute_tool",
                status=str(result.get("status") or "unknown"),
                round=round_idx + 1,
                call_index=call_index,
                tool_name=tc.name,
                tool_kind=tool_kind,
                cache_hit=cache_hit,
                arguments=args,
                result=result,
                issued_fact_count=len(issued_fact_ids),
                issued_fact_ids=issued_fact_ids,
                external_calls=external_calls,
                scope_summary=_summarize_planning_value(scope),
                fact_registry_count=len(fact_registry),
                retained_fact_count=len(retained_facts),
                retained_fact_ids=list(retained_facts),
                unknown_fact_ids=unknown_ids,
                remaining_queries=remaining_queries,
                research_finished=research_summary is not None,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        _compact_planning_fact_value(result),
                        ensure_ascii=False,
                    ),
                }
            )

        if research_summary is not None:
            break
        completed_rounds = round_idx + 1
        if completed_rounds == target_rounds:
            critical_gaps = _critical_research_gaps(
                scope=scope,
                remaining_queries=remaining_queries,
                fact_registry=fact_registry,
            )
            if (
                critical_gaps
                and external_calls < _MAX_TOOL_CALLS_TOTAL
            ):
                active_round_limit = max_rounds
                forced_close_reason = "max_rounds_reached_with_critical_gaps"
                log_event(
                    "planning_research_rounds_extended",
                    status="success",
                    completed_rounds=completed_rounds,
                    previous_limit=target_rounds,
                    extended_limit=max_rounds,
                    critical_gaps=critical_gaps,
                    external_calls=external_calls,
                    planning_model=planning_model,
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"默认 {target_rounds} 轮研究已完成，但以下关键事实仍缺失，"
                            f"因此允许延长到 {max_rounds} 轮：{'; '.join(critical_gaps)}。"
                            "请只补查这些关键缺口，完成后立即调用 finish_research。"
                        ),
                    }
                )
            else:
                forced_close_reason = (
                    "tool_call_budget_exhausted"
                    if external_calls >= _MAX_TOOL_CALLS_TOTAL
                    else "target_rounds_complete"
                )
        remaining_rounds = active_round_limit - round_idx - 1
        messages.append(
            {
                "role": "user",
                "content": (
                    f"本轮已调用：{', '.join(called_names)}。"
                    f"还剩 {remaining_rounds} 个研究轮次，已执行 {external_calls} 次外部调用。"
                    "继续用 remainingQueries 驱动查询；不要提前输出最终行程。"
                ),
            }
        )

    if scope is None:
        raise AIGenerationError("行程规划生成失败：模型未声明旅行范围")
    if research_summary is None:
        if not fact_registry:
            raise AIGenerationError("行程规划生成失败：模型未调用 finish_research")
        research_summary = {
            "completed": [
                f"已取得 {len(fact_registry)} 条工具事实",
                f"已执行 {external_calls} 次外部工具调用",
            ],
            "unresolved": remaining_queries,
            "outline": [],
            "forcedClose": True,
            "reason": "达到研究轮次上限，编排器关闭工具并进入结构化生成",
        }
        log_event(
            "planning_research_forced_close",
            status="success",
            reason=forced_close_reason,
            target_rounds=target_rounds,
            max_rounds=max_rounds,
            external_calls=external_calls,
            fact_registry_count=len(fact_registry),
            retained_fact_count=len(retained_facts),
            remaining_queries=remaining_queries,
            planning_model=planning_model,
        )

    planning_progress.report(
        "selecting_facts",
        fact_count=len(fact_registry) or None,
        detail=f"已取得 {len(fact_registry)} 条工具事实",
    )
    retained_facts = _select_planning_facts_for_final(
        retained_facts or fact_registry
    )
    planning_progress.report(
        "synthesizing",
        fact_count=len(retained_facts) or None,
        detail=f"依据 {len(retained_facts)} 条事实编排行程",
    )
    final_data = _generate_itinerary_from_research(
        system_prompt=system_prompt,
        user_text=user_text,
        scope=scope,
        retained_facts=retained_facts,
        research_summary=research_summary,
        stage="planning_final",
        planning_model=planning_model,
    )
    # An advisory LLM audit used to run here and could trigger a full
    # regeneration, costing 3–7 minutes for a second opinion on requirement
    # coverage and pacing. Truthfulness — the part that makes a plan unusable —
    # is enforced deterministically below and needs no model round, so the audit
    # was removed rather than kept as the single largest cost in the pipeline.
    planning_progress.report("verifying")
    return _repair_itinerary_violations(
        system_prompt=system_prompt,
        user_text=user_text,
        itinerary=final_data,
        retained_facts=retained_facts,
        planning_model=planning_model,
    )


def collect_planning_requirements(
    *,
    messages: list[PlanningChatMessage],
    previous_brief: PlanningBrief | None,
    memory_summary: str,
    execute_tool: ToolExecutor | None = None,
    planning_model: PlanningModel = DEFAULT_PLANNING_MODEL,
) -> PlanningIntakeResult:
    """Run one intake turn, optionally using bounded read-only travel tools."""
    current_date = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    system_prompt = (
        f"【当前日期】\n今天是 {current_date}（北京时间）。\n\n"
        + load_prompt("planning_intake_system.md")
    )
    deepseek_fast_intake = planning_model in DEEPSEEK_PLANNING_MODELS
    bounded_messages = [
        {
            "role": message.role,
            "content": message.content.strip()[:1600],
        }
        for message in messages[-16:]
        if message.content.strip()
    ]
    user_text = json.dumps(
        {
            "memorySummary": memory_summary[:1600],
            "previousBrief": (
                previous_brief.model_dump(by_alias=True)
                if previous_brief is not None
                else None
            ),
            "conversation": bounded_messages,
        },
        ensure_ascii=False,
    )
    if deepseek_fast_intake:
        compact_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"{user_text}\n\n本阶段只抽取和确认旅行需求，不调用外部工具。"
                    "必须输出完整 PlanningIntakeResult JSON，包含 assistantMessage 和 "
                    "brief；未知字段使用 null 或空数组。"
                ),
            },
        ]
        with timed_stage(
            "planning_intake_model",
            conversation_turns=len(bounded_messages),
            has_previous_brief=previous_brief is not None,
            tools_enabled=False,
            fast_path=True,
            planning_model=planning_model,
        ):
            turn = vivo_chat_client.chat_messages(
                messages=compact_messages,
                tools=None,
                stage="planning_intake_compact",
                temperature=0.0,
                max_completion_tokens=4000,
                planning_model=planning_model,
                response_format={"type": "json_object"},
                thinking_enabled=False,
            )
            if not turn.content:
                raise AIGenerationError("旅行需求沟通失败：模型未返回内容")
            try:
                return output_parser.parse_model_json(
                    turn.content,
                    PlanningIntakeResult,
                )
            except AIGenerationError as exc:
                error_detail = _model_output_error_detail(exc, turn.content)
                compact_messages.extend(
                    [
                        _assistant_history_message(turn, planning_model),
                        {
                            "role": "user",
                            "content": (
                                "只修复 PlanningIntakeResult 结构；必须同时包含 "
                                "assistantMessage 和完整 brief。未知值用 null 或空数组。"
                                f"\n【错误诊断】{error_detail}"
                            ),
                        },
                    ]
                )
                repaired = vivo_chat_client.chat_messages(
                    messages=compact_messages,
                    tools=None,
                    stage="planning_intake_compact_repair",
                    temperature=0.0,
                    max_completion_tokens=4000,
                    planning_model=planning_model,
                    response_format={"type": "json_object"},
                    thinking_enabled=False,
                )
                if not repaired.content:
                    raise AIGenerationError(
                        "旅行需求沟通失败：JSON 修复未返回内容"
                    )
                return output_parser.parse_model_json(
                    repaired.content,
                    PlanningIntakeResult,
                )
    if execute_tool is None:
        with timed_stage(
            "planning_intake_model",
            conversation_turns=len(bounded_messages),
            has_previous_brief=previous_brief is not None,
            tools_enabled=False,
            planning_model=planning_model,
        ):
            raw = vivo_chat_client.chat_json(
                task=vivo_chat_client.TASK_PLANNING_INTAKE,
                system_prompt=system_prompt,
                user_text=user_text,
                temperature=0.2,
                max_completion_tokens=3000,
                planning_model=planning_model,
            )
        return output_parser.parse_model_json(raw, PlanningIntakeResult)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
    intake_tools = tool_specs.planning_intake_tools_for_model(planning_model)
    allowed_intake_tool_names = tool_specs.external_tool_names_for_model(
        planning_model
    )
    tool_calls = 0
    intake_cache: dict[str, dict[str, Any]] = {}
    with timed_stage(
        "planning_intake_model",
        conversation_turns=len(bounded_messages),
        has_previous_brief=previous_brief is not None,
        tools_enabled=True,
        planning_model=planning_model,
    ):
        for round_idx in range(_MAX_INTAKE_TOOL_ROUNDS):
            turn = vivo_chat_client.chat_messages(
                messages=messages,
                tools=intake_tools,
                stage="planning_intake",
                temperature=0.2,
                max_completion_tokens=3000,
                planning_model=planning_model,
            )
            if not turn.tool_calls:
                if not turn.content:
                    raise AIGenerationError("旅行需求沟通失败：模型未返回内容")
                try:
                    return output_parser.parse_model_json(
                        turn.content,
                        PlanningIntakeResult,
                    )
                except AIGenerationError as exc:
                    log_event(
                        "planning_intake_json_repair",
                        status="start",
                        round=round_idx + 1,
                        reason=exc.message,
                        invalid_content_chars=len(turn.content),
                        invalid_content_excerpt=_excerpt(turn.content),
                    )
                    messages.append(_assistant_history_message(turn, planning_model))
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "上一条回复不是约定的 JSON。请把上一条给用户的完整答复原样保留在 "
                                "assistantMessage 中，并依据系统提示补全累计 brief；只输出一个 JSON 对象，"
                                "不要 Markdown、代码围栏或额外说明。"
                            ),
                        }
                    )
                    repaired = vivo_chat_client.chat_messages(
                        messages=messages,
                        tools=None,
                        stage="planning_intake_json_repair",
                        temperature=0.0,
                        max_completion_tokens=4000,
                        planning_model=planning_model,
                        response_format=(
                            {"type": "json_object"}
                            if planning_model in DEEPSEEK_PLANNING_MODELS
                            else None
                        ),
                        thinking_enabled=(
                            False
                            if planning_model in DEEPSEEK_PLANNING_MODELS
                            else None
                        ),
                    )
                    if repaired.tool_calls or not repaired.content:
                        raise AIGenerationError("旅行需求沟通失败：JSON 修复未返回内容")
                    parsed = output_parser.parse_model_json(
                        repaired.content,
                        PlanningIntakeResult,
                    )
                    log_event(
                        "planning_intake_json_repair",
                        status="success",
                        repaired_content_chars=len(repaired.content),
                    )
                    return parsed

            messages.append(_assistant_history_message(turn, planning_model))
            parsed_calls = [(tc, _safe_json_args(tc.arguments)) for tc in turn.tool_calls]
            allowed_external_ids: set[str] = set()
            batch_requests: dict[str, tuple[str, dict[str, Any]]] = {}
            reserved_calls = 0
            for tc, args in parsed_calls:
                if tc.name not in allowed_intake_tool_names:
                    continue
                if tool_calls + reserved_calls >= _MAX_INTAKE_TOOL_CALLS_TOTAL:
                    continue
                reserved_calls += 1
                allowed_external_ids.add(tc.id)
                cache_key = _planning_tool_cache_key(tc.name, args)
                if cache_key not in intake_cache and cache_key not in batch_requests:
                    batch_requests[cache_key] = (tc.name, args)
            parallel_results = _execute_external_batch(batch_requests, execute_tool)
            tool_calls += reserved_calls
            for tc, args in parsed_calls:
                if tc.name not in allowed_intake_tool_names:
                    result = {
                        "tool": tc.name,
                        "status": "error",
                        "message": "工具不在需求沟通阶段白名单中",
                    }
                elif tc.id not in allowed_external_ids:
                    result = {
                        "tool": tc.name,
                        "status": "error",
                        "message": "已达到需求沟通阶段工具调用上限",
                    }
                else:
                    cache_key = _planning_tool_cache_key(tc.name, args)
                    cached = intake_cache.get(cache_key)
                    if cached is not None:
                        result = deepcopy(cached)
                        result["cached"] = True
                    else:
                        result = deepcopy(parallel_results[cache_key])
                        intake_cache[cache_key] = deepcopy(result)
                log_event(
                    "planning_intake_execute_tool",
                    status=str(result.get("status") or "unknown"),
                    round=round_idx + 1,
                    tool_name=tc.name,
                    arguments=args,
                    result=result,
                    total_calls=tool_calls,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(
                            _compact_planning_fact_value(result),
                            ensure_ascii=False,
                        ),
                    }
                )
            if tool_calls >= _MAX_INTAKE_TOOL_CALLS_TOTAL:
                break

        messages.append(
            {
                "role": "user",
                "content": (
                    "本轮需求沟通的工具查询已经结束。请结合已有对话和工具结果，"
                    "只输出约定的 PlanningIntakeResult JSON；不要生成每日行程。"
                ),
            }
        )
        final = vivo_chat_client.chat_messages(
            messages=messages,
            tools=None,
            stage="planning_intake_finalize",
            temperature=0.2,
            max_completion_tokens=3000,
            planning_model=planning_model,
            response_format=(
                {"type": "json_object"}
                if planning_model in DEEPSEEK_PLANNING_MODELS
                else None
            ),
            thinking_enabled=(
                False if planning_model in DEEPSEEK_PLANNING_MODELS else None
            ),
        )
    if not final.content:
        raise AIGenerationError("旅行需求沟通失败：模型未返回内容")
    try:
        return output_parser.parse_model_json(final.content, PlanningIntakeResult)
    except AIGenerationError as exc:
        error_detail = _model_output_error_detail(exc, final.content)
        log_event(
            "planning_intake_json_repair",
            status="start",
            path="finalize",
            reason=error_detail,
            invalid_content_chars=len(final.content),
            invalid_content_excerpt=_excerpt(final.content),
        )
        messages.extend(
            [
                _assistant_history_message(final, planning_model),
                {
                    "role": "user",
                    "content": (
                        "上一条 PlanningIntakeResult 缺少必需字段或字段类型错误。"
                        "保留 assistantMessage 的完整含义，并补全 brief 对象；brief 至少包含 "
                        "origin、destinations、startDate、endDate、travelerCount、budget、"
                        "transportPreference、lodgingPreference、interests、constraints、"
                        "assumptions、summary、detailRequirements。未知值用 null 或空数组。"
                        "detailRequirements 写结构化字段之外的时段/顺序/特殊要求。只输出一个 JSON 对象。\n"
                        f"【错误诊断】{error_detail}"
                    ),
                },
            ]
        )
        repaired = vivo_chat_client.chat_messages(
            messages=messages,
            tools=None,
            stage="planning_intake_finalize_repair",
            temperature=0.0,
            max_completion_tokens=4000,
            planning_model=planning_model,
            response_format=(
                {"type": "json_object"}
                if planning_model in DEEPSEEK_PLANNING_MODELS
                else None
            ),
            thinking_enabled=(
                False if planning_model in DEEPSEEK_PLANNING_MODELS else None
            ),
        )
        if not repaired.content:
            raise AIGenerationError("旅行需求沟通失败：JSON 修复未返回内容")
        parsed = output_parser.parse_model_json(
            repaired.content,
            PlanningIntakeResult,
        )
        log_event(
            "planning_intake_json_repair",
            status="success",
            path="finalize",
            repaired_content_chars=len(repaired.content),
        )
        return parsed


def _critical_research_gaps(
    *,
    scope: dict[str, Any] | None,
    remaining_queries: list[str],
    fact_registry: dict[str, dict[str, Any]],
) -> list[str]:
    """Return only gaps important enough to extend research past round five."""
    if scope is None:
        return ["旅行范围未声明"]
    gaps: list[str] = []
    tool_names = {
        str(fact.get("tool") or "")
        for fact in fact_registry.values()
        if _tool_result_has_usable_fact(fact)
    }
    transport_tools = {
        tool_specs.TOOL_QUERY_RAIL,
        tool_specs.TOOL_SEARCH_FLIGHT_ITINERARIES,
        tool_specs.TOOL_SEARCH_FLIGHT_TRANSFER,
        tool_specs.TOOL_SEARCH_FLIGHT_TRAIN_TRANSFER,
    }
    transport_query_keys = {
        _planning_tool_cache_key(
            str(fact.get("tool") or ""),
            fact.get("arguments") if isinstance(fact.get("arguments"), dict) else {},
        )
        for fact in fact_registry.values()
        if str(fact.get("tool") or "") in transport_tools
        and _tool_result_has_usable_fact(fact)
    }
    destinations = scope.get("destinations")
    destination_list = [
        str(item) for item in destinations if str(item).strip()
    ] if isinstance(destinations, list) else []
    destination_count = len(destination_list)
    if scope.get("needsTransport") and not transport_query_keys:
        gaps.append("去返程或跨城大交通事实缺失")
    elif len(transport_query_keys) < destination_count + 1:
        gaps.append(
            f"多城转场交通覆盖不足：{destination_count} 个目的地至少需要 "
            f"{destination_count + 1} 段大交通查询，目前只有 {len(transport_query_keys)} 段"
        )

    if scope.get("needsHotel"):
        missing_hotel_cities = [
            city
            for city in destination_list
            if not _has_hotel_fact_for_city(city, fact_registry)
        ]
        if destination_count and missing_hotel_cities:
            gaps.append(
                "以下过夜城市还没有具体酒店候选：" + "、".join(missing_hotel_cities[:4])
            )
        elif not destination_count and not _has_hotel_fact_for_city(
            "", fact_registry
        ):
            gaps.append("过夜城市的酒店落点缺失")

    if scope.get("needsTransport"):
        route_facts = [
            fact
            for fact in fact_registry.values()
            if str(fact.get("tool") or "") == tool_specs.TOOL_AMAP_ROUTE
            and _tool_result_has_usable_fact(fact)
        ]
        if tool_specs.TOOL_AMAP_ROUTE not in tool_names:
            gaps.append("酒店、车站/机场或主要景点的关键路线缺失")
        else:
            # Every overnight city needs at least the transport-hub connection
            # and the first main activity of the day checked, otherwise the model
            # fills the gap with invented distances and travel times.
            required_routes = max(2, 2 * destination_count)
            if len(route_facts) < required_routes:
                gaps.append(
                    f"关键路线覆盖不足：{destination_count} 个过夜城市至少需要 "
                    f"{required_routes} 条 amap_route 查证（酒店↔到离站/机场、"
                    f"酒店↔当日首个主要地点），目前只有 {len(route_facts)} 条"
                )
            if not any(_route_touches_transport_hub(fact) for fact in route_facts):
                gaps.append("缺少酒店与机场/车站之间的接驳路线查证")

    critical_markers = (
        "去程",
        "返程",
        "往返",
        "航班",
        "飞机",
        "铁路",
        "火车",
        "车次",
        "跨城",
        "转场",
        "酒店",
        "住宿",
        "关键路线",
        "接驳",
    )
    critical_remaining = [
        query
        for query in remaining_queries
        if any(marker in query.lower() for marker in critical_markers)
    ]
    if critical_remaining:
        gaps.append("模型标记仍待查：" + "；".join(critical_remaining[:4]))
    return list(dict.fromkeys(gaps))


_HOTEL_MARKERS = ("酒店", "住宿", "客栈", "民宿", "hotel")
_TRANSPORT_HUB_MARKERS = ("机场", "火车站", "高铁站", "客运站", "汽车站", "码头")


def _city_aliases(city: str) -> list[str]:
    """Loose aliases so 「喀纳斯（布尔津）」 still matches 「布尔津」 facts."""
    cleaned = re.sub(r"[（）()\s]+", " ", city).strip()
    parts = [part for part in re.split(r"[ 、,，/]+", cleaned) if part]
    aliases: set[str] = set()
    for part in [cleaned, *parts]:
        if not part:
            continue
        aliases.add(part)
        trimmed = re.sub(r"(市|县|区|自治州|地区|自治县)$", "", part)
        if len(trimmed) >= 2:
            aliases.add(trimmed)
    return sorted(aliases, key=len, reverse=True)


def _has_hotel_fact_for_city(
    city: str, fact_registry: dict[str, dict[str, Any]]
) -> bool:
    """Whether a concrete lodging candidate was searched for this city."""
    aliases = _city_aliases(city) if city else []
    for fact in fact_registry.values():
        if str(fact.get("tool") or "") not in {
            tool_specs.TOOL_AMAP_POI_SEARCH,
            tool_specs.TOOL_AMAP_POI_DETAIL,
            tool_specs.TOOL_AMAP_POI_AROUND,
        }:
            continue
        if not _tool_result_has_usable_fact(fact):
            continue
        argument_text = json.dumps(
            fact.get("arguments") or {}, ensure_ascii=False
        ).lower()
        if not any(marker in argument_text for marker in _HOTEL_MARKERS):
            # `amap_poi_detail` carries only POI IDs, so fall back to the
            # resolved name/category of the returned fact itself.
            fact_text = " ".join(
                str(fact.get(key) or "")
                for key in ("name", "category", "address", "city_name")
            ).lower()
            if not any(marker in fact_text for marker in _HOTEL_MARKERS):
                continue
            argument_text = f"{argument_text} {fact_text}"
        if not aliases:
            return True
        haystack = (
            argument_text
            + " "
            + " ".join(
                str(fact.get(key) or "")
                for key in ("name", "address", "city_name", "district_name")
            )
        )
        if any(alias in haystack for alias in aliases):
            return True
    return False


def _route_touches_transport_hub(fact: dict[str, Any]) -> bool:
    """Whether a route fact connects an airport / station endpoint."""
    endpoints = " ".join(
        str(fact.get(key) or "") for key in ("origin", "destination")
    )
    return any(marker in endpoints for marker in _TRANSPORT_HUB_MARKERS)


def _planning_tool_cache_key(tool_name: str, arguments: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True)}"


def _planning_tool_kind(
    tool_name: str, allowed_external_tool_names: frozenset[str]
) -> str:
    if tool_name in {
        tool_specs.TOOL_DECLARE_TRIP_SCOPE,
        tool_specs.TOOL_UPDATE_PLANNING_FACT_STATE,
        tool_specs.TOOL_FINISH_RESEARCH,
    }:
        return "internal"
    if tool_name in allowed_external_tool_names:
        return "external"
    return "unsupported"


def _summarize_planning_value(value: Any, *, depth: int = 0) -> Any:
    """Keep diagnostic structure while bounding large model/tool payloads."""
    if depth >= 5:
        return "<max-depth>"
    if isinstance(value, dict):
        items = list(value.items())
        summary = {
            str(key): _summarize_planning_value(item, depth=depth + 1)
            for key, item in items[:30]
        }
        if len(items) > 30:
            summary["_omitted_key_count"] = len(items) - 30
        return summary
    if isinstance(value, list):
        summary = [
            _summarize_planning_value(item, depth=depth + 1)
            for item in value[:12]
        ]
        if len(value) > 12:
            summary.append({"_omitted_item_count": len(value) - 12})
        return summary
    if isinstance(value, str):
        return value if len(value) <= 1200 else value[:1200] + "…"
    return value


def _fact_tool_counts(facts: dict[str, dict[str, Any]]) -> dict[str, int]:
    return dict(
        Counter(str(fact.get("tool") or "unknown") for fact in facts.values())
    )


def _new_fact_id(tool_name: str) -> str:
    short = tool_name.removeprefix("amap_").removeprefix("query_")
    return f"fact_{short}_{uuid.uuid4().hex[:12]}"


def _register_planning_facts(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Attach request-unique IDs to every independently selectable fact."""
    enriched = deepcopy(result)
    list_keys = [
        key for key in ("pois", "days", "candidates") if isinstance(enriched.get(key), list)
    ]
    has_selectable_items = any(enriched.get(key) for key in list_keys) or isinstance(
        enriched.get("recommended"), dict
    )
    result_context = {
        key: deepcopy(value)
        for key, value in enriched.items()
        if key not in {*list_keys, "recommended"}
        and not (has_selectable_items and key in _BULKY_CONTEXT_KEYS)
    }
    registered = False
    for key in list_keys:
        for item in enriched[key]:
            if not isinstance(item, dict):
                continue
            fact_id = _new_fact_id(tool_name)
            item["fact_id"] = fact_id
            registry[fact_id] = {
                "fact_id": fact_id,
                "tool": tool_name,
                "arguments": deepcopy(arguments),
                "result_context": result_context,
                **deepcopy(item),
            }
            registered = True
    recommended = enriched.get("recommended")
    if isinstance(recommended, dict):
        fact_id = _new_fact_id(tool_name)
        recommended["fact_id"] = fact_id
        registry[fact_id] = {
            "fact_id": fact_id,
            "tool": tool_name,
            "arguments": deepcopy(arguments),
            "result_context": result_context,
            **deepcopy(recommended),
        }
        registered = True
    if not registered:
        fact_id = _new_fact_id(tool_name)
        enriched["fact_id"] = fact_id
        registry[fact_id] = {
            "fact_id": fact_id,
            "tool": tool_name,
            "arguments": deepcopy(arguments),
            **deepcopy(enriched),
        }
    return enriched


def _select_planning_facts_for_final(
    facts: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Bound unselected research results while preserving transport first."""
    tool_priority = {
        "searchFlightItineraries": 0,
        "query_rail_tickets": 1,
        "amap_weather_range": 2,
        "amap_route": 3,
        "amap_poi_detail": 4,
        "amap_poi_search": 5,
        "amap_poi_around": 6,
    }
    ordered = sorted(
        facts.items(),
        key=lambda item: tool_priority.get(str(item[1].get("tool") or ""), 99),
    )
    per_query_counts: Counter[str] = Counter()
    selected: dict[str, dict[str, Any]] = {}
    for fact_id, fact in ordered:
        tool_name = str(fact.get("tool") or "unknown")
        arguments = fact.get("arguments")
        query_key = _planning_tool_cache_key(
            tool_name,
            arguments if isinstance(arguments, dict) else {},
        )
        per_query_limit = _MAX_FINAL_FACTS_PER_QUERY.get(tool_name, 8)
        if per_query_counts[query_key] >= per_query_limit:
            continue
        selected[fact_id] = fact
        per_query_counts[query_key] += 1
        if len(selected) >= _MAX_FINAL_FACTS:
            break
    log_event(
        "planning_final_fact_selection",
        status="success",
        input_fact_count=len(facts),
        selected_fact_count=len(selected),
        omitted_fact_count=max(0, len(facts) - len(selected)),
        selected_fact_tool_counts=_fact_tool_counts(selected),
    )
    return selected


def _compact_planning_facts(
    facts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        _compact_planning_fact_value(fact)
        for fact in facts.values()
    ]


def _compact_planning_fact_value(value: Any, *, field_name: str = "") -> Any:
    """Remove payloads useless to synthesis while keeping evidence fields."""
    if isinstance(value, dict):
        return {
            str(key): _compact_planning_fact_value(item, field_name=str(key))
            for key, item in value.items()
            if str(key) not in _FINAL_FACT_DROP_KEYS
        }
    if isinstance(value, list):
        limit = 1 if field_name == "alternatives" else 12 if field_name == "steps" else 25
        return [
            _compact_planning_fact_value(item, field_name=field_name)
            for item in value[:limit]
        ]
    if isinstance(value, str) and len(value) > 1200:
        return value[:1200] + "…"
    return value


_TIME_WINDOW_BOUNDS = {
    "早上": ("06:00", "11:59"),
    "上午": ("06:00", "11:59"),
    "傍晚": ("17:00", "19:30"),
    "晚上": ("18:00", "23:59"),
}
_TIME_WINDOW_PATTERN = re.compile(
    r"(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]"
    r"(?P<period>早上|上午|傍晚|晚上)(?P<clause>[^，。；\n]{0,80})"
)
_DESTINATION_PATTERN = re.compile(
    r"(?:去|前往|返回|回)(?P<destination>[\u4e00-\u9fff]{2,8}?)(?=[，、。；\s]|$)"
)
_CLOCK_PATTERN = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d")
_INTERCITY_MARKERS = ("航班", "飞机", "高铁", "动车", "火车", "列车", "返程")


def _normalize_clock(value: str | None) -> str | None:
    if not value or not _CLOCK_PATTERN.fullmatch(value):
        return None
    hour, minute = value.split(":", 1)
    return f"{int(hour):02d}:{minute}"


def _time_window_problems(
    data: ItineraryData, user_text: str
) -> list[planning_feasibility.Problem]:
    """Check explicit Chinese departure windows against the generated timeline.

    Findings are day-scoped (``date`` set, no ``schedule_id``): the missing leg is
    about that day's shape, not a single bad row, so annotate_unresolved can put a
    day advisory on any trip that states 「X日晚上回Y」— not only the e2e fixture.
    """
    try:
        year = int(data.trip_info.start_date[:4])
    except (TypeError, ValueError):
        return []
    days = {day.date: day for day in data.itinerary}
    problems: list[planning_feasibility.Problem] = []
    seen: set[tuple[str, str, str]] = set()
    for match in _TIME_WINDOW_PATTERN.finditer(user_text):
        destination_match = _DESTINATION_PATTERN.search(match.group("clause"))
        if not destination_match:
            continue
        date = f"{year:04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
        period = match.group("period")
        destination = destination_match.group("destination")
        anchor = (date, period, destination)
        if anchor in seen:
            continue
        seen.add(anchor)
        earliest, latest = _TIME_WINDOW_BOUNDS[period]
        day = days.get(date)
        matched = False
        if day:
            for schedule in day.schedules:
                text = " ".join(
                    part
                    for part in (
                        schedule.activity,
                        schedule.transport,
                        schedule.place_name,
                    )
                    if part
                )
                if destination not in text and not any(
                    marker in text for marker in _INTERCITY_MARKERS
                ):
                    continue
                candidate_times = [schedule.start_time or ""]
                candidate_times.extend(_CLOCK_PATTERN.findall(text))
                normalized_times = [
                    normalized
                    for value in candidate_times
                    if (normalized := _normalize_clock(value)) is not None
                ]
                if any(earliest <= value <= latest for value in normalized_times):
                    matched = True
                    break
        if not matched:
            problems.append(
                planning_feasibility.Problem(
                    message=(
                        f"{date} {period}前往{destination}缺少 "
                        f"{earliest}–{latest} 内的独立跨城交通日程"
                    ),
                    date=date,
                )
            )
    return problems


def _schedule_overlap_problems(
    data: ItineraryData,
) -> list[planning_feasibility.Problem]:
    """Describe overlapping timed schedules for a targeted model correction."""
    problems: list[planning_feasibility.Problem] = []
    for day in data.itinerary:
        timed = sorted(
            (
                schedule
                for schedule in day.schedules
                if _normalize_clock(schedule.start_time) is not None
            ),
            key=lambda schedule: _normalize_clock(schedule.start_time) or "",
        )
        previous = None
        for schedule in timed:
            if (
                previous is not None
                and previous.end_time
                and schedule.start_time
                and (_normalize_clock(schedule.start_time) or "")
                < (_normalize_clock(previous.end_time) or "")
            ):
                problems.append(
                    planning_feasibility.Problem(
                        message=(
                            f"{day.date} 的 {previous.id}（至 {previous.end_time}）与 "
                            f"{schedule.id}（{schedule.start_time} 开始）时间重叠"
                        ),
                        date=day.date,
                        schedule_id=schedule.id,
                    )
                )
            if schedule.end_time and _CLOCK_PATTERN.fullmatch(schedule.end_time):
                previous = schedule
    return problems


_MAX_FEASIBILITY_REPAIR_ROUNDS = 2


def _itinerary_violations(
    itinerary: ItineraryData,
    user_text: str,
    retained_facts: dict[str, dict[str, Any]],
) -> list[str]:
    """All deterministic problems worth a repair round, in fix order."""
    return [
        problem.message
        for problem in _itinerary_problems(itinerary, user_text, retained_facts)
    ]


def _itinerary_problems(
    itinerary: ItineraryData,
    user_text: str,
    retained_facts: dict[str, dict[str, Any]],
) -> list[planning_feasibility.Problem]:
    """Deterministic findings from the orchestrator-level and fact-level checks.

    A schedule that ignores the user's stated time window or overlaps its
    neighbour is as unusable as one citing a flight that does not exist, so both
    block; only the day-density rules are advisory.
    """
    problems = list(_time_window_problems(itinerary, user_text))
    problems.extend(_schedule_overlap_problems(itinerary))
    problems.extend(
        planning_feasibility.find_problem_details(itinerary, retained_facts)
    )
    seen: set[str] = set()
    unique: list[planning_feasibility.Problem] = []
    for problem in problems:
        if problem.message in seen:
            continue
        seen.add(problem.message)
        unique.append(problem)
    return unique


def _repair_itinerary_violations(
    *,
    system_prompt: str,
    user_text: str,
    itinerary: ItineraryData,
    retained_facts: dict[str, dict[str, Any]],
    planning_model: PlanningModel,
) -> ItineraryData:
    """Demand targeted corrections while the plan contradicts its own facts.

    Applies to every planning model. Checks cover explicit user time windows,
    overlapping schedules, and (crucially) schedules whose departure times,
    flight/train numbers, distances or travel times are not backed by the facts
    they cite.
    """
    # Mechanical violations are corrected in code first: a model round costs a
    # minute or more and could rewrite unrelated parts of a plan that is otherwise
    # fine, and these edits have exactly one correct outcome anyway.
    current, autofixed = planning_feasibility.autofix(itinerary, retained_facts)
    if autofixed:
        log_event(
            "planning_feasibility_autofix",
            status="applied",
            fix_count=len(autofixed),
            fixes=autofixed[:30],
            planning_model=planning_model,
        )
    problems = _itinerary_violations(current, user_text, retained_facts)
    if not problems:
        return current
    for attempt in range(1, _MAX_FEASIBILITY_REPAIR_ROUNDS + 1):
        planning_progress.report(
            "verifying",
            repair_round=attempt,
            detail=f"正在修正 {len(problems)} 处与事实不符之处",
        )
        log_event(
            "planning_feasibility_repair",
            status="start",
            attempt=attempt,
            problem_count=len(problems),
            problems=problems[:20],
            planning_model=planning_model,
        )
        repaired = _request_itinerary_repair(
            system_prompt=system_prompt,
            user_text=user_text,
            itinerary=current,
            retained_facts=retained_facts,
            problems=problems,
            planning_model=planning_model,
            attempt=attempt,
        )
        if repaired is None:
            break
        remaining = _itinerary_violations(repaired, user_text, retained_facts)
        resolved = len(problems) - len(remaining)
        log_event(
            "planning_feasibility_repair",
            status="success" if not remaining else "partial",
            attempt=attempt,
            resolved_count=max(0, resolved),
            remaining_count=len(remaining),
            remaining_problems=remaining[:20],
            planning_model=planning_model,
        )
        current = repaired
        if not remaining:
            return current
        if len(remaining) >= len(problems):
            # No forward progress; a further identical request would not help.
            break
        problems = remaining
    unresolved = _itinerary_problems(current, user_text, retained_facts)
    blocking = [problem for problem in unresolved if problem.blocking]
    if not unresolved:
        return current
    # A plan that survives repair with a row we cannot vouch for is still worth
    # far more than no plan at all — provided that row says so plainly.
    current, flags = planning_feasibility.annotate_unresolved(current, blocking)
    log_event(
        "planning_feasibility_repair",
        status="shipped_with_flags" if blocking else "shipped_with_advisories",
        remaining_count=len(unresolved),
        blocking_count=len(blocking),
        remaining_problems=[problem.message for problem in unresolved][:20],
        flags=flags,
        planning_model=planning_model,
    )
    return current


def _request_itinerary_repair(
    *,
    system_prompt: str,
    user_text: str,
    itinerary: ItineraryData,
    retained_facts: dict[str, dict[str, Any]],
    problems: list[str],
    planning_model: PlanningModel,
    attempt: int,
) -> ItineraryData | None:
    current = json.dumps(itinerary.model_dump(mode="json"), ensure_ascii=False)
    facts = json.dumps(_compact_planning_facts(retained_facts), ensure_ascii=False)
    repair = vivo_chat_client.chat_messages(
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "以下行程已完成事实检索，但存在与事实矛盾或不可执行的问题。"
                    "只修复列出的问题，不要重写无关内容：跨城交通必须单独成条，"
                    "start_time/end_time 必须直接取自所引班次事实的真实起降时刻；"
                    "distance_km/travel_minutes 必须等于所引 amap_route 事实的值，"
                    "没有对应路线事实时置为 null 并在 note 写明需以地图实时为准；"
                    "不得写未查到的航班号或车次号；保留其余行程、fact_refs 和地图语义字段，"
                    "消除时间重叠。不得调用工具，只输出完整 ItineraryData JSON。\n"
                    f"【用户需求】{user_text[:4000]}\n"
                    f"【必须修复】{json.dumps(problems[:30], ensure_ascii=False)}\n"
                    f"【可引用的保留事实】{facts}\n"
                    f"【当前行程】{current}"
                ),
            },
        ],
        tools=None,
        stage="planning_feasibility_repair",
        temperature=0.0,
        max_completion_tokens=_MAX_GENERATION_TOKENS,
        planning_model=planning_model,
        response_format=(
            {"type": "json_object"}
            if planning_model in DEEPSEEK_PLANNING_MODELS
            else None
        ),
        # No max_attempts cap: pinning it to 1 meant a single reasoning-only round
        # (observed: flash burnt the budget and returned nothing) silently spent
        # the only repair round, shipping the problems it was called to fix.
    )
    if not repair.content:
        log_event(
            "planning_feasibility_repair",
            status="empty_response",
            attempt=attempt,
            planning_model=planning_model,
        )
        return None
    try:
        return output_parser.parse_model_json(repair.content, ItineraryData)
    except AIGenerationError as exc:
        log_event(
            "planning_feasibility_repair",
            status="unparseable",
            attempt=attempt,
            error_message=str(exc)[:500],
            planning_model=planning_model,
        )
        return None


def _generate_itinerary_from_research(
    *,
    system_prompt: str,
    user_text: str,
    scope: dict[str, Any],
    retained_facts: dict[str, dict[str, Any]],
    research_summary: dict[str, Any],
    stage: str,
    planning_model: PlanningModel,
) -> ItineraryData:
    reference_fact_rule = "铁路参考事实标 reference。"
    if planning_model in DEEPSEEK_PLANNING_MODELS:
        reference_fact_rule = "飞友航班/中转与铁路参考事实标 reference。"
    final_facts = _compact_planning_facts(retained_facts)
    compact_user = (
        f"{user_text}\n\n【模型已声明的旅行范围】\n"
        f"{json.dumps(scope, ensure_ascii=False)}\n\n【保留的完整工具事实】\n"
        f"{json.dumps(final_facts, ensure_ascii=False)}\n\n"
        f"【研究结束摘要】\n{json.dumps(research_summary, ensure_ascii=False)}\n\n"
        "工具已关闭。工具事实已去除路线折线和供应商原始报文，但 fact_id 与规划所需字段完整保留。"
        "现在只输出完整 ItineraryData JSON。新增字段可选；使用事实的日程必须填写"
        " fact_refs，只有 status=ok 的高德事实可标 verified，"
        + reference_fact_rule
        + " transport_mode 只能是 driving/transit/walking/bicycling 或 null；飞机、铁路等大交通写入 transport，transport_mode=null。"
        "用户明确的日期与时段是不可改写的硬约束：早上须在 06:00–11:59 出发，"
        "傍晚须在 17:00–19:30 出发，晚上须在 18:00 以后出发；没有已核验班次时，"
        "只能在原时间窗内给 reference 方案，不得用其他时段替代。"
        "同一天任一日程的 start_time 不得早于上一日程的 end_time，禁止时间重叠；"
        "备选交通不得与同日活动同时排入主时间线。"
        "每段带时段硬约束的跨城交通必须单独成为一条 schedule，start_time 填实际发车或起飞时间；"
        "去车站、取行李、候车等准备活动不得与该跨城交通合并为同一条。"
        "每段跨城交通只能给一个可执行的主方案，禁止在主时间线写“高铁或航班”等二选一；"
        "每个具名酒店和景点优先使用保留事实中的名称、坐标与 fact_id，已有匹配事实却标 unverified"
        " 属于不合格；不得为了凑满日程编造未检索的具名景点。"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": compact_user},
    ]
    final = vivo_chat_client.chat_messages(
        messages=messages,
        tools=None,
        stage=stage,
        temperature=0.2,
        max_completion_tokens=_MAX_GENERATION_TOKENS,
        planning_model=planning_model,
        response_format=(
            {"type": "json_object"}
            if planning_model in DEEPSEEK_PLANNING_MODELS
            else None
        ),
        # Final synthesis is where flight times, route evidence and daily pacing
        # have to be reconciled against each other, so thinking stays on.
        thinking_enabled=None,
    )
    if final.content:
        log_event(
            "planning_model_response_summary",
            status="ready_to_parse",
            path="research_final",
            generation_stage=stage,
            content_chars=len(final.content),
            content_excerpt=_excerpt(final.content),
            scope=_summarize_planning_value(scope),
            retained_fact_count=len(retained_facts),
            final_fact_chars=len(json.dumps(final_facts, ensure_ascii=False)),
            retained_fact_ids=list(retained_facts),
            retained_fact_tool_counts=_fact_tool_counts(retained_facts),
            research_summary=_summarize_planning_value(research_summary),
            planning_model=planning_model,
        )
        try:
            with timed_stage("planning_parse_model_json", path="research_final"):
                return output_parser.parse_model_json(final.content, ItineraryData)
        except AIGenerationError as exc:
            field_repaired = _repair_final_json_fields(
                invalid_content=final.content,
                parse_error=exc,
                planning_model=planning_model,
            )
            if field_repaired is not None:
                return field_repaired
            messages.extend(
                [
                    {"role": "assistant", "content": final.content},
                    {
                        "role": "user",
                        "content": (
                            "字段级补丁无法完成修复。请根据以下精确诊断修复结构，"
                            "只输出完整合法 JSON，"
                            "不得调用工具或添加解释。"
                            f"\n【错误诊断】{_model_output_error_detail(exc, final.content)}"
                        ),
                    },
                ]
            )
            repaired = vivo_chat_client.chat_messages(
                messages=messages,
                tools=None,
                stage="planning_final_full_repair",
                temperature=0.1,
                max_completion_tokens=_MAX_GENERATION_TOKENS,
                planning_model=planning_model,
                response_format=(
                    {"type": "json_object"}
                    if planning_model in DEEPSEEK_PLANNING_MODELS
                    else None
                ),
                thinking_enabled=(
                    False if planning_model in DEEPSEEK_PLANNING_MODELS else None
                ),
            )
            if repaired.content:
                return output_parser.parse_model_json(repaired.content, ItineraryData)
        raise AIGenerationError("行程规划生成失败：模型未返回合法 JSON")
    log_event(
        "planning_final_empty_content",
        status="failed",
        generation_stage=stage,
        retained_fact_count=len(retained_facts),
        planning_model=planning_model,
    )
    raise AIGenerationError(
        "行程规划生成失败：模型只输出了思考过程、没有给出行程正文，请重试"
    )


def _repair_final_json_fields(
    *,
    invalid_content: str,
    parse_error: AIGenerationError,
    planning_model: PlanningModel,
) -> ItineraryData | None:
    """Ask for a bounded field patch when the original JSON is parseable."""
    cause = parse_error.__cause__
    if not isinstance(cause, ValidationError):
        log_event(
            "planning_final_field_patch",
            status="skipped",
            reason="json_not_parseable_or_no_field_paths",
        )
        return None
    try:
        original = json.loads(output_parser.extract_first_json(invalid_content))
    except (AIGenerationError, json.JSONDecodeError, TypeError):
        log_event(
            "planning_final_field_patch",
            status="skipped",
            reason="json_document_unavailable",
        )
        return None
    if not isinstance(original, dict):
        return None

    allowed_paths = [
        list(item.get("loc") or ())
        for item in cause.errors()
        if item.get("loc")
    ][:12]
    if not allowed_paths:
        return None
    diagnostic = _model_output_error_detail(parse_error, invalid_content)
    log_event(
        "planning_final_field_patch",
        status="start",
        allowed_paths=allowed_paths,
        error_detail=diagnostic,
        planning_model=planning_model,
    )
    patch_prompt = (
        "你是 JSON 字段补丁器。不得重写整份行程，只能修改允许路径中的错误字段。"
        "输出一个 JSON 对象：{\"patches\":[{\"path\":[\"itinerary\",0,...],"
        "\"value\":<替换后的值>}]} 。path 必须与允许路径完全一致，不得增删其他字段。\n"
        f"【允许路径】{json.dumps(allowed_paths, ensure_ascii=False)}\n"
        f"【错误诊断】{diagnostic}\n"
        f"【原 JSON】{json.dumps(original, ensure_ascii=False)}"
    )
    try:
        turn = vivo_chat_client.chat_messages(
            messages=[
                {"role": "system", "content": "只输出合法 JSON 字段补丁，不要解释。"},
                {"role": "user", "content": patch_prompt},
            ],
            tools=None,
            stage="planning_final_field_patch",
            temperature=0.0,
            max_completion_tokens=3000,
            planning_model=planning_model,
            response_format=(
                {"type": "json_object"}
                if planning_model in DEEPSEEK_PLANNING_MODELS
                else None
            ),
            thinking_enabled=(
                False if planning_model in DEEPSEEK_PLANNING_MODELS else None
            ),
        )
        if not turn.content:
            raise ValueError("empty patch response")
        payload = json.loads(output_parser.extract_first_json(turn.content))
        patches = payload.get("patches") if isinstance(payload, dict) else None
        if not isinstance(patches, list) or not patches:
            raise ValueError("patches must be a non-empty list")
        allowed = {_json_path_key(path) for path in allowed_paths}
        patched = deepcopy(original)
        applied_paths: list[list[Any]] = []
        for item in patches[:12]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), list):
                raise ValueError("invalid patch entry")
            path = item["path"]
            if _json_path_key(path) not in allowed:
                raise ValueError("patch path outside validation errors")
            _set_json_path(patched, path, item.get("value"))
            applied_paths.append(path)
        result = ItineraryData.model_validate(patched)
        log_event(
            "planning_final_field_patch",
            status="success",
            patch_count=len(applied_paths),
            applied_paths=applied_paths,
            response_chars=len(turn.content),
            planning_model=planning_model,
        )
        return result
    except (AIGenerationError, ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        log_event(
            "planning_final_field_patch",
            status="failed",
            error_type=type(exc).__name__,
            error_message=str(exc)[:1000],
            planning_model=planning_model,
        )
        return None


def _json_path_key(path: list[Any]) -> str:
    return json.dumps(path, ensure_ascii=False, separators=(",", ":"))


def _set_json_path(document: dict[str, Any], path: list[Any], value: Any) -> None:
    if not path:
        raise ValueError("root replacement is not allowed")
    current: Any = document
    for part in path[:-1]:
        if isinstance(current, dict) and isinstance(part, str) and part in current:
            current = current[part]
        elif (
            isinstance(current, list)
            and isinstance(part, int)
            and 0 <= part < len(current)
        ):
            current = current[part]
        else:
            raise ValueError("patch parent path does not exist")
    leaf = path[-1]
    if isinstance(current, dict) and isinstance(leaf, str):
        current[leaf] = value
        return
    if isinstance(current, list) and isinstance(leaf, int) and 0 <= leaf < len(current):
        current[leaf] = value
        return
    raise ValueError("patch leaf path is invalid")


def _repair_tool_final_json(
    *,
    messages: list[dict[str, Any]],
    invalid_content: str,
    parse_error: AIGenerationError,
    execute_tool: ToolExecutor,
    total_calls: int,
) -> ItineraryData:
    """Try one tool-capable targeted repair, then one bounded plain fallback."""
    first_detail = _model_output_error_detail(parse_error, invalid_content)
    messages.append({"role": "assistant", "content": invalid_content})
    messages.append(
        {
            "role": "user",
            "content": (
                "上一次最终 ItineraryData JSON 无法解析。请重点修复以下错误，"
                "保留已有工具事实和行程内容；如确有必要可继续调用工具，否则只输出"
                "修正后的完整 JSON。\n"
                f"【错误诊断】{first_detail}"
            ),
        }
    )
    log_event(
        "planning_json_repair",
        status="start",
        path="function_calling",
        tools_enabled=True,
        timeout_seconds=_JSON_REPAIR_TIMEOUT_SECONDS,
        error_detail=first_detail,
    )

    try:
        repair = vivo_chat_client.chat_messages(
            messages=messages,
            tools=(
                tool_specs.PLANNING_TOOLS
                if total_calls < _MAX_TOOL_CALLS_TOTAL
                else None
            ),
            temperature=0.0,
            max_completion_tokens=_MAX_GENERATION_TOKENS,
            timeout_seconds=_JSON_REPAIR_TIMEOUT_SECONDS,
            max_attempts=1,
        )
        if repair.tool_calls:
            repair = _execute_repair_tools_and_finalize(
                messages=messages,
                repair=repair,
                execute_tool=execute_tool,
                total_calls=total_calls,
            )
        if not repair.content:
            raise AIGenerationError("JSON 修复未返回内容")
        log_event(
            "planning_model_response_summary",
            status="ready_to_parse",
            path="function_calling_repair",
            content_chars=len(repair.content),
            content_excerpt=_excerpt(repair.content),
        )
        try:
            with timed_stage(
                "planning_parse_model_json", path="function_calling_repair"
            ):
                return output_parser.parse_model_json(repair.content, ItineraryData)
        except AIGenerationError as exc:
            second_detail = _model_output_error_detail(exc, repair.content)
            messages.append({"role": "assistant", "content": repair.content})
            return _plain_fallback_with_tool_history(messages, second_detail)
    except _PlanningFallbackExhausted:
        raise
    except AIGenerationError as exc:
        return _plain_fallback_with_tool_history(
            messages, _model_output_error_detail(exc, "")
        )


def _execute_repair_tools_and_finalize(
    *,
    messages: list[dict[str, Any]],
    repair: vivo_chat_client.ChatTurn,
    execute_tool: ToolExecutor,
    total_calls: int,
) -> vivo_chat_client.ChatTurn:
    """Execute tools requested by the single repair attempt, then force JSON."""
    remaining_calls = max(0, _MAX_TOOL_CALLS_TOTAL - total_calls)
    executable_calls = repair.tool_calls[:remaining_calls]
    messages.append(
        {
            "role": "assistant",
            "content": repair.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in executable_calls
            ],
        }
    )
    parsed_calls = [(tc, _safe_json_args(tc.arguments)) for tc in executable_calls]
    batch_requests: dict[str, tuple[str, dict[str, Any]]] = {}
    for tc, args in parsed_calls:
        cache_key = _planning_tool_cache_key(tc.name, args)
        batch_requests.setdefault(cache_key, (tc.name, args))
    batch_results = _execute_external_batch(batch_requests, execute_tool)
    for offset, (tc, args) in enumerate(parsed_calls, start=1):
        log_event(
            "planning_execute_tool",
            status="start",
            tool_name=tc.name,
            arguments=args,
            total_calls=total_calls + offset,
            source="json_repair",
        )
        result = deepcopy(
            batch_results[_planning_tool_cache_key(tc.name, args)]
        )
        log_event(
            "planning_execute_tool",
            status=result.get("status", "unknown"),
            tool_name=tc.name,
            arguments=args,
            result=result,
            source="json_repair",
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(
                    _compact_planning_fact_value(result),
                    ensure_ascii=False,
                ),
            }
        )
    if len(executable_calls) < len(repair.tool_calls):
        messages.append(
            {
                "role": "user",
                "content": "工具调用已达到总上限，请使用已有事实修复并输出完整 JSON。",
            }
        )
    else:
        messages.append(
            {
                "role": "user",
                "content": "请根据刚返回的工具事实完成修复，现在只输出完整合法的 ItineraryData JSON。",
            }
        )
    log_event(
        "planning_json_repair",
        status="finalize",
        tools_executed=len(executable_calls),
        timeout_seconds=_JSON_REPAIR_TIMEOUT_SECONDS,
    )
    return vivo_chat_client.chat_messages(
        messages=messages,
        tools=None,
        temperature=0.0,
        max_completion_tokens=_MAX_GENERATION_TOKENS,
        timeout_seconds=_JSON_REPAIR_TIMEOUT_SECONDS,
        max_attempts=1,
    )


def _plain_fallback_with_tool_history(
    messages: list[dict[str, Any]], error_detail: str
) -> ItineraryData:
    """One tool-free full regeneration that retains all prior tool facts."""
    messages.append(
        {
            "role": "user",
            "content": (
                "定向修复仍未得到合法 JSON。不要再调用工具；请保留以上工具事实，"
                "重新生成一份完整、合法的 ItineraryData JSON，不要解释或 Markdown。\n"
                f"【最近错误】{error_detail}"
            ),
        }
    )
    log_event(
        "planning_plain_fallback",
        status="start",
        context="tool_history_retained",
        timeout_seconds=_PLAIN_FALLBACK_TIMEOUT_SECONDS,
    )
    try:
        final = vivo_chat_client.chat_messages(
            messages=messages,
            tools=None,
            temperature=0.0,
            max_completion_tokens=_MAX_GENERATION_TOKENS,
            timeout_seconds=_PLAIN_FALLBACK_TIMEOUT_SECONDS,
            max_attempts=1,
        )
        if not final.content:
            raise AIGenerationError("plain fallback 未返回内容")
        log_event(
            "planning_model_response_summary",
            status="ready_to_parse",
            path="tool_context_plain_fallback",
            content_chars=len(final.content),
            content_excerpt=_excerpt(final.content),
        )
        with timed_stage(
            "planning_parse_model_json", path="tool_context_plain_fallback"
        ):
            return output_parser.parse_model_json(final.content, ItineraryData)
    except AIGenerationError as exc:
        raise _PlanningFallbackExhausted(
            "行程规划生成失败：JSON 修复与 plain fallback 均失败"
        ) from exc


def _model_output_error_detail(exc: AIGenerationError, content: str) -> str:
    """Build a bounded diagnostic suitable for the model repair prompt/log."""
    cause = exc.__cause__
    if isinstance(cause, json.JSONDecodeError):
        start = max(0, cause.pos - 120)
        end = min(len(content), cause.pos + 120)
        nearby = " ".join(content[start:end].split())
        return (
            f"JSONDecodeError: {cause.msg}，第 {cause.lineno} 行第 {cause.colno} 列"
            f"（位置 {cause.pos}）；错误附近：{nearby}"
        )
    errors = getattr(cause, "errors", None)
    if callable(errors):
        summaries: list[str] = []
        for item in errors()[:5]:
            loc = ".".join(str(part) for part in item.get("loc", ())) or "根对象"
            summaries.append(f"{loc}: {item.get('msg', '字段不合法')}")
        if summaries:
            return "字段校验失败：" + "；".join(summaries)
    return str(exc)


def _safe_json_args(raw: str) -> dict:
    """Parse model-supplied function arguments; tolerate malformed JSON."""
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _plan_plain(
    system_prompt: str,
    user_text: str,
    *,
    planning_model: PlanningModel = DEFAULT_PLANNING_MODEL,
) -> ItineraryData:
    """No-tools planning fallback; one JSON-repair retry allowed."""
    with timed_stage("planning_plain_model"):
        raw = vivo_chat_client.chat_json(
            task=vivo_chat_client.TASK_PLANNING,
            system_prompt=system_prompt,
            user_text=user_text,
            max_completion_tokens=_MAX_GENERATION_TOKENS,
            planning_model=planning_model,
        )
    try:
        log_event(
            "planning_model_response_summary",
            status="ready_to_parse",
            path="plain",
            content_chars=len(raw),
            content_excerpt=_excerpt(raw),
        )
        with timed_stage("planning_parse_model_json", path="plain"):
            return output_parser.parse_model_json(raw, ItineraryData)
    except AIGenerationError:
        # Planning is the only task allowed a single JSON-repair retry.
        logger.info("planning JSON invalid, attempting one repair retry")
        log_event("planning_json_repair", status="start", reason="initial_parse_failed")
        repair_text = (
            user_text
            + "\n\n【上一次输出无法解析为合法 JSON，请只输出修正后的完整 ItineraryData JSON，"
            "不要任何解释】\n" + raw[:2000]
        )
        with timed_stage("planning_json_repair_model"):
            raw2 = vivo_chat_client.chat_json(
                task=vivo_chat_client.TASK_PLANNING,
                system_prompt=system_prompt,
                user_text=repair_text,
                max_completion_tokens=_MAX_GENERATION_TOKENS,
                planning_model=planning_model,
            )
        try:
            log_event(
                "planning_model_response_summary",
                status="ready_to_parse",
                path="repair",
                content_chars=len(raw2),
                content_excerpt=_excerpt(raw2),
            )
            with timed_stage("planning_parse_model_json", path="repair"):
                return output_parser.parse_model_json(raw2, ItineraryData)
        except AIGenerationError as exc:
            raise AIGenerationError("行程规划生成失败：模型未返回合法 JSON") from exc


def _excerpt(text: str | None, limit: int = 500) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    return compact[:limit] + ("..." if len(compact) > limit else "")
