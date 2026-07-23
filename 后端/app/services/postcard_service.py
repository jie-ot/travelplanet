"""PostcardGroup business service: list + delete (《任务详细流程规范》四、十).

All queries filter by `user_id`. Deletion releases references via
`FileAssetService` (never `os.remove`); the system default cover is skipped
automatically inside the reference release.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.core.exceptions import NotFoundError
from app.db.session import session_scope
from app.models import dto
from app.models.postcard import Postcard as PostcardEntity
from app.models.postcard_group import PostcardGroup as PostcardGroupEntity
from app.services import file_asset_service, mappers


def list_postcard_groups(session: Session, user_id: str) -> list[dto.PostcardGroup]:
    """List the user's postcard groups, newest first, with their postcards."""
    groups = session.exec(
        select(PostcardGroupEntity)
        .where(PostcardGroupEntity.user_id == user_id)
        .order_by(PostcardGroupEntity.created_at.desc())
    ).all()

    result: list[dto.PostcardGroup] = []
    for group in groups:
        postcards = session.exec(
            select(PostcardEntity)
            .where(
                PostcardEntity.group_id == group.id,
                PostcardEntity.user_id == user_id,
            )
            .order_by(PostcardEntity.sort_order.asc())
        ).all()
        result.append(mappers.postcard_group_to_dto(group, list(postcards)))
    return result


def delete_postcard_group(user_id: str, group_id: str) -> None:
    """Delete a postcard group, its postcards, and release all references."""
    with session_scope() as session:
        group = session.exec(
            select(PostcardGroupEntity).where(
                PostcardGroupEntity.id == group_id,
                PostcardGroupEntity.user_id == user_id,
            )
        ).first()
        if group is None:
            raise NotFoundError("明信片组不存在或已被删除")

        postcards = session.exec(
            select(PostcardEntity).where(
                PostcardEntity.group_id == group_id,
                PostcardEntity.user_id == user_id,
            )
        ).all()

        # Release references first (group source photos + per-postcard images).
        file_asset_service.release_and_cleanup(
            session, owner_type="postcard_group", owner_id=group_id
        )
        for pc in postcards:
            file_asset_service.release_and_cleanup(
                session, owner_type="postcard", owner_id=pc.id
            )

        # Delete business rows (postcards before group due to FK).
        for pc in postcards:
            session.delete(pc)
        session.delete(group)
        session.flush()
