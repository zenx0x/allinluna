#!/usr/bin/env python3
"""Build a routed control message without a second persistent message log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codex_app_adapter import SEND_MESSAGE_TOOL
from workflow_state import load_state, now_iso


def target_thread(state: dict, target: str) -> str | None:
    control = state["control_plane"]
    if target == "sponsor":
        return control["sponsor"].get("thread_id")
    if target == "primary":
        return control["primary_coordinator"].get("thread_id")
    return control["subcoordinators"].get(target, {}).get("thread_id")


def target_record(state: dict, target: str) -> dict:
    control = state["control_plane"]
    if target == "sponsor":
        return control["sponsor"]
    if target == "primary":
        return control["primary_coordinator"]
    return control["subcoordinators"].get(target, {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--from-role", required=True)
    parser.add_argument("--to-role", required=True)
    parser.add_argument(
        "--type",
        choices=[
            "requirement-change", "authorization-request", "direction-choice", "status",
            "resource-change", "blocker", "decision",
        ],
        required=True,
    )
    parser.add_argument("--body", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, state = load_state(args.run)
        thread_id = target_thread(state, args.to_role)
        if not thread_id:
            raise ValueError(f"target role has no assigned thread: {args.to_role}")
        target = target_record(state, args.to_role)
        prompt = f"[All in Luna {args.type}] {args.body}"
        payload = {
            "timestamp": now_iso(),
            "from": args.from_role,
            "to": args.to_role,
            "type": args.type,
            "body": args.body,
            "target_thread_id": thread_id,
        }
        output = {
            "ok": True,
            "message": payload,
            "action": {
                "kind": "send-control-message",
                "tool": SEND_MESSAGE_TOOL,
                "threadId": thread_id,
                "prompt": prompt,
            },
        }
        if target.get("host_id"):
            output["action"]["hostId"] = target["host_id"]
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
