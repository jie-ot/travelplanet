"""`/api/generate` (#5) main flow (《任务详细流程规范》六).

Validate photos/options → read memory → photo understanding → postcard and/or
report sub-flows → on overall success a SHORT transaction writes
PostcardGroup/Postcard/Report + references and flips assets to `attached` →
deterministic memory增量. Model/tool/download phases never hold a DB
transaction. Any failure discards this request's temporary assets (generated +
uploaded source) to avoid orphans. Error codes: 1001 (generation/content),
1002 (param/path/ownership), 1003 (config/download/disk).
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock

from sqlmodel import Session

from app.ai import orchestrator
from app.ai.schemas import (
    MemoryUpdateResult,
    PhotoAnalysisResult,
    PostcardPlanItem,
    ReportDraftResult,
)
from app.core.exceptions import (
    AIGenerationError,
    BusinessError,
    ImageInputPolicyError,
    InternalError,
    InvalidParamError,
)
from app.core.config import settings
from app.core.business_logging import call_in_current_context, log_event, timed_stage
from app.db.session import session_scope
from app.models import dto
from app.models.dto import RADAR_DIMENSIONS
from app.models.file_asset import FileAsset
from app.models.postcard import Postcard as PostcardEntity
from app.models.postcard_group import PostcardGroup as PostcardGroupEntity
from app.models.report import Report as ReportEntity
from app.services import (
    date_label_service,
    file_asset_service,
    id_service,
    mappers,
    memory_service,
    storage_service,
)
from app.services.storage_service import PREFIX_UPLOADS

logger = logging.getLogger("travelplanet")

# Backend safety boundary only: the postcard count decision belongs to the
# model (driven by the prompt). The backend never parses the count from the
# user's natural-language requirement text.
MAX_POSTCARD_COUNT = 5


def _bounded_workers(item_count: int, configured: int) -> int:
    return max(1, min(item_count, max(1, configured)))


def _validate_and_load_photos(
    session: Session, user_id: str, photos: list[dto.UploadedPhoto]
) -> None:
    """Validate ownership + path + relative_path match for each photo (1002)."""
    for photo in photos:
        if not photo.image_url.startswith(PREFIX_UPLOADS) or ".." in photo.image_url:
            raise InvalidParamError("照片路径不合法")
        asset = session.get(FileAsset, photo.asset_id)
        if asset is None or asset.user_id != user_id:
            raise InvalidParamError("照片资源不存在或不属于当前用户")
        if asset.relative_path != photo.image_url:
            raise InvalidParamError("照片 assetId 与 imageUrl 不匹配")


def _validate_analysis(analysis: PhotoAnalysisResult, source_ids: set[str]) -> None:
    if not analysis.photos:
        raise AIGenerationError("AI 生成失败：照片分析结果为空")
    for item in analysis.photos:
        if item.asset_id not in source_ids:
            raise AIGenerationError("AI 生成失败：分析结果包含非本次照片")


def _enforce_postcard_plan(
    items: list[PostcardPlanItem], source_ids: set[str]
) -> list[PostcardPlanItem]:
    """Apply the backend safety boundary to the model's postcard plan.

    Boundary only (no NL count parsing): the model decides the count via the
    prompt. If it returns more than 5, truncate to 5; if empty, treat as a
    generation failure. Then validate each item's fields and source assets.
    """
    if len(items) > MAX_POSTCARD_COUNT:
        items = items[:MAX_POSTCARD_COUNT]
    if not items:
        raise AIGenerationError("AI 生成失败：明信片计划为空")
    for item in items:
        if not item.title.strip() or not item.image_prompt.strip():
            raise AIGenerationError("AI 生成失败：明信片标题或绘图描述为空")
        if len(item.extra_texts) > 2 or any(
            not text.strip() for text in item.extra_texts
        ):
            raise AIGenerationError("AI 生成失败：明信片辅助文案不合法")
        if any(
            not value.strip()
            for value in (
                item.design_concept,
                item.photo_transformation,
                item.visual_device,
                item.typography,
            )
        ):
            raise AIGenerationError("AI 生成失败：明信片创意方案不完整")
        if "明信片设计" not in item.image_prompt:
            raise AIGenerationError("AI 生成失败：创意指令未突出明信片设计")
        if not item.source_asset_ids or any(a not in source_ids for a in item.source_asset_ids):
            raise AIGenerationError("AI 生成失败：明信片参考照片非本次上传")
    return items


def _validate_report(draft: ReportDraftResult) -> None:
    if not draft.location.strip() or not draft.personality_summary.strip() or not draft.content.strip():
        raise AIGenerationError("AI 生成失败：报告字段不完整")
    narrative_chars = len("".join(draft.content.split()))
    narrative_blocks = [
        block.strip()
        for block in draft.content.replace("\r\n", "\n").split("\n")
        if block.strip()
    ]
    if not 180 <= narrative_chars <= 240:
        raise AIGenerationError("AI 生成失败：人格正文应为 180-240 字")
    required_markers = ("瞬间一｜", "瞬间二｜", "瞬间三｜", "人格判词｜")
    if len(narrative_blocks) != 5 or any(
        not narrative_blocks[index].startswith(marker)
        for index, marker in enumerate(required_markers, start=1)
    ):
        raise AIGenerationError("AI 生成失败：人格正文必须包含引言、三个旅行瞬间和人格判词")
    dims = [point.dimension for point in draft.chart_data]
    if list(dims) != list(RADAR_DIMENSIONS):
        raise AIGenerationError("AI 生成失败：雷达图维度必须完整且顺序固定")
    for point in draft.chart_data:
        if not (0 <= point.value <= 100):
            raise AIGenerationError("AI 生成失败：雷达图分值超出 0-100")
    profile = draft.profile_data
    if [item.id for item in profile.spectrums] != [
        "environment",
        "depth",
        "planning",
        "social",
    ]:
        raise AIGenerationError("AI 生成失败：旅行光谱必须完整且顺序固定")
    if any(not 0 <= item.value <= 100 for item in profile.spectrums):
        raise AIGenerationError("AI 生成失败：旅行光谱分值超出 0-100")
    if not 3 <= len(profile.keywords) <= 5 or len(profile.modules) != 3:
        raise AIGenerationError("AI 生成失败：人格关键词或模块数量不合法")
    if any(not 30 <= len(item.content.strip()) <= 60 for item in profile.modules):
        raise AIGenerationError("AI 生成失败：人格模块应为 30-60 字")


def generate(user_id: str, request: dto.GenerateRequest) -> dto.GenerateResult:
    """Run the full generate flow and return {postcardGroup, report}."""
    options = request.options
    if not options.generate_postcards and not options.generate_report:
        raise InvalidParamError("请至少选择生成明信片或报告其一")
    if not request.photos:
        raise InvalidParamError("请至少选择一张照片")

    source_asset_ids = [p.asset_id for p in request.photos]
    source_id_set = set(source_asset_ids)

    # —— Validation + memory read (no model calls held in a write txn) ——
    with timed_stage("generate_validate_and_memory", photo_count=len(request.photos)):
        with session_scope() as session:
            _validate_and_load_photos(session, user_id, request.photos)
            memory = memory_service.get_or_create_current_memory(session, user_id)
            memory_summary = memory_service.build_memory_summary(memory)

    # —— Prepare base64 inputs (file I/O in service layer) ——
    photo_metas = [
        {
            "asset_id": p.asset_id,
            "image_url": p.image_url,
            "taken_at": p.taken_at,
            "location": p.location,
        }
        for p in request.photos
    ]
    with timed_stage("generate_read_source_images", photo_count=len(request.photos)):
        image_data_urls = _read_image_data_urls([p.image_url for p in request.photos])
    data_url_by_asset = dict(zip(source_asset_ids, image_data_urls, strict=True))

    generated_temp_asset_ids: list[str] = []
    generated_temp_lock = Lock()
    try:
        with timed_stage("generate_photo_analysis", photo_count=len(photo_metas)):
            analysis = orchestrator.analyze_photos(
                photo_metas=photo_metas,
                image_data_urls=image_data_urls,
                requirements=request.requirements,
                memory_summary=memory_summary,
            )
            _validate_analysis(analysis, source_id_set)
        log_event(
            "generate_photo_analysis_result",
            status="success",
            location=analysis.overall_location,
            start_date=analysis.start_date,
            end_date=analysis.end_date,
            photo_count=len(analysis.photos),
            suitability_counts={
                level: sum(photo.suitability == level for photo in analysis.photos)
                for level in ("good", "usable", "unsuitable")
            },
            location_guess_count=sum(
                photo.location_guess is not None for photo in analysis.photos
            ),
            taken_date_guess_count=sum(
                photo.taken_date_guess is not None for photo in analysis.photos
            ),
            photo_summaries=[
                {
                    "asset_id": photo.asset_id,
                    "suitability": photo.suitability,
                    "location_guess": photo.location_guess,
                    "taken_date_guess": photo.taken_date_guess,
                    "scene_summary": photo.scene_summary[:300],
                    "postcard_reason": (photo.postcard_reason or "")[:300],
                    "report_reason": (photo.report_reason or "")[:300],
                }
                for photo in analysis.photos
            ],
        )

        date_label = date_label_service.generate_label(analysis.start_date, analysis.end_date)
        location = (analysis.overall_location or "未知目的地").strip() or "未知目的地"

        # —— Independent model sub-flows. Keep the public endpoint synchronous,
        # but overlap report drafting, memory proposal, and postcard rendering.
        postcard_renders: list[tuple[str, str, int]] = []  # (title, relative_path, sort)
        postcard_asset_ids: list[str] = []  # parallel to renders
        report_draft: ReportDraftResult | None = None
        memory_future: Future[MemoryUpdateResult] | None = None

        with ThreadPoolExecutor(max_workers=3) as executor:
            memory_future = executor.submit(
                call_in_current_context(
                    _propose_memory_update,
                    location=location,
                    analysis=analysis,
                    requirements=request.requirements,
                )
            )
            postcard_future: Future[tuple[list[tuple[str, str, int]], list[str]]] | None = None
            report_future: Future[ReportDraftResult] | None = None

            if options.generate_postcards:
                postcard_future = executor.submit(
                    call_in_current_context(
                        _generate_postcards,
                        user_id=user_id,
                        analysis=analysis,
                        requirements=request.requirements,
                        memory_summary=memory_summary,
                        source_id_set=source_id_set,
                        data_url_by_asset=data_url_by_asset,
                        fallback_image_data_urls=image_data_urls,
                        generated_temp_asset_ids=generated_temp_asset_ids,
                        generated_temp_lock=generated_temp_lock,
                    )
                )
            if options.generate_report:
                report_future = executor.submit(
                    call_in_current_context(
                        _draft_and_validate_report,
                        analysis=analysis,
                        requirements=request.requirements,
                        memory_summary=memory_summary,
                    )
                )

            if postcard_future is not None:
                postcard_renders, postcard_asset_ids = postcard_future.result()
            if report_future is not None:
                report_draft = report_future.result()

        # —— SHORT WRITE TRANSACTION ——
        with timed_stage(
            "generate_persist_results",
            postcard_count=len(postcard_renders),
            has_report=report_draft is not None,
            report_profile_version=2 if report_draft is not None else None,
            report_archetype=(
                report_draft.profile_data.archetype_name
                if report_draft is not None
                else None
            ),
            report_visual_theme=(
                report_draft.profile_data.visual_theme
                if report_draft is not None
                else None
            ),
        ):
            with session_scope() as session:
                result = _persist_results(
                    session,
                    user_id=user_id,
                    options=options,
                    location=location,
                    date_label=date_label,
                    start_date=analysis.start_date,
                    end_date=analysis.end_date,
                    source_asset_ids=source_asset_ids,
                    postcard_renders=postcard_renders,
                    postcard_asset_ids=postcard_asset_ids,
                    report_draft=report_draft,
                )
    except BusinessError:
        log_event(
            "generate_discard_assets",
            status="start",
            reason="business_error",
            generated_asset_count=len(generated_temp_asset_ids),
            source_asset_count=len(source_asset_ids),
        )
        _discard(generated_temp_asset_ids + source_asset_ids)
        raise
    except Exception as exc:  # noqa: BLE001
        log_event(
            "generate_discard_assets",
            status="start",
            reason="unexpected_error",
            error_type=type(exc).__name__,
            generated_asset_count=len(generated_temp_asset_ids),
            source_asset_count=len(source_asset_ids),
        )
        _discard(generated_temp_asset_ids + source_asset_ids)
        raise InternalError("生成过程发生内部错误") from exc

    # —— Memory increment (independent transaction; non-fatal on failure) ——
    with timed_stage("generate_merge_memory"):
        _merge_memory_from_future(user_id, memory_future)
    return result


def _read_image_data_urls(image_urls: list[str]) -> list[str]:
    if len(image_urls) <= 1:
        return [storage_service.read_file_as_base64_data_url(url) for url in image_urls]
    workers = _bounded_workers(len(image_urls), settings.GENERATION_FILE_IO_PARALLELISM)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(storage_service.read_file_as_base64_data_url, image_urls))


def _draft_and_validate_report(
    *,
    analysis: PhotoAnalysisResult,
    requirements: str,
    memory_summary: str,
) -> ReportDraftResult:
    diagnostics: dict[str, object] = {}
    with timed_stage(
        "generate_draft_report",
        profile_version=2,
        source_photo_count=len(analysis.photos),
        requirements_chars=len(requirements),
        memory_summary_chars=len(memory_summary),
        diagnostics=diagnostics,
    ):
        draft = orchestrator.draft_report(
            analysis=analysis,
            requirements=requirements,
            memory_summary=memory_summary,
        )
        _validate_report(draft)
        profile = draft.profile_data
        diagnostics.update(
            {
                "location": draft.location,
                "start_date": draft.start_date,
                "end_date": draft.end_date,
                "personality_summary": draft.personality_summary,
                "archetype_id": profile.archetype_id,
                "archetype_name": profile.archetype_name,
                "persona_code": profile.persona_code,
                "visual_theme": profile.visual_theme,
                "slogan_chars": len(profile.slogan),
                "spectrums": [
                    {"id": item.id, "value": item.value}
                    for item in profile.spectrums
                ],
                "keywords": profile.keywords,
                "keyword_count": len(profile.keywords),
                "modules": [
                    {
                        "title": item.title,
                        "content_chars": len(item.content),
                        "content_excerpt": item.content[:180],
                    }
                    for item in profile.modules
                ],
                "module_count": len(profile.modules),
                "content_chars": len(draft.content),
                "narrative_chars_without_whitespace": len(
                    "".join(draft.content.split())
                ),
                "narrative_block_count": len(
                    [
                        block
                        for block in draft.content.replace("\r\n", "\n").split("\n")
                        if block.strip()
                    ]
                ),
                "narrative_markers": [
                    marker
                    for marker in ("瞬间一｜", "瞬间二｜", "瞬间三｜", "人格判词｜")
                    if marker in draft.content
                ],
                "content_excerpt": draft.content[:500],
                "chart_data": [
                    point.model_dump(by_alias=True) for point in draft.chart_data
                ],
                "next_trip_inspiration": profile.next_trip_inspiration[:400],
            }
        )
    return draft


def _generate_postcards(
    *,
    user_id: str,
    analysis: PhotoAnalysisResult,
    requirements: str,
    memory_summary: str,
    source_id_set: set[str],
    data_url_by_asset: dict[str, str],
    fallback_image_data_urls: list[str],
    generated_temp_asset_ids: list[str],
    generated_temp_lock: Lock,
) -> tuple[list[tuple[str, str, int]], list[str]]:
    with timed_stage("generate_select_postcard_photos"):
        selection = orchestrator.select_postcard_photos(
            analysis=analysis,
            requirements=requirements,
            memory_summary=memory_summary,
        )
    selected_asset_ids = list(
        dict.fromkeys(
            asset_id
            for item in selection.items
            for asset_id in item.source_asset_ids
        )
    )
    log_event(
        "generate_postcard_selection_result",
        status="success",
        postcard_count=len(selection.items),
        selected_asset_ids=selected_asset_ids,
    )

    with timed_stage(
        "generate_create_postcard_creative",
        postcard_count=len(selection.items),
        selected_photo_count=len(selected_asset_ids),
    ):
        plan = orchestrator.create_postcard_creative_plan(
            analysis=analysis,
            selection=selection,
            selected_asset_ids=selected_asset_ids,
            image_data_urls=[data_url_by_asset[asset_id] for asset_id in selected_asset_ids],
            requirements=requirements,
            memory_summary=memory_summary,
        )
        plan_items = _enforce_postcard_plan(list(plan.items), source_id_set)
    log_event("generate_postcard_plan_result", status="success", postcard_count=len(plan_items))
    if len(plan_items) == 1:
        try:
            render, asset_id = _render_and_store_postcard(
                user_id=user_id,
                idx=0,
                item=plan_items[0],
                data_url_by_asset=data_url_by_asset,
                fallback_image_data_urls=fallback_image_data_urls,
                generated_temp_asset_ids=generated_temp_asset_ids,
                generated_temp_lock=generated_temp_lock,
            )
        except ImageInputPolicyError as exc:
            _log_skipped_postcard(0, plan_items[0], exc)
            raise AIGenerationError("所有明信片均因图片内容审核未通过而跳过") from exc
        return [render], [asset_id]

    workers = _bounded_workers(len(plan_items), settings.GENERATION_IMAGE_PARALLELISM)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                call_in_current_context(
                    _render_and_store_postcard,
                    user_id=user_id,
                    idx=idx,
                    item=item,
                    data_url_by_asset=data_url_by_asset,
                    fallback_image_data_urls=fallback_image_data_urls,
                    generated_temp_asset_ids=generated_temp_asset_ids,
                    generated_temp_lock=generated_temp_lock,
                ),
            )
            for idx, item in enumerate(plan_items)
        ]
        results: list[tuple[tuple[str, str, int], str]] = []
        for idx, (item, future) in enumerate(zip(plan_items, futures, strict=True)):
            try:
                results.append(future.result())
            except ImageInputPolicyError as exc:
                _log_skipped_postcard(idx, item, exc)
    if not results:
        raise AIGenerationError("所有明信片均因图片内容审核未通过而跳过")
    postcard_renders = [render for render, _ in results]
    postcard_asset_ids = [asset_id for _, asset_id in results]
    return postcard_renders, postcard_asset_ids


def _log_skipped_postcard(
    index: int,
    item: PostcardPlanItem,
    exc: ImageInputPolicyError,
) -> None:
    log_event(
        "generate_render_postcard_skipped",
        status="skipped",
        index=index,
        title=item.title,
        source_asset_ids=item.source_asset_ids,
        reason="input_policy_violation_after_sanitized_retry",
        message=str(exc),
    )


def _render_and_store_postcard(
    *,
    user_id: str,
    idx: int,
    item: PostcardPlanItem,
    data_url_by_asset: dict[str, str],
    fallback_image_data_urls: list[str],
    generated_temp_asset_ids: list[str],
    generated_temp_lock: Lock,
) -> tuple[tuple[str, str, int], str]:
    with timed_stage("generate_render_postcard", index=idx, title=item.title):
        ref_urls = [
            data_url_by_asset[a]
            for a in item.source_asset_ids
            if a in data_url_by_asset
        ]
        if not ref_urls:
            ref_urls = fallback_image_data_urls
        img = orchestrator.render_postcard_image(
            prompt=item.image_prompt,
            image_data_urls=ref_urls,
            design_concept=item.design_concept,
            photo_transformation=item.photo_transformation,
            visual_device=item.visual_device,
            typography=item.typography,
            title=item.title,
            extra_texts=item.extra_texts,
        )
        stored = _persist_generated_image(img, kind="postcard")
        with session_scope() as session:
            asset = file_asset_service.create_temporary(
                session,
                user_id=user_id,
                relative_path=stored.relative_path,
                mime_type=stored.mime_type,
                size_bytes=stored.size_bytes,
                usage_type="generated_postcard",
            )
            new_asset_id = asset.id
    with generated_temp_lock:
        generated_temp_asset_ids.append(new_asset_id)
    log_event(
        "generate_render_postcard_result",
        status="success",
        index=idx,
        asset_id=new_asset_id,
        relative_path=stored.relative_path,
    )
    return (item.title, stored.relative_path, idx), new_asset_id


def _persist_generated_image(img, kind: str) -> storage_service.StoredFile:
    """Persist a model image result by downloading its remote temporary URL."""
    if kind == "postcard":
        relative_path = storage_service.build_postcard_relative_path(img.ext)
    else:
        relative_path = storage_service.build_report_cover_relative_path(img.ext)
    if img.image_url:
        return storage_service.download_to_static(img.image_url, relative_path)
    raise InternalError("生成图结果为空")


def _persist_results(
    session: Session,
    *,
    user_id: str,
    options: dto.GenerateOptions,
    location: str,
    date_label: str,
    start_date: str | None,
    end_date: str | None,
    source_asset_ids: list[str],
    postcard_renders: list[tuple[str, str, int]],
    postcard_asset_ids: list[str],
    report_draft: ReportDraftResult | None,
) -> dto.GenerateResult:
    """Write all business records + references in one short transaction."""
    group_dto: dto.PostcardGroup | None = None
    first_postcard_path: str | None = None
    first_postcard_asset_id: str | None = None

    if options.generate_postcards and postcard_renders:
        first_postcard_path = postcard_renders[0][1]
        first_postcard_asset_id = postcard_asset_ids[0]
        group = PostcardGroupEntity(
            id=id_service.new_postcard_group_id(),
            user_id=user_id,
            location=location,
            start_date=start_date,
            end_date=end_date,
            date_label=date_label,
            cover_image=first_postcard_path,
        )
        session.add(group)
        session.flush()

        postcard_entities: list[PostcardEntity] = []
        for (title, rel_path, sort), asset_id in zip(postcard_renders, postcard_asset_ids):
            pc = PostcardEntity(
                id=id_service.new_postcard_id(),
                user_id=user_id,
                group_id=group.id,
                title=title,
                image_url=rel_path,
                sort_order=sort,
            )
            session.add(pc)
            session.flush()
            postcard_entities.append(pc)
            file_asset_service.attach_with_reference(
                session,
                asset_id=asset_id,
                user_id=user_id,
                owner_type="postcard",
                owner_id=pc.id,
                role="postcard_image",
            )
        # Bind source photos to the group.
        for sid in source_asset_ids:
            file_asset_service.attach_with_reference(
                session,
                asset_id=sid,
                user_id=user_id,
                owner_type="postcard_group",
                owner_id=group.id,
                role="source_photo",
            )
        group_dto = mappers.postcard_group_to_dto(group, postcard_entities)

    report_dto: dto.Report | None = None
    if options.generate_report and report_draft is not None:
        cover_image, cover_asset_id = _resolve_report_cover(
            first_postcard_path, first_postcard_asset_id, source_asset_ids, session
        )
        report = ReportEntity(
            id=id_service.new_report_id(),
            user_id=user_id,
            location=report_draft.location or location,
            start_date=report_draft.start_date,
            end_date=report_draft.end_date,
            date_label=date_label,
            cover_image=cover_image,
            personality_summary=report_draft.personality_summary,
            content=report_draft.content,
            chart_data=[point.model_dump() for point in report_draft.chart_data],
            profile_version=2,
            profile_data=report_draft.profile_data.model_dump(by_alias=True),
        )
        session.add(report)
        session.flush()
        # report_cover reference (skip system default cover).
        if cover_asset_id is not None and cover_image != storage_service.DEFAULT_COVER_PATH:
            file_asset_service.attach_with_reference(
                session,
                asset_id=cover_asset_id,
                user_id=user_id,
                owner_type="report",
                owner_id=report.id,
                role="report_cover",
            )
        # Bind source photos to the report.
        for sid in source_asset_ids:
            file_asset_service.attach_with_reference(
                session,
                asset_id=sid,
                user_id=user_id,
                owner_type="report",
                owner_id=report.id,
                role="source_photo",
            )
        report_dto = mappers.report_to_dto(report)

    return dto.GenerateResult(postcard_group=group_dto, report=report_dto)


def _resolve_report_cover(
    first_postcard_path: str | None,
    first_postcard_asset_id: str | None,
    source_asset_ids: list[str],
    session: Session,
) -> tuple[str, str | None]:
    """Pick the report cover per 6.4: first postcard → representative upload →
    default cover."""
    if first_postcard_path and first_postcard_asset_id:
        return first_postcard_path, first_postcard_asset_id
    # Report-only: use the first uploaded photo as the representative cover.
    for sid in source_asset_ids:
        asset = session.get(FileAsset, sid)
        if asset is not None:
            return asset.relative_path, sid
    return storage_service.DEFAULT_COVER_PATH, None


def _discard(asset_ids: list[str]) -> None:
    """Release this request's temporary assets after a failure."""
    try:
        with session_scope() as session:
            file_asset_service.discard_temporary(session, list(set(asset_ids)))
    except Exception:  # noqa: BLE001
        logger.exception("failed to discard temporary assets after generate failure")


