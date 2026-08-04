#!/usr/bin/env python3
"""Validate a serialized Research Routes context without promoting any state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.model import ContextKind, EvidencePolarity, RelationKind


def validate(payload: dict) -> list[str]:
    errors: list[str] = []
    if payload.get("kind") not in {item.value for item in ContextKind}:
        errors.append("kind must be software, research-exploration, or hybrid")
    if not isinstance(payload.get("starting_point"), str) or not payload["starting_point"].strip():
        errors.append("starting_point must be non-empty")
    nodes = payload.get("nodes", {})
    for node_id, node in nodes.items():
        if node.get("relation") not in {item.value for item in RelationKind}:
            errors.append(f"{node_id}: invalid relation")
        polarity = node.get("polarity")
        if polarity is not None and polarity not in {item.value for item in EvidencePolarity}:
            errors.append(f"{node_id}: invalid evidence polarity")
        if node.get("relation") == RelationKind.CANDIDATE_INFERRED.value and node.get("status") in {"fact", "canonical"}:
            errors.append(f"{node_id}: candidate inference cannot be fact or canonical")
    continuation = payload.get("current_continuation_id")
    if continuation and not nodes.get(continuation, {}).get("human_decision_id"):
        errors.append("current continuation requires a HumanDecision")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.context.read_text(encoding="utf-8"))
    errors = validate(payload)
    print(json.dumps({"valid": not errors, "errors": errors}, indent=2 if args.pretty else None))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
