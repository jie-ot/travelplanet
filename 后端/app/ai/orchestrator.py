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
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.ai import output_parser
from app.ai.clients import vivo_chat_client, vivo_image_client
from app.ai.clients.vivo_image_client import ImageGenerationResult
from app.ai.memory import context_builder
from app.ai.prompts import load_prompt
from app.ai.schemas import (
    MemoryUpdateResult,
    PhotoAnalysisItem,
    PhotoAnalysisResult,
    PostcardPlanItem,
    PostcardPlanResult,
    PostcardSelectionResult,
    ReportDraftResult,
)
from app.ai.tools import tool_specs
from app.core.business_logging import call_in_current_context, log_event, timed_stage
from app.core.config import settings
from app.core.exceptions import AIGenerationError, ImageInputPolicyError
from app.models.itinerary import ItineraryData

logger = logging.getLogger("travelplanet")

# Planning function-calling loop bounds (defensive; tool execution is fast and
# non-blocking, but the model must always converge to a final JSON answer).
_MAX_TOOL_ROUNDS = 5
_MAX_TOOL_CALLS_TOTAL = 75
_JSON_REPAIR_TIMEOUT_SECONDS = 60
_PLAIN_FALLBACK_TIMEOUT_SECONDS = 90
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
) -> ItineraryData:
    """Planning → full ItineraryData.

    With `execute_tool`, run a bounded function-calling loop so the model can
    choose whitelist tools (高德 weather/POI/route, 12306 rail, flight) and
    synthesize the itinerary from returned facts. The orchestrator intercepts
    each function call and routes it through the injected executor (which
    validates + executes via travel_fact_service). Falls back to the plain JSON
    path with the baseline fact pack on any function-calling failure.
    """
    system_prompt = _load_planning_system_prompt()
    user_text = context_builder.build_planning_user_text(
        message=message,
        context=context,
        memory_summary=memory_summary,
        fact_pack=fact_pack,
    )
    if execute_tool is not None:
        try:
            return _plan_with_tools(system_prompt, user_text, execute_tool)
        except _PlanningFallbackExhausted:
            raise
        except AIGenerationError:
            logger.info(
                "planning function-calling path failed; falling back to plain JSON path"
            )

    return _plan_plain(system_prompt, user_text)


def _load_planning_system_prompt() -> str:
    """Load planning contract and quality skill as one system prompt."""
    current_date = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    return (
        f"【当前日期】\n今天是 {current_date}（北京时间）。"
        "用户的日期表达可能较为模糊，请根据当前日期和用户需求推断具体行程日期；"
        "需要推断时，推断结果不得早于今天。\n\n"
        "【planning_system.md：必须遵守的契约】\n"
        + load_prompt("planning_system.md")
        + "\n\n【planning_skill.md：好旅行规划的标准】\n"
        + load_prompt("planning_skill.md")
    )


