"""Minimal-mode skill loading configuration for ZBrush."""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from dcc_mcp_core import MinimalModeConfig

MINIMAL_SKILLS: Tuple[str, ...] = ("zbrush-scripting", "zbrush-scene")

STAGES: Tuple[str, ...] = (
    "bootstrap",
    "scene",
    "authoring",
    "interchange",
    "pipeline",
)

STAGE_SKILLS: dict[str, Tuple[str, ...]] = {
    "bootstrap": ("zbrush-scripting",),
    "scene": ("zbrush-scene",),
    "authoring": ("zbrush-subtool",),
    "interchange": ("zbrush-interchange",),
    "pipeline": (),
}


def skills_for_stage(stage: str) -> Tuple[str, ...]:
    return STAGE_SKILLS.get(stage, ())


def build_minimal_mode_config(
    skill_names: Optional[Iterable[str]] = None,
) -> MinimalModeConfig:
    skills = tuple(skill_names) if skill_names is not None else MINIMAL_SKILLS
    return MinimalModeConfig(skills=skills, deactivate_groups=())


def build_minimal_mode_for_stages(stages: Iterable[str]) -> MinimalModeConfig:
    names: list[str] = []
    seen: set[str] = set()
    for stage in stages:
        for skill in skills_for_stage(stage):
            if skill not in seen:
                seen.add(skill)
                names.append(skill)
    for skill in STAGE_SKILLS.get("bootstrap", ()):
        if skill not in seen:
            names.insert(0, skill)
    return MinimalModeConfig(skills=tuple(names), deactivate_groups=())
