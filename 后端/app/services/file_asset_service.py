"""FileAsset lifecycle & reference management.

State machine / reference matrix master definition: 《后端技术栈与全局规范》三.
`file_asset_references` is the single source of truth; `ref_count` is a cache
recomputed from it. Business services must NEVER call `os.remove` — physical
deletion only happens here via `storage_service`. System assets
(`user_id == "system"`) are never released or physically deleted.

These functions operate on a caller-provided `Session` and do NOT commit; the
calling service owns the (short) transaction boundary.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.models.file_asset import FileAsset
from app.models.file_asset_reference import FileAssetReference
from app.services import id_service

logger = logging.getLogger("travelplanet")

SYSTEM_USER_ID = "system"

# Status values.
STATUS_TEMPORARY = "temporary"
STATUS_ATTACHED = "attached"
STATUS_DELETED = "deleted"


def create_temporary(
    session: Session,
    *,
    user_id: str,
    relative_path: str,
    mime_type: str,
    size_bytes: int,
    usage_type: str,
) -> FileAsset:
    """Create a `temporary` FileAsset right after a file lands on disk."""
    asset = FileAsset(
        id=id_service.new_asset_id(),
        user_id=user_id,
        relative_path=relative_path,
        mime_type=mime_type,
        size_bytes=size_bytes,
        usage_type=usage_type,
        status=STATUS_TEMPORARY,
        ref_count=0,
    )
    session.add(asset)
    session.flush()
    return asset


def get_asset(session: Session, asset_id: str) -> FileAsset | None:
    return session.get(FileAsset, asset_id)


def attach_with_reference(
    session: Session,
    *,
    asset_id: str,
    user_id: str,
    owner_type: str,
    owner_id: str,
    role: str,
) -> FileAssetReference | None:
    """Create a reference and flip the asset to `attached` in one transaction.

    Idempotent on the unique `(asset_id, owner_type, owner_id, role)` tuple.
    Recomputes `ref_count` from the reference table. Returns the (existing or
    new) reference, or None if the asset does not exist.
    """
    asset = session.get(FileAsset, asset_id)
    if asset is None:
        logger.warning("attach_with_reference: asset %s not found", asset_id)
        return None

    existing = session.exec(
        select(FileAssetReference).where(
            FileAssetReference.asset_id == asset_id,
            FileAssetReference.owner_type == owner_type,
            FileAssetReference.owner_id == owner_id,
            FileAssetReference.role == role,
        )
    ).first()

    reference = existing
    if existing is None:
        reference = FileAssetReference(
            id=id_service.new_reference_id(),
            user_id=user_id,
            asset_id=asset_id,
            owner_type=owner_type,
            owner_id=owner_id,
            role=role,
        )
        session.add(reference)
        session.flush()

    asset.status = STATUS_ATTACHED
    session.add(asset)
    recompute_ref_count(session, asset_id)
    return reference


def release(session: Session, *, owner_type: str, owner_id: str) -> list[str]:
    """Release all references owned by `(owner_type, owner_id)`.

    Returns the list of affected asset ids (for a subsequent safe-delete sweep).
    System assets are skipped entirely.
    """
    references = session.exec(
        select(FileAssetReference).where(
            FileAssetReference.owner_type == owner_type,
            FileAssetReference.owner_id == owner_id,
        )
    ).all()

    affected: list[str] = []
    for ref in references:
        asset = session.get(FileAsset, ref.asset_id)
        if asset is not None and asset.user_id == SYSTEM_USER_ID:
            # Never release/touch system assets (e.g. default cover).
            continue
        session.delete(ref)
        affected.append(ref.asset_id)

    session.flush()
    for asset_id in set(affected):
        recompute_ref_count(session, asset_id)
    return list(set(affected))


def recompute_ref_count(session: Session, asset_id: str) -> int:
    """Recount references for an asset and sync the cached `ref_count`."""
    asset = session.get(FileAsset, asset_id)
    if asset is None:
        return 0
    count = len(
        session.exec(
            select(FileAssetReference).where(FileAssetReference.asset_id == asset_id)
        ).all()
    )
    asset.ref_count = count
    session.add(asset)
    session.flush()
    return count


def safe_delete_if_unreferenced(session: Session, asset_id: str) -> bool:
    """Physically delete an asset only if it has no references.

    Re-queries the reference table (never trusts the cached count alone). System
    assets are skipped. Missing physical files do not crash the caller. Returns
    True if the asset was physically removed / marked deleted.
    """
    from app.services import storage_service

    asset = session.get(FileAsset, asset_id)
    if asset is None:
        return False
    if asset.user_id == SYSTEM_USER_ID:
        return False

    count = recompute_ref_count(session, asset_id)
    if count > 0:
        return False

    # No references: remove physical file (tolerate missing) and mark deleted.
    storage_service.delete_physical_file(asset.relative_path)
    asset.status = STATUS_DELETED
    from app.models.base import utcnow

    asset.deleted_at = utcnow()
    session.add(asset)
    session.flush()
    return True


def release_and_cleanup(session: Session, *, owner_type: str, owner_id: str) -> None:
    """Convenience: release an owner's references then sweep-delete orphans."""
    affected = release(session, owner_type=owner_type, owner_id=owner_id)
    for asset_id in affected:
        safe_delete_if_unreferenced(session, asset_id)


def discard_temporary(session: Session, asset_ids: list[str]) -> None:
    """Release未绑定的 temporary 资产（生成失败时清理孤儿）。

    For assets still `temporary` with no references, delete the physical file
    and mark deleted. System assets are skipped.
    """
    for asset_id in asset_ids:
        asset = session.get(FileAsset, asset_id)
        if asset is None or asset.user_id == SYSTEM_USER_ID:
            continue
        count = recompute_ref_count(session, asset_id)
        if count == 0:
            safe_delete_if_unreferenced(session, asset_id)