def _plan_with_tools(
    system_prompt: str, user_text: str, execute_tool: ToolExecutor
) -> ItineraryData:
    """Bounded function-calling loop; the model drives whitelist tool selection."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
    total_calls = 0
    for round_idx in range(_MAX_TOOL_ROUNDS):
        log_event("planning_tool_round", round=round_idx + 1, total_calls=total_calls)
        turn = vivo_chat_client.chat_messages(
            messages=messages,
            tools=tool_specs.PLANNING_TOOLS,
            max_completion_tokens=16000,
        )
        if turn.tool_calls:
            remaining_calls = max(0, _MAX_TOOL_CALLS_TOTAL - total_calls)
            executable_calls = turn.tool_calls[:remaining_calls]
            skipped_calls = turn.tool_calls[remaining_calls:]
            log_event(
                "planning_tool_round_result",
                status="tool_calls",
                round=round_idx + 1,
                tool_calls=len(turn.tool_calls),
                executable_calls=len(executable_calls),
                skipped_calls=len(skipped_calls),
                assistant_content_excerpt=_excerpt(turn.content),
            )
            if not executable_calls:
                messages.append(
                    {
                        "role": "user",
                        "content": "已达到本次规划的工具调用总上限，请停止调用工具，"
                        "基于已有事实输出完整 ItineraryData JSON。",
                    }
                )
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.content,
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
            for tc in executable_calls:
                if total_calls >= _MAX_TOOL_CALLS_TOTAL:
                    break
                args = _safe_json_args(tc.arguments)
                total_calls += 1
                log_event(
                    "planning_execute_tool",
                    status="start",
                    tool_name=tc.name,
                    total_calls=total_calls,
                )
                result = execute_tool(tc.name, args)
                log_event(
                    "planning_execute_tool",
                    status=result.get("status", "unknown"),
                    tool_name=tc.name,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            if skipped_calls:
                messages.append(
                    {
                        "role": "user",
                        "content": f"已达到工具调用总上限（{_MAX_TOOL_CALLS_TOTAL} 次），"
                        "本轮剩余工具调用已截断；请基于已返回事实继续或输出最终 JSON。",
                    }
                )
            continue
        # No (further) tool calls: this should be the final ItineraryData JSON.
        if turn.content:
            log_event(
                "planning_tool_round_result",
                status="final_content",
                round=round_idx + 1,
            )
            log_event(
                "planning_model_response_summary",
                status="ready_to_parse",
                path="function_calling",
                content_chars=len(turn.content),
                content_excerpt=_excerpt(turn.content),
            )
            try:
                with timed_stage("planning_parse_model_json", path="function_calling"):
                    return output_parser.parse_model_json(turn.content, ItineraryData)
            except AIGenerationError as exc:
                return _repair_tool_final_json(
                    messages=messages,
                    invalid_content=turn.content,
                    parse_error=exc,
                    execute_tool=execute_tool,
                    total_calls=total_calls,
                )
        break

    # Force a final tool-free answer (the model has all tool results by now).
    messages.append(
        {
            "role": "user",
            "content": "请基于以上对话与工具返回的事实，现在只输出完整的 ItineraryData JSON，"
            "不要再调用任何工具，不要任何解释、Markdown 或代码块围栏。",
        }
    )
    final = vivo_chat_client.chat_messages(
        messages=messages, tools=None, max_completion_tokens=16000
    )
    if final.content:
        log_event(
            "planning_model_response_summary",
            status="ready_to_parse",
            path="forced_final",
            content_chars=len(final.content),
            content_excerpt=_excerpt(final.content),
        )
        try:
            with timed_stage("planning_parse_model_json", path="forced_final"):
                return output_parser.parse_model_json(final.content, ItineraryData)
        except AIGenerationError as exc:
            return _repair_tool_final_json(
                messages=messages,
                invalid_content=final.content,
                parse_error=exc,
                execute_tool=execute_tool,
                total_calls=total_calls,
            )
    raise AIGenerationError("行程规划生成失败：模型未返回合法 JSON")


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
            max_completion_tokens=16000,
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
    for offset, tc in enumerate(executable_calls, start=1):
        args = _safe_json_args(tc.arguments)
        log_event(
            "planning_execute_tool",
            status="start",
            tool_name=tc.name,
            total_calls=total_calls + offset,
            source="json_repair",
        )
        result = execute_tool(tc.name, args)
        log_event(
            "planning_execute_tool",
            status=result.get("status", "unknown"),
            tool_name=tc.name,
            source="json_repair",
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
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
        max_completion_tokens=16000,
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
            max_completion_tokens=16000,
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


def _plan_plain(system_prompt: str, user_text: str) -> ItineraryData:
    """No-tools planning fallback; one JSON-repair retry allowed."""
    with timed_stage("planning_plain_model"):
        raw = vivo_chat_client.chat_json(
            task=vivo_chat_client.TASK_PLANNING,
            system_prompt=system_prompt,
            user_text=user_text,
            max_completion_tokens=16000,
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
                max_completion_tokens=16000,
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
