#!/usr/bin/env python3
"""Record the projectId selected from a real Codex App list_projects response."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codex_app_adapter import project_root
from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--root")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, state = load_state(args.run)
        root = args.root or project_root(state)
        state["capabilities"]["project_id"] = args.project_id
        state["capabilities"]["project_receipt"] = {
            "kind": "project-receipt",
            "project_id": args.project_id,
            "project_root": root,
            "received_at": now_iso(),
        }
        state["capabilities"].pop("project_resolution", None)
        state["updated_at"] = now_iso()
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(
            run_dir,
            event("host-adapter", f"run:{state['run_id']}", None, "project-receipt", args.reason, state["capabilities"]["project_receipt"]),
        )
        output = {"ok": True, "project_id": args.project_id, "project_root": root}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
