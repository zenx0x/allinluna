#!/usr/bin/env python3
"""Create, route, and resolve defects against the original All in Luna owner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--action", choices=["create", "resolve", "reopen"], required=True)
    parser.add_argument("--defect-id", required=True)
    parser.add_argument("--owner-task")
    parser.add_argument("--reporter-task")
    parser.add_argument("--summary")
    parser.add_argument("--reproduction")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    output: dict
    try:
        run_dir, state = load_state(args.run)
        defects = state.setdefault("defects", {})
        timestamp = now_iso()
        if args.action == "create":
            if args.defect_id in defects:
                raise ValueError(f"defect already exists: {args.defect_id}")
            if not args.owner_task or args.owner_task not in state["tasks"]:
                raise ValueError("create requires a valid --owner-task")
            if not args.summary or not args.reproduction:
                raise ValueError("create requires --summary and --reproduction")
            owner = state["tasks"][args.owner_task]
            previous_owner = owner["status"]
            prior_owner_evidence = json.loads(json.dumps(owner["evidence"]))
            if previous_owner in {"completed", "blocked", "failed"}:
                owner["status"] = "ready"
                owner["updated_at"] = timestamp
            blocker = f"{args.defect_id}: {args.summary}"
            if blocker not in owner["evidence"]["blockers"]:
                owner["evidence"]["blockers"].append(blocker)
            defects[args.defect_id] = {
                "id": args.defect_id,
                "status": "open",
                "owner_task": args.owner_task,
                "reporter_task": args.reporter_task,
                "summary": args.summary,
                "reproduction": args.reproduction,
                "evidence": args.evidence,
                "repair_commits": [],
                "prior_owner_evidence": prior_owner_evidence,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            if previous_owner == "completed":
                owner["evidence"] = {
                    "final_commit": None,
                    "changed_files": [],
                    "checks": [],
                    "blockers": [blocker],
                    "skip_approved": False,
                }
            if args.reporter_task and args.reporter_task in state["tasks"]:
                reporter = state["tasks"][args.reporter_task]
                if reporter["status"] == "running":
                    reporter["status"] = "blocked"
                    reporter["updated_at"] = timestamp
            previous, current = None, "open"
        else:
            if args.defect_id not in defects:
                raise ValueError(f"unknown defect: {args.defect_id}")
            defect = defects[args.defect_id]
            previous = defect["status"]
            if args.action == "resolve":
                owner = state["tasks"][defect["owner_task"]]
                if owner["status"] != "completed":
                    raise ValueError("defect can be resolved only after the owner task is completed")
                defect["status"] = "resolved"
                commit = owner["evidence"].get("final_commit")
                if commit and commit not in defect["repair_commits"]:
                    defect["repair_commits"].append(commit)
            else:
                defect["status"] = "open"
                owner = state["tasks"][defect["owner_task"]]
                if owner["status"] in {"completed", "blocked", "failed"}:
                    owner["status"] = "ready"
                    owner["updated_at"] = timestamp
            defect["updated_at"] = timestamp
            current = defect["status"]
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(
            run_dir,
            event(
                actor="coordinator",
                entity=f"defect:{args.defect_id}",
                previous=previous,
                current=current,
                reason=args.reason,
                evidence={"owner_task": defects[args.defect_id]["owner_task"]},
            ),
        )
        output = {
            "ok": True,
            "defect_id": args.defect_id,
            "status": defects[args.defect_id]["status"],
            "owner_task": defects[args.defect_id]["owner_task"],
            "owner_status": state["tasks"][defects[args.defect_id]["owner_task"]]["status"],
        }
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
