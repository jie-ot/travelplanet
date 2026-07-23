"""Prompt template loading. All model prompts live here as `.md` files."""

from __future__ import annotations

import os
from functools import lru_cache

_PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))


@lru_cache
def load_prompt(name: str) -> str:
    """Load a prompt template by file name (e.g. 'planning_system.md')."""
    path = os.path.join(_PROMPTS_DIR, name)
    with open(path, encoding="utf-8") as fp:
        return fp.read()
