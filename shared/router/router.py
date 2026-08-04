"""Shared journey router; overlays only add contextual skill entry points."""

from __future__ import annotations

from typing import Literal

Journey = Literal["software", "research-exploration", "hybrid"]


def route_for(kind: str, starting_point: str = "") -> dict[str, str]:
    """Resolve a journey without assuming the user starts from a software repository."""
    if kind not in {"software", "research-exploration", "hybrid"}:
        raise ValueError(f"unsupported journey kind: {kind}")
    return {
        "kind": kind,
        "starting_point": starting_point,
        "plan_skill": "research-routes-plan",
        "run_skill": "research-routes-run",
        "explore_skill": "research-routes-explore",
    }
