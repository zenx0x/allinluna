#!/usr/bin/env python3
"""Create and resolve evidence-backed CounterPilot challenges."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--action", choices=["create", "resolve"], required=True)
    parser.add_argument("--challenge-id", required=True)
    parser.add_argument("--target", choices=["sponsor", "coordinator"])
    parser.add_argument("--severity", choices=["low", "medium", "high", "critical"])
    parser.add_argument("--category")
    parser.add_argument("--assumption")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--missing-information")
    parser.add_argument("--question")
    parser.add_argument("--risk-if-ignored")
    parser.add_argument("--suggested-probe")
    parser.add_argument("--resolution", choices=["accepted", "rejected", "deferred"])
    parser.add_argument("--resolution-reason")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, state = load_state(args.run)
        challenges = state["challenges"]
        timestamp = now_iso()
        if args.action == "create":
            required = [args.target, args.severity, args.category, args.assumption, args.question, args.risk_if_ignored, args.suggested_probe]
            if not all(required) or not args.evidence:
                raise ValueError("create requires target, severity, category, assumption, evidence, question, risk, and probe")
            if args.challenge_id in challenges:
                raise ValueError("challenge already exists")
            challenges[args.challenge_id] = {
                "challenge_id": args.challenge_id,
                "target": args.target,
                "severity": args.severity,
                "category": args.category,
                "challenged_assumption": args.assumption,
                "evidence": args.evidence,
                "missing_information": args.missing_information,
                "question": args.question,
                "risk_if_ignored": args.risk_if_ignored,
                "suggested_probe": args.suggested_probe,
                "status": "open",
                "resolution_reason": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            previous, current = None, "open"
        else:
            if args.challenge_id not in challenges or not args.resolution or not args.resolution_reason:
                raise ValueError("resolve requires an existing challenge, resolution, and reason")
            challenge = challenges[args.challenge_id]
            previous = challenge["status"]
            challenge["status"] = args.resolution
            challenge["resolution_reason"] = args.resolution_reason
            challenge["updated_at"] = timestamp
            current = args.resolution
        state["updated_at"] = timestamp
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(run_dir, event("counterpilot", f"challenge:{args.challenge_id}", previous, current, args.resolution_reason or args.question or "challenge"))
        output = {"ok": True, "challenge": challenges[args.challenge_id]}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
