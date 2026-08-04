#!/usr/bin/env python3
"""Record sponsor, Coordinator, child-coordinator, or CounterPilot thread assignments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--role", choices=["sponsor", "primary-coordinator", "subcoordinator", "counterpilot", "secondary-counterpilot"], required=True)
    parser.add_argument("--coordinator-id")
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--host-id")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, state = load_state(args.run)
        control = state["control_plane"]
        if args.role == "sponsor":
            target = control["sponsor"]
        elif args.role == "primary-coordinator":
            target = control["primary_coordinator"]
        elif args.role == "counterpilot":
            target = control["counterpilot"]
        elif args.role == "secondary-counterpilot":
            target = control["secondary_counterpilot"]
        else:
            if not args.coordinator_id or args.coordinator_id not in control["subcoordinators"]:
                raise ValueError("subcoordinator requires a valid --coordinator-id")
            target = control["subcoordinators"][args.coordinator_id]
        all_threads = {
            item.get("thread_id")
            for item in [control["sponsor"], control["primary_coordinator"], control["counterpilot"], control["secondary_counterpilot"], *control["subcoordinators"].values()]
            if item.get("thread_id")
        }
        if args.thread_id in all_threads and target.get("thread_id") != args.thread_id:
            raise ValueError("control-plane roles must use distinct threads")
        previous = target.get("thread_id")
        target["thread_id"] = args.thread_id
        target["host_id"] = args.host_id
        if "status" in target:
            target["status"] = "running"
        state["updated_at"] = now_iso()
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(run_dir, event("sponsor", f"control-plane:{args.role}", previous, args.thread_id, args.reason))
        output = {"ok": True, "role": args.role, "thread_id": args.thread_id}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
