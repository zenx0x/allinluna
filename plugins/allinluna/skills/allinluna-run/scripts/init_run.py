#!/usr/bin/env python3
"""Initialize a persistent All in Luna run from a validated development plan."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR.parent.parent
PLAN_SCRIPTS = SKILLS_DIR / "allinluna-plan" / "scripts"
sys.path.insert(0, str(PLAN_SCRIPTS))
sys.path.insert(0, str(SCRIPT_DIR))

from resolve_profile import DEFAULT_PROFILES, catalog_surface, read_json, resolve  # noqa: E402
from validate_plan import validate as validate_plan  # noqa: E402
from workflow_state import (  # noqa: E402
    append_event,
    atomic_write_json,
    build_initial_state,
    event,
    role_for_task,
)


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.").lower()
    return cleaned[:80] or "run"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path.home() / ".codex" / "allinluna" / "runs",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--goal-authorized", action="store_true")
    parser.add_argument(
        "--allow-delegation-fallback",
        action="store_true",
        help=(
            "Compatibility override for custom profiles that still require explicit fallback "
            "approval. Built-in profiles automatically fall back when the host truly lacks "
            "top-level task capability."
        ),
    )
    parser.add_argument(
        "--runtime-tier",
        choices=["auto", "top-level-task", "subagent", "sequential"],
        default="auto",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = read_json(args.plan)
        validation = validate_plan(plan)
        if not validation["valid"]:
            raise ValueError("invalid plan: " + "; ".join(validation["errors"]))
        plan_goal = bool(plan["authorizations"]["goal_creation"])
        if args.goal_authorized and not (plan_goal and plan["mode"] == "goal-ready"):
            raise ValueError("--goal-authorized requires a goal-ready plan with explicit authorization")
        if plan["mode"] == "goal-ready" and plan_goal and not args.goal_authorized:
            raise ValueError("goal-ready plan requires --goal-authorized at run initialization")
        if args.runtime_tier == "top-level-task" and not plan["authorizations"]["top_level_tasks"]:
            raise ValueError("top-level-task tier requires explicit top_level_tasks authorization")

        profile_name = args.profile or plan["resource_policy"]["profile"]
        profiles = read_json(args.profiles)
        catalog = read_json(args.catalog) if args.catalog else None
        runtime_tier = args.runtime_tier
        delegation_fallback_reason = None
        if runtime_tier == "auto" and catalog is not None:
            profile = profiles.get("profiles", {}).get(profile_name, {})
            delegation_policy = profile.get("delegation", {})
            preferred = delegation_policy.get("root_preferred", "top-level-task")
            if preferred == "top-level-task":
                if not plan["authorizations"]["top_level_tasks"]:
                    raise ValueError(
                        "top-level-task is the default delegation but is not authorized; "
                        "obtain explicit user authorization instead of falling back to subagents"
                    )
                if catalog_surface(catalog, "top-level-task")["available"]:
                    runtime_tier = "top-level-task"
                elif (
                    delegation_policy.get("root_fallback_requires_user_approval", False)
                    and not args.allow_delegation_fallback
                ):
                    raise ValueError(
                        "top-level-task is unavailable; delegation fallback requires explicit "
                        "user approval via --allow-delegation-fallback"
                    )
                else:
                    runtime_tier = next(
                        (
                            tier
                            for tier in delegation_policy.get("root_fallback_order", [])
                            if catalog_surface(catalog, tier)["available"]
                        ),
                        "sequential",
                    )
                    delegation_fallback_reason = "top-level-tool-unavailable"
        resolution = resolve(
            profiles,
            profile_name,
            plan_policy=plan["resource_policy"],
            catalog=catalog,
            delegation=runtime_tier,
        )
        if not resolution["valid"]:
            raise ValueError("invalid resource policy: " + "; ".join(resolution["errors"]))
        if profile_name == "custom":
            required_roles = {role_for_task(task) for task in plan["tasks"]}
            missing_roles = sorted(required_roles - resolution["policy"].get("roles", {}).keys())
            if missing_roles:
                raise ValueError(
                    "custom profile is missing task role policies: " + ", ".join(missing_roles)
                )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = slug(args.run_id or f"{plan['plan_id']}-{timestamp}")
        run_dir = args.state_root.expanduser().resolve() / run_id
        if run_dir.exists():
            raise FileExistsError(f"run directory already exists: {run_dir}")
        run_dir.mkdir(parents=True)

        state = build_initial_state(
            plan=plan,
            run_id=run_id,
            run_dir=run_dir,
            profile=profile_name,
            policy=resolution["policy"],
            goal_authorized=args.goal_authorized,
            runtime_tier=runtime_tier,
        )
        state["capabilities"]["requested_delegation"] = args.runtime_tier
        state["capabilities"]["actual_delegation"] = (
            runtime_tier if runtime_tier != "auto" else "unavailable"
        )
        if catalog is not None:
            state["capabilities"]["host_concurrency"] = resolution["concurrency"]["host_cap"]
        state["capabilities"]["fallback_reason"] = delegation_fallback_reason
        atomic_write_json(run_dir / "plan.json", plan)
        atomic_write_json(run_dir / "run-state.json", state)
        append_event(
            run_dir,
            event(
                actor="allinluna",
                entity=f"run:{run_id}",
                previous=None,
                current="planned",
                reason="run initialized from validated plan",
                evidence={
                    "plan_hash": state["plan_hash"],
                    "profile": profile_name,
                    "runtime_tier": runtime_tier,
                    "runtime_tier_requested": args.runtime_tier,
                    "delegation_fallback_reason": delegation_fallback_reason,
                    "catalog": str(args.catalog.resolve()) if args.catalog else None,
                    "resource_warnings": resolution["warnings"],
                },
            ),
        )
        output = {
            "ok": True,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "state": str(run_dir / "run-state.json"),
            "ready_tasks": [task_id for task_id, task in state["tasks"].items() if task["status"] == "ready"],
            "profile": profile_name,
            "runtime_tier": runtime_tier,
            "delegation_fallback_reason": delegation_fallback_reason,
            "warnings": resolution["warnings"],
        }
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
