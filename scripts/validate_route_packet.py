#!/usr/bin/env python3
"""Fail-closed runtime contract for a Research Routes evidence packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


FORBIDDEN_AUTHORIZATIONS = ("experiment", "implementation", "canonical_promotion")
POLARITIES = {"positive", "negative", "mixed", "unknown"}


def validate(packet: dict) -> list[str]:
    errors: list[str] = []
    for field in ("routes", "claims", "evidence", "next_probe", "boundary_conditions"):
        if field not in packet:
            errors.append(f"missing {field}")
    if errors:
        return errors
    if not isinstance(packet["routes"], list) or not packet["routes"]:
        errors.append("routes must be a non-empty list")
    claims = packet["claims"]
    evidence = packet["evidence"]
    if not isinstance(claims, list) or not claims:
        errors.append("claims must be a non-empty list")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must be a non-empty list")
    evidence_ids = {item.get("id") for item in evidence if isinstance(item, dict)}
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, dict) or not claim.get("id"):
            errors.append("each claim needs an id")
            continue
        refs = claim.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            errors.append(f"claim {claim['id']} needs evidence_refs")
        elif any(ref not in evidence_ids for ref in refs):
            errors.append(f"claim {claim['id']} references missing evidence")
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            errors.append("each evidence item needs an id")
        elif item.get("polarity") not in POLARITIES:
            errors.append(f"evidence {item.get('id')} has invalid polarity")
    probe = packet["next_probe"]
    if not isinstance(probe, dict) or probe.get("reversible") is not True:
        errors.append("next_probe must be explicitly reversible")
    boundaries = packet["boundary_conditions"]
    if not isinstance(boundaries, dict):
        errors.append("boundary_conditions must be an object")
    else:
        for authorization in FORBIDDEN_AUTHORIZATIONS:
            if boundaries.get(authorization) is True:
                errors.append(f"route map cannot authorize {authorization}")
        if boundaries.get("human_decision") is True:
            errors.append("route map cannot stand in for HumanDecision")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    args = parser.parse_args()
    try:
        errors = validate(json.loads(args.packet.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