def _propose_memory_update(
    *,
    location: str,
    analysis: PhotoAnalysisResult,
    requirements: str,
) -> MemoryUpdateResult:
    useful_photos = [photo for photo in analysis.photos if photo.suitability != "unsuitable"]
    scene_lines = [
        f"- {photo.scene_summary}"
        for photo in useful_photos[:8]
        if photo.scene_summary.strip()
    ]
    evidence = "\n".join(
        [
            f"用户完成了一次旅行内容生成，整体地点推断：{location}。",
            f"用户需求：{requirements.strip()[:240] or '无明确额外需求'}",
            "照片和创作证据：",
            *scene_lines,
            "请只提炼可复用的长期旅行偏好，不要把地点名称本身当作偏好。",
        ]
    )
    return orchestrator.propose_memory_update(
        source_task="generate",
        location=location,
        evidence_summary=evidence,
    )


def _merge_memory_from_future(
    user_id: str,
    future: Future[MemoryUpdateResult] | None,
) -> None:
    if future is None:
        return
    try:
        update = future.result()
        with session_scope() as session:
            memory_service.merge_memory_update(
                session,
                user_id=user_id,
                add_preferences=update.add_preferences,
                weaken_preferences=update.weaken_preferences,
                evidence_summary=update.evidence_summary,
                confidence=update.confidence,
                source_type="generate",
                source_id=None,
            )
    except Exception:  # noqa: BLE001
        logger.exception("memory merge failed after generate (non-fatal)")
