#!/usr/bin/env python3
"""Re-resolve undispatched All in Luna tasks against a live runtime catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from resolve_profile import DEFAULT_PROFILES, parse_role_override, read_json, resolve
from workflow_state import append_event, atomic_write_json, event, load_state, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--profile")
    parser.add_argument("--delegation", choices=["top-level-task", "subagent", "sequential"])
    parser.add_argument("--role", action="append", default=[])
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    output: dict
    try:
        run_dir, state = load_state(args.run)
        profiles = read_json(args.profiles)
        catalog = read_json(args.catalog)
        profile = args.profile or state["profile"]
        delegation = args.delegation or state["capabilities"].get("actual_delegation")
        if delegation not in {"top-level-task", "subagent", "sequential"}:
            delegation = "top-level-task"
        overrides = dict(parse_role_override(item) for item in args.role)
        result = resolve(
            profiles,
            profile,
            plan_policy=state["resource_policy"],
            role_overrides=overrides,
            catalog=catalog,
            delegation=delegation,
            concurrency_override=args.concurrency,
        )
        if not result["valid"]:
            raise ValueError("resource resolution failed: " + "; ".join(result["errors"]))
        changed: list[str] = []
        for task_id, task in state["tasks"].items():
            if task["status"] not in {"pending", "ready", "blocked", "failed"}:
                continue
            role = task["requested"]["role"]
            resolved = result["resolved_roles"].get(role)
            if not resolved:
                raise ValueError(f"profile {profile} does not define role {role}")
            task["requested"]["model"] = resolved["requested_model"]
            task["requested"]["reasoning"] = resolved["requested_reasoning"]
            task["assignment"]["resolved_model"] = resolved["actual_model"]
            task["assignment"]["resolved_reasoning"] = resolved["actual_reasoning"]
            task["assignment"]["resource_resolution"] = resolved["resolution"]
            task["updated_at"] = now_iso()
            changed.append(task_id)
        state["profile"] = profile
        state["resource_policy"] = result["policy"]
        state["capabilities"]["actual_delegation"] = result["delegation"]["selected"]
        state["capabilities"]["host_concurrency"] = result["concurrency"]["host_cap"]
        state["updated_at"] = now_iso()
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(
            run_dir,
            event(
                actor="resource-control",
                entity=f"run:{state['run_id']}:resources",
                previous=None,
                current=profile,
                reason=args.reason,
                evidence={
                    "tasks": changed,
                    "delegation": result["delegation"]["selected"],
                    "warnings": result["warnings"],
                },
            ),
        )
        output = {
            "ok": True,
            "profile": profile,
            "delegation": result["delegation"]["selected"],
            "updated_tasks": changed,
            "concurrency": result["concurrency"],
            "warnings": result["warnings"],
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
