"""Ownership conflict and dependency graph primitives."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from ..core.policy import overlaps


def _paths(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        candidate = value.get("paths", value.get("ownership", value.get("write_set", ())))
        if isinstance(candidate, str) and candidate.startswith("["):
            import json

            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError:
                candidate = ()
        return tuple(str(item) for item in (candidate or ()))
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def path_overlaps(left: str, right: str) -> bool:
    return overlaps(left, right)


def ownership_conflict(left: Any, right: Any) -> bool:
    return any(path_overlaps(a, b) for a in _paths(left) for b in _paths(right))


def filter_ownership_conflicts(items: Sequence[Any], active: Sequence[Any] = ()) -> list[Any]:
    selected: list[Any] = []
    occupied = list(active)
    for item in items:
        raw = item if isinstance(item, Mapping) else vars(item)
        ownership = raw.get("ownership", raw.get("write_set", ()))
        if "write_set_json" in raw:
            ownership = raw
        conflict = False
        for other in occupied:
            other_raw = other if isinstance(other, Mapping) else vars(other)
            other_ownership = other_raw if "write_set_json" in other_raw else other_raw.get("ownership", other_raw.get("write_set", ()))
            if ownership_conflict(ownership, other_ownership):
                conflict = True
                break
        if conflict:
            continue
        selected.append(item)
        occupied.append(raw)
    return selected


def detect_cycles(nodes: Iterable[str], dependencies: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    state: dict[str, int] = {}
    cycle: list[str] = []

    def visit(node: str, stack: list[str]) -> bool:
        state[node] = 1
        for dep in dependencies.get(node, ()):
            if dep not in state:
                if visit(dep, stack + [node]):
                    return True
            elif state[dep] == 1:
                cycle.extend(stack + [node, dep])
                return True
        state[node] = 2
        return False

    for node in nodes:
        if node not in state and visit(node, []):
            break
    return tuple(cycle)


def critical_path_lengths(nodes: Iterable[str], dependencies: Mapping[str, Sequence[str]]) -> dict[str, int]:
    memo: dict[str, int] = {}

    def length(node: str, visiting: set[str] | None = None) -> int:
        if node in memo:
            return memo[node]
        visiting = visiting or set()
        if node in visiting:
            return 0
        visiting.add(node)
        value = 1 + max((length(dep, visiting) for dep in dependencies.get(node, ())), default=0)
        visiting.remove(node)
        memo[node] = value
        return value

    return {str(node): length(str(node)) for node in nodes}


__all__ = [
    "critical_path_lengths",
    "detect_cycles",
    "filter_ownership_conflicts",
    "ownership_conflict",
    "path_overlaps",
]
