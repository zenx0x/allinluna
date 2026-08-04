#!/usr/bin/env python3
"""Record requested and completed CounterPilot challenge passes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--trigger", required=True)
    parser.add_argument("--status", choices=["requested", "completed"], required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        run_dir, state = load_state(args.run)
        counterpilot = state["control_plane"]["counterpilot"]
        if counterpilot.get("mode", "off") == "off":
            raise ValueError("CounterPilot is disabled by the selected mode")
        field = "requested_triggers" if args.status == "requested" else "completed_triggers"
        if args.trigger not in counterpilot[field]:
            counterpilot[field].append(args.trigger)
        counterpilot.setdefault("trigger_history", []).append(
            {"trigger": args.trigger, "status": args.status, "recorded_at": now_iso()}
        )
        state["updated_at"] = now_iso()
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(run_dir, event("coordinator", f"counterpilot:{args.trigger}", None, args.status, args.reason))
        output = {"ok": True, "trigger": args.trigger, "status": args.status}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
