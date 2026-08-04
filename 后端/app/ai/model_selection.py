"""Stable planning-model identifiers shared by DTOs and AI orchestration."""

from __future__ import annotations

from typing import Literal, TypeAlias

PlanningModel: TypeAlias = Literal[
    "doubao-seed-2.0-pro",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
]

DEFAULT_PLANNING_MODEL: PlanningModel = "doubao-seed-2.0-pro"
DEEPSEEK_PLANNING_MODELS: frozenset[PlanningModel] = frozenset(
    {"deepseek-v4-flash", "deepseek-v4-pro"}
)


def requires_reasoning_replay(model: PlanningModel) -> bool:
    """Whether tool turns must replay provider reasoning in later requests."""
    return model in DEEPSEEK_PLANNING_MODELS
