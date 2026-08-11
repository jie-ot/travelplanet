"""Assemble model inputs: implicit-traits summary + request + photo semantics +
planning context + TravelFactPack (《任务详细流程规范》二、七).

The implicit-traits document is only ever injected as a bounded summary, never
the full document, and never logged in full.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.schemas import PhotoAnalysisResult, PostcardSelectionResult
from app.models.itinerary import ItineraryData


def build_planning_user_text(
    *,
    message: str,
    context: ItineraryData | None,
    memory_summary: str,
    fact_pack: dict[str, Any] | None = None,
) -> str:
    """Compose the planning input from cumulative requirements + latest plan."""
    parts: list[str] = []
    parts.append(f"【用户长期旅行偏好摘要】\n{memory_summary}")
    parts.append(f"【累计用户需求（含本轮新增要求）】\n{message}")
    if context is not None:
        compact_context = context.model_dump()
        for day in compact_context.get("itinerary", []):
            day.pop("daily_maps", None)
        parts.append(
            "【最新完整行程 context（只保留上一版规划；请在此基础上重写，未涉及部分保持不变）】\n"
            + json.dumps(compact_context, ensure_ascii=False)
        )
    else:
        parts.append("【当前完整行程 context】\nnull（首轮，请新建完整行程）")
    if fact_pack is not None:
        rails = fact_pack.get("rails") or []
        if rails:
            rail_rule = (
                "仅当 Function Calling 返回 rails 且 status=ok 时：可在 bookings 中引用其参考车次、"
                "时刻、参考票价，但每条都必须显式附“以 12306 官方实时为准，票价余票请"
                "官方渠道确认”；严禁超出工具字段编造其它车次。"
            )
        else:
            rail_rule = (
                "事实包 rails 为空是正常状态：严禁出现任何具体车次、票价、余票；"
                "如需要火车事实，必须主动调用 query_rail_tickets，否则只能写“建议在"
                " 12306 官方 App 查询并尽早购票/候补”。"
            )
        parts.append(
            "【受控事实包 TravelFactPack】\n"
            "事实数组为空是正常状态，不代表目的地没有相关信息或已经完成查询。所有 A 类高德事实（天气、POI、路线）"
            "与 A′ 火车/机票参考事实，必须由 Function Calling 工具返回后才能写入行程；"
            "不得把空事实包当作已完成查询，也不得编造实时事实。\n"
            + rail_rule
            + "\n"
            + json.dumps(fact_pack, ensure_ascii=False)
        )
    return "\n\n".join(parts)


def build_generate_user_text(*, requirements: str, memory_summary: str) -> str:
    """Compose user text for postcard/report generation tasks."""
    return (
        f"【用户长期旅行偏好摘要】\n{memory_summary}\n\n"
        f"【本次生成需求】\n{requirements}"
    )


def build_report_draft_user_text(
    *,
    analysis: PhotoAnalysisResult,
    requirements: str,
    memory_summary: str,
) -> str:
    """Compose user text for report draft generation.

    Passes the full ``PhotoAnalysisResult`` from the photo understanding step
    unchanged so the report model can ground its output in per-photo semantics.
    """
    return (
        f"【用户长期旅行偏好摘要】\n{memory_summary}\n\n"
        f"【本次生成需求】\n{requirements}\n\n"
        "【照片理解结果 PhotoAnalysisResult（上游照片理解任务的完整 JSON 输出，"
        "请据此撰写报告；勿臆造其中未出现的画面细节）】\n"
        + json.dumps(analysis.model_dump(), ensure_ascii=False)
    )


def build_postcard_selection_user_text(
    *, analysis: PhotoAnalysisResult, requirements: str, memory_summary: str
) -> str:
    """Compose the text-only input for postcard count and source selection.

    Passes the full ``PhotoAnalysisResult`` from the photo understanding step
    (after backend suitability pre-filter) so the model can select only valid
    ``source_asset_ids``. Creative work intentionally happens in a later turn
    that receives the selected original images.
    """
    return (
        f"【用户长期旅行偏好摘要】\n{memory_summary}\n\n"
        f"【本次生成需求】\n{requirements}\n\n"
        "【照片理解结果 PhotoAnalysisResult（上游照片理解任务的完整 JSON 输出；"
        "source_asset_ids 只能从其中 photos[].asset_id 选取，严禁臆造）】\n"
        + json.dumps(analysis.model_dump(), ensure_ascii=False)
    )


def build_postcard_creative_user_text(
    *,
    analysis: PhotoAnalysisResult,
    selection: PostcardSelectionResult,
    selected_asset_ids: list[str],
    requirements: str,
    memory_summary: str,
) -> str:
    """Compose the multimodal creative brief input for selected photos.

    Images are attached after this text in exactly ``selected_asset_ids`` order.
    The explicit manifest lets the model associate each original image with the
    backend asset IDs used by each selected postcard slot.
    """
    image_manifest = "\n".join(
        f"图片{idx}：asset_id={asset_id}"
        for idx, asset_id in enumerate(selected_asset_ids, start=1)
    )
    selection_manifest = "\n".join(
        f"明信片{idx}：source_asset_ids="
        + json.dumps(item.source_asset_ids, ensure_ascii=False)
        for idx, item in enumerate(selection.items, start=1)
    )
    return (
        f"【用户长期旅行偏好摘要】\n{memory_summary}\n\n"
        f"【本次生成需求】\n{requirements}\n\n"
        "【已选明信片与参考照片】\n"
        f"{selection_manifest}\n\n"
        "【随消息附加的原图顺序】\n"
        f"{image_manifest}\n\n"
        "【已选照片的语义信息】\n"
        + json.dumps(analysis.model_dump(), ensure_ascii=False)
    )


def build_photo_analysis_user_text(
    *,
    photo_metas: list[dict],
    requirements: str,
    memory_summary: str,
    batch_index: int | None = None,
    batch_total: int | None = None,
) -> str:
    """Compose user text for photo analysis.

    Includes a per-photo manifest so the model echoes the exact backend
    `asset_id` for each image (images are attached in the same order). Without
    this, the model has no way to return our internal asset ids.

    When ``batch_total > 1``, clarifies that only the current subset is in
    scope so the model does not expect photos from other batches.
    """
    lines = [
        f"本次共有 {len(photo_metas)} 张照片，按顺序与下方图片一一对应。",
        "请在输出的 photos[] 中，对每张照片使用下面给定的 asset_id（严禁臆造其它 ID）：",
    ]
    for idx, meta in enumerate(photo_metas, start=1):
        taken = meta.get("taken_at") or "未知"
        loc = meta.get("location") or "未知"
        lines.append(
            f"{idx}. asset_id={meta.get('asset_id')}，拍摄时间={taken}，地点={loc}"
        )
    manifest = "\n".join(lines)
    parts = [
        f"【用户长期旅行偏好摘要】\n{memory_summary}",
        f"【本次生成需求】\n{requirements}",
    ]
    if batch_index is not None and batch_total is not None and batch_total > 1:
        parts.append(
            f"【分批说明】本次为第 {batch_index}/{batch_total} 批，仅包含本批 "
            f"{len(photo_metas)} 张照片。请只分析本批图片，不得遗漏本批任一 asset_id；"
            "overall_location / start_date / end_date 仅反映本批照片的综合推断，"
            "后端会将多批结果合并为完整 PhotoAnalysisResult。"
        )
    parts.append(f"【照片清单（asset_id 必须原样使用）】\n{manifest}")
    return "\n\n".join(parts)
