"""Demo data initialization.

Runs `alembic upgrade head` first (never creates tables directly), then seeds
the deterministic baseline: the demo user's blank `user_memories` document and
the system default-cover `FileAsset` (`/static/images/default_cover.jpg`,
`user_id="system"`, `usage_type="system"`, `status="attached"`, no reference).
Idempotent on an empty or already-seeded database.

Run:  uv run python scripts/seed_demo.py
"""

from __future__ import annotations

import os
import sys

# Ensure project root on path when invoked as a script.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_ROOT, "alembic.ini"))
    command.upgrade(cfg, "head")


def main() -> None:
    print("[seed_demo] applying migrations (alembic upgrade head)...")
    _run_migrations()

    from sqlmodel import Session

    from app.core.config import settings
    from app.db.session import engine
    from app.services.bootstrap_service import ensure_demo_baseline

    print(f"[seed_demo] seeding baseline for user '{settings.DEFAULT_USER_ID}'...")
    with Session(engine) as session:
        ensure_demo_baseline(session)

    print("[seed_demo] done.")


if __name__ == "__main__":
    main()
