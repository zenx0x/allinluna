#!/usr/bin/env python3
"""Persist the single projectId needed before project-scoped owner dispatch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dispatcher_lease import state_lock
from workflow_state import atomic_write_json, load_state, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-root")
    parser.add_argument("--host-id")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, _ = load_state(args.run)
        with state_lock(run_dir):
            _, state = load_state(run_dir)
            current = state.setdefault("capabilities", {}).get("project_id")
            if current not in {None, args.project_id}:
                raise ValueError(f"project_id is already resolved to {current!r}")
            state["capabilities"]["project_id"] = args.project_id
            state["capabilities"]["project_resolution"] = {
                "status": "resolved",
                "project_id": args.project_id,
                "project_root": args.project_root,
                "host_id": args.host_id,
                "reason": args.reason,
                "resolved_at": now_iso(),
            }
            state["updated_at"] = now_iso()
            atomic_write_json(run_dir / "run-state.json", state)
        output = {"ok": True, "project_id": args.project_id}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
