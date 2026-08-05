"""One canonical path policy for ownership, leases, and workspace evidence."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Iterable, Sequence


def normalize_path_pattern(value: str) -> str:
    text = str(value).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    if not text or text == "." or any(part == ".." for part in text.split("/")):
        raise ValueError(f"invalid relative path pattern: {value!r}")
    return text


def matches(path: str, pattern: str) -> bool:
    child = normalize_path_pattern(path)
    parent = normalize_path_pattern(pattern)
    if parent.endswith("/**"):
        prefix = parent[:-3].rstrip("/")
        return child == prefix or child.startswith(prefix + "/")
    return child == parent or fnmatchcase(child, parent)


def _literal_prefix(pattern: str) -> str:
    parts = normalize_path_pattern(pattern).split("/")
    literal: list[str] = []
    for part in parts:
        if any(token in part for token in "*?["):
            break
        literal.append(part)
    return "/".join(literal)


def contains(parent: str, child: str) -> bool:
    """Return whether every path selected by child stays within parent."""

    parent_text = normalize_path_pattern(parent)
    child_text = normalize_path_pattern(child)
    if parent_text == child_text:
        return True
    if not any(token in parent_text for token in "*?[") and child_text.startswith(parent_text + "/"):
        return True
    if not any(token in child_text for token in "*?[") and matches(child_text, parent_text):
        return True
    parent_prefix = _literal_prefix(parent_text)
    child_prefix = _literal_prefix(child_text)
    if not parent_prefix:
        return True
    if child_prefix != parent_prefix and not child_prefix.startswith(parent_prefix + "/"):
        return False
    if parent_text.endswith("/**"):
        return True
    return fnmatchcase(child_text, parent_text)


def overlaps(left: str, right: str) -> bool:
    left_text = normalize_path_pattern(left)
    right_text = normalize_path_pattern(right)
    if matches(left_text, right_text) or matches(right_text, left_text):
        return True
    left_prefix = _literal_prefix(left_text)
    right_prefix = _literal_prefix(right_text)
    return bool(left_prefix and right_prefix) and (
        left_prefix == right_prefix
        or left_prefix.startswith(right_prefix + "/")
        or right_prefix.startswith(left_prefix + "/")
    )


def contains_all(parents: Sequence[str], children: Sequence[str]) -> bool:
    if not children:
        return True
    if not parents:
        return False
    return all(any(contains(parent, child) for parent in parents) for child in children)


def intersects(left: Iterable[str], right: Iterable[str]) -> bool:
    return any(overlaps(a, b) for a in left for b in right)


__all__ = ["contains", "contains_all", "intersects", "matches", "normalize_path_pattern", "overlaps"]
