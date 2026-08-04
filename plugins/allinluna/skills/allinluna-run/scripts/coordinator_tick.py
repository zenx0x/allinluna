#!/usr/bin/env python3
"""Compute the next mandatory All in Luna Coordinator actions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codex_app_adapter import (
    CREATE_THREAD_TOOL,
    await_dispatch_receipt,
    control_target,
    create_thread_action,
    default_repository_identity,
    dispatch_identity,
    dispatch_id,
    dispatch_intent,
    LIST_PROJECTS_TOOL,
    monitoring_action,
    owner_target,
    project_resolution_action,
    send_message_action,
)
from dispatcher_lease import DispatcherLeaseError, dispatcher_session, make_owner_identity
from render_control_plane_brief import render as render_control_brief
from render_task_brief import render
from workflow_state import (
    append_event,
    atomic_write_json,
    counterpilot_trigger,
    event,
    load_state,
    now_iso,
)


def effective_slots(state: dict) -> int:
    desired = int(state.get("resource_policy", {}).get("concurrency", {}).get("desired", 1))
    host = state.get("capabilities", {}).get("host_concurrency")
    return min(desired, host) if isinstance(host, int) and host > 0 else desired


def soft_budget_reached(state: dict) -> dict | None:
    budget = state.get("resource_policy", {}).get("budget", {})
    metric = budget.get("metric")
    limit = budget.get("soft_limit")
    used = state.get("usage", {}).get(metric) if metric else None
    if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and used >= limit:
        return {"metric": metric, "used": used, "limit": limit}
    return None


def _unresolved(model: object) -> bool:
    return not model or model == "unavailable" or str(model).startswith(("tier:", "family:"))


def _control_action(
    state: dict,
    child_id: str,
    child: dict,
    *,
    run_dir: Path,
    write_briefs: bool,
    record_intents: bool,
    lease: dict | None,
) -> dict:
    if child.get("dispatch_intent"):
        return await_dispatch_receipt(child_id, child["dispatch_intent"], lease=lease)
    brief_path = run_dir / "briefs" / f"{child_id}.md"
    prompt = render_control_brief(state, "subcoordinator", child_id)
    if write_briefs:
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(prompt, encoding="utf-8")
    resolved = state["control_plane"]["primary_coordinator"]["resolved"]
    model = resolved.get("model")
    if _unresolved(model):
        return {
            "kind": "resolve-runtime-resources",
            "coordinator_id": child_id,
            "instruction": "resolve the child Coordinator model before dispatch",
        }
    action = create_thread_action(
        kind="dispatch-subcoordinator",
        entity_id=dispatch_id(state["run_id"], child_id, epoch=(lease or {}).get("epoch")),
        prompt=prompt,
        target=control_target(state, child_id),
        model=model,
        thinking=resolved.get("reasoning"),
        title=f"All in Luna {child_id} [{dispatch_id(state['run_id'], child_id, epoch=(lease or {}).get('epoch'))}]",
        record_with="record_control_plane.py --role subcoordinator",
        metadata={
            "coordinator_id": child_id,
            "git_bootstrap_required": False,
        },
        task_id=child_id,
        identity=dispatch_identity(
            state,
            task_id=child_id,
            target=control_target(state, child_id),
        ),
        dispatcher_epoch=(lease or {}).get("epoch"),
        state=state,
    )
    if record_intents:
        child["dispatch_intent"] = dispatch_intent(action, emitted_at=now_iso(), lease=lease)
    return action


def counterpilot_creation_action(
    state: dict,
    run_dir: Path,
    trigger: str | None,
    write_briefs: bool,
    record_intents: bool,
    lease: dict | None,
) -> dict:
    counterpilot = state["control_plane"]["counterpilot"]
    if counterpilot.get("dispatch_intent"):
        return await_dispatch_receipt("counterpilot", counterpilot["dispatch_intent"], lease=lease)
    resolved = counterpilot.get("resolved", {})
    resolved_model = resolved.get("model")
    if _unresolved(resolved_model):
        return {
            "kind": "resolve-control-plane-resources",
            "role": "counterpilot",
            "trigger": trigger,
            "instruction": "resolve the CounterPilot model before creating the deferred task",
        }
    brief_path = run_dir / "briefs" / "counterpilot.md"
    prompt = render_control_brief(state, "counterpilot")
    if write_briefs:
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(prompt, encoding="utf-8")
    action = create_thread_action(
        kind="create-counterpilot",
        entity_id=dispatch_id(state["run_id"], "counterpilot", epoch=(lease or {}).get("epoch")),
        prompt=prompt,
        target=control_target(state, "counterpilot"),
        model=resolved_model,
        thinking=resolved.get("reasoning"),
        title=f"All in Luna counterpilot [{dispatch_id(state['run_id'], 'counterpilot', epoch=(lease or {}).get('epoch'))}]",
        record_with="record_control_plane.py --role counterpilot",
        metadata={
            "role": "counterpilot",
            "trigger": trigger,
            "git_bootstrap_required": False,
        },
        task_id="counterpilot",
        identity=dispatch_identity(
            state,
            task_id="counterpilot",
            target=control_target(state, "counterpilot"),
        ),
        dispatcher_epoch=(lease or {}).get("epoch"),
        state=state,
    )
    if trigger:
        action["record_with"] += f" --trigger {trigger}"
    if record_intents:
        counterpilot["dispatch_intent"] = dispatch_intent(action, emitted_at=now_iso(), lease=lease)
    return action


def _project_action(state: dict, *, record_intents: bool) -> dict:
    resolution = state.setdefault("capabilities", {}).get("project_resolution")
    if resolution:
        return {
            "kind": "await-project-receipt",
            "tool": LIST_PROJECTS_TOOL,
            "project_root": resolution.get("project_root"),
            "receipt_required": True,
            "instruction": "reuse the existing list_projects request; do not redispatch it",
        }
    action = project_resolution_action(state)
    if record_intents:
        state["capabilities"]["project_resolution"] = {
            "status": "emitted",
            "project_root": action.get("project_root"),
            "requested_at": now_iso(),
        }
    return action


def _owner_action(
    state: dict,
    run_dir: Path,
    task_id: str,
    *,
    write_briefs: bool,
    record_intents: bool,
    lease: dict | None,
) -> dict:
    task = state["tasks"][task_id]
    assignment = task["assignment"]
    if assignment.get("dispatch_intent"):
        action = await_dispatch_receipt(task_id, assignment["dispatch_intent"], lease=lease)
        action["task_id"] = task_id
        return action
    resolved_model = assignment.get("resolved_model")
    if _unresolved(resolved_model):
        return {
            "kind": "resolve-runtime-resources",
            "task_id": task_id,
            "instruction": (
                "resolve this task against the current runtime catalog with "
                "refresh_task_resources.py before dispatch; never pass a logical tier "
                "or family name to create_thread"
            ),
        }
    target = owner_target(state)
    if target is None:
        raise RuntimeError("projectId is required before dispatching a worktree owner")
    brief_path = run_dir / "briefs" / f"{task_id}.md"
    prompt = render(state, task_id)
    if write_briefs:
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(prompt, encoding="utf-8")
    action = create_thread_action(
        kind="dispatch-top-level-task",
        entity_id=dispatch_id(state["run_id"], task_id, epoch=(lease or {}).get("epoch")),
        prompt=prompt,
        target=target,
        model=resolved_model,
        thinking=assignment.get("resolved_reasoning") or task["requested"]["reasoning"],
        title=f"All in Luna {task_id} [{dispatch_id(state['run_id'], task_id, epoch=(lease or {}).get('epoch'))}]",
        record_with=f"record_thread_receipt.py --task {task_id} --receipt APP_RESPONSE.json",
        metadata={
            "task_id": task_id,
            "delegation": "top-level-task",
        },
        task_id=task_id,
        identity=dispatch_identity(
            state,
            task_id=task_id,
            target=target,
        ),
        dispatcher_epoch=(lease or {}).get("epoch"),
        state=state,
    )
    if record_intents:
        assignment["dispatch_intent"] = dispatch_intent(action, emitted_at=now_iso(), lease=lease)
    return action


def actions_for(
    state: dict,
    run_dir: Path,
    coordinator_id: str = "primary",
    write_briefs: bool = True,
    record_intents: bool = True,
    lease: dict | None = None,
) -> dict:
    tasks = state["tasks"]
    control = state["control_plane"]
    primary = control["primary_coordinator"]
    run_status = state["status"]
    if run_status in {"paused", "completed", "failed"}:
        instruction = {
            "paused": "resume the run explicitly before dispatching new work",
            "completed": "the run is complete; no further dispatch is allowed",
            "failed": "the run is failed; create a new run or explicitly recover it",
        }[run_status]
        return {
            "ok": True,
            "run_id": state["run_id"],
            "run_status": run_status,
            "desired_concurrency": state["resource_policy"]["concurrency"]["desired"],
            "effective_concurrency": effective_slots(state),
            "running": [],
            "ready": [],
            "dispatch_count": 0,
            "actions": [{"kind": "human-control-required", "instruction": instruction}],
        }
    if primary["status"] != "running":
        return {
            "ok": True,
            "run_id": state["run_id"],
            "run_status": state["status"],
            "desired_concurrency": state["resource_policy"]["concurrency"]["desired"],
            "effective_concurrency": effective_slots(state),
            "running": [],
            "ready": [],
            "dispatch_count": 0,
            "actions": [{"kind": "bootstrap-control-plane", "tool": "bootstrap_control_plane.py"}],
        }

    if coordinator_id == "primary":
        managed_ids = {
            task_id for task_id, task in tasks.items()
            if task["assignment"].get("coordinator_id") == "primary"
        }
    else:
        if coordinator_id not in control["subcoordinators"]:
            raise ValueError(f"unknown coordinator: {coordinator_id}")
        shard = control["subcoordinators"][coordinator_id]
        if shard["status"] != "running":
            raise ValueError(f"coordinator is not assigned: {coordinator_id}")
        managed_ids = set(shard["task_ids"])

    running = [task_id for task_id, task in tasks.items() if task_id in managed_ids and task["status"] == "running"]
    ready = [task_id for task_id, task in tasks.items() if task_id in managed_ids and task["status"] == "ready"]
    total_running = sum(task["status"] == "running" for task in tasks.values())
    slots = max(0, effective_slots(state) - total_running)
    if coordinator_id != "primary":
        slots = min(slots, int(control["subcoordinators"][coordinator_id]["slot_limit"]) - len(running))
        slots = max(0, slots)
    dispatch = ready[:slots]
    actions: list[dict] = []

    counterpilot = control["counterpilot"]
    if coordinator_id == "primary":
        trigger = counterpilot_trigger(state)
        if trigger and counterpilot["status"] in {"deferred", "unassigned"}:
            actions.append(
                counterpilot_creation_action(
                    state,
                    run_dir,
                    trigger if counterpilot["status"] == "deferred" else None,
                    write_briefs,
                    record_intents,
                    lease,
                )
            )
        elif trigger and counterpilot["status"] == "running":
            message = send_message_action(
                state,
                thread_id=counterpilot["thread_id"],
                host_id=counterpilot.get("host_id"),
                prompt=f"Run one consolidated CounterPilot pass for trigger: {trigger}",
                record_with="record_counterpilot_trigger.py --status requested",
            )
            message.update({"kind": "request-counterpilot-pass", "trigger": trigger})
            actions.append(message)

    if coordinator_id == "primary":
        for child_id, child in control["subcoordinators"].items():
            if child["status"] == "unassigned":
                actions.append(
                    _control_action(
                        state,
                        child_id,
                        child,
                        run_dir=run_dir,
                        write_briefs=write_briefs,
                        record_intents=record_intents,
                        lease=lease,
                    )
                )
        active_children = [
            {
                "role": "subcoordinator",
                "thread_id": child["thread_id"],
                "host_id": child.get("host_id"),
                "after_cursor": child.get("cursor"),
            }
            for child in control["subcoordinators"].values()
            if child["status"] == "running" and child.get("thread_id")
        ]
        if active_children:
            child_monitor = monitoring_action(state, active_children)
            child_monitor["kind"] = f"{child_monitor['kind']}-subcoordinators"
            actions.append(child_monitor)

    budget_signal = soft_budget_reached(state)
    if budget_signal:
        actions.append(
            {
                "kind": "resource-reassessment",
                "budget": budget_signal,
                "instruction": (
                    "re-resolve not-yet-dispatched tasks using the active profile and live catalog; "
                    "preserve scope, ownership, validation, and hard model locks"
                ),
            }
        )

    if dispatch and owner_target(state) is None:
        actions.append(_project_action(state, record_intents=record_intents))
    elif dispatch:
        for task_id in dispatch:
            actions.append(
                _owner_action(
                    state,
                    run_dir,
                    task_id,
                    write_briefs=write_briefs,
                    record_intents=record_intents,
                    lease=lease,
                )
            )

    wait_targets = [
        {
            "task_id": task_id,
            "thread_id": tasks[task_id]["assignment"].get("thread_id"),
            "host_id": tasks[task_id]["assignment"].get("host_id"),
            "after_cursor": tasks[task_id]["assignment"].get("cursor"),
        }
        for task_id in running
        if tasks[task_id]["assignment"].get("thread_id")
    ]
    if wait_targets:
        actions.append(monitoring_action(state, wait_targets))
    incomplete = [
        task_id for task_id, task in tasks.items()
        if task["status"] not in {"completed", "skipped", "cancelled"}
    ]
    if not incomplete:
        actions.append({"kind": "completion-check", "instruction": "validate the run and completion standard, then mark completed"})
    elif not actions:
        actions.append(
            {
                "kind": "attention-required",
                "blocked_tasks": [task_id for task_id, task in tasks.items() if task["status"] in {"blocked", "failed"}],
                "instruction": "continue unrelated ready lanes; pause only if every remaining lane is blocked",
            }
        )
    return {
        "ok": True,
        "run_id": state["run_id"],
        "coordinator_id": coordinator_id,
        "run_status": state["status"],
        "desired_concurrency": state["resource_policy"]["concurrency"]["desired"],
        "effective_concurrency": effective_slots(state),
        "running": running,
        "ready": ready,
        "dispatch_count": sum(action["kind"] == "dispatch-top-level-task" for action in actions),
        "actions": actions,
    }


def _coordinator_owner_identity(state: dict, coordinator_id: str) -> dict:
    control = state["control_plane"]
    # Child Coordinators are delegated scopes of the one primary Dispatcher.  They
    # must not acquire a second global lease; their tick is serialized under the
    # primary Coordinator's logical identity.
    item = control["primary_coordinator"]
    thread_id = item.get("thread_id")
    if not thread_id:
        raise DispatcherLeaseError(
            f"{coordinator_id} cannot tick without a real primary Coordinator thread receipt"
        )
    return make_owner_identity(
        role="primary-coordinator",
        run_id=state.get("run_id"),
        coordinator_id="primary",
        thread_id=thread_id,
        host_id=item.get("host_id"),
        repository_identity=default_repository_identity(state),
        worktree_identity=control_target(state, "primary-coordinator"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--coordinator-id", default="primary")
    parser.add_argument("--no-write-briefs", action="store_true")
    parser.add_argument("--no-record", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        run_dir, initial_state = load_state(args.run)
        primary_running = initial_state["control_plane"]["primary_coordinator"].get("status") == "running"
        if not primary_running:
            output = actions_for(
                initial_state,
                run_dir,
                args.coordinator_id,
                not args.no_write_briefs,
                not args.no_record,
                None,
            )
            print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
            return 0

        owner_identity = _coordinator_owner_identity(initial_state, args.coordinator_id)
        with dispatcher_session(
            run_dir,
            owner_identity,
            purpose=f"Coordinator tick: {args.coordinator_id}",
        ) as session:
            _, state = load_state(run_dir)
            output = actions_for(
                state,
                run_dir,
                args.coordinator_id,
                not args.no_write_briefs,
                not args.no_record,
                session.lease,
            )
            output["dispatcher_lease"] = session.evidence()
            if not args.no_record:
                previous = state.get("coordination", {}).get("last_tick_at")
                state["coordination"]["last_tick_at"] = now_iso()
                state["updated_at"] = state["coordination"]["last_tick_at"]
                atomic_write_json(run_dir / "run-state.json", state)
                append_event(
                    run_dir,
                    event(
                        actor="coordinator",
                        entity=f"run:{state['run_id']}",
                        previous=previous,
                        current=state["coordination"]["last_tick_at"],
                        reason="coordinator control tick",
                        evidence={
                            "actions": [item["kind"] for item in output["actions"]],
                            "dispatch_ids": [
                                item.get("dispatch_id")
                                for item in output["actions"]
                                if item.get("dispatch_id")
                            ],
                            "dispatcher": session.evidence(),
                        },
                    ),
                )
            for action in output["actions"]:
                if action.get("duplicate_resolution"):
                    append_event(
                        run_dir,
                        event(
                            actor="coordinator",
                            entity=f"dispatch:{action.get('dispatch_id') or action.get('entity_id')}",
                            previous="dispatch-intent",
                            current=action["duplicate_resolution"]["decision"],
                            reason=action["duplicate_resolution"]["reason"],
                            evidence={
                                "duplicate": action["duplicate_resolution"],
                                "dispatcher": session.evidence(),
                            },
                        ),
                    )
        print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
        return 0
    except (OSError, KeyError, ValueError, DispatcherLeaseError) as exc:
        output = {"ok": False, "errors": [str(exc)]}
        print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
