"""Small vNext CLI surface backed only by Store and runtime engines."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from .engine.coordinator import CoordinatorEngine
from .packs.public_skill import SinglePublicSkillAPI
from .resource import ResourceBroker
from .store import Store


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def _load_json(value: str) -> Any:
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _intent_id(explicit: str | None, goal: str) -> str:
    if explicit:
        return explicit
    slug = re.sub(r"[^A-Za-z0-9._:-]+", "-", goal).strip("-")[:48]
    return slug if slug and slug[0].isalpha() else f"intent-{slug or 'run'}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="allinluna", description="All in Luna vNext runtime")
    parser.add_argument("--db", default="runtime.db", help="runtime.db path")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--goal", required=True)
    start.add_argument("--done-when", action="append", default=[])
    start.add_argument("--task-graph")
    start.add_argument("--intent-id", default=None)
    start.add_argument("--model", default=None)
    start.add_argument("--reasoning", default=None)
    start.add_argument("--pack", default="delivery")

    for name in ("compile", "plan"):
        command = sub.add_parser(name)
        command.add_argument("--goal", required=True)
        command.add_argument("--done-when", action="append", default=[])
        command.add_argument("--pack", default="delivery")
        command.add_argument("--model", default=None)
        command.add_argument("--reasoning", default=None)

    for name in ("status", "next-actions", "pause", "resume", "reconcile"):
        command = sub.add_parser(name)
        command.add_argument("run_id")
    ingest = sub.add_parser("ingest-receipt")
    ingest.add_argument("run_id")
    ingest.add_argument("receipt")

    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("run_id")

    inspect = sub.add_parser("inspect")
    inspect_sub = inspect.add_subparsers(dest="inspect_kind", required=True)
    for kind in ("run", "task", "work", "context", "outbox", "receipt"):
        command = inspect_sub.add_parser(kind)
        command.add_argument("identity")
    artifacts = inspect_sub.add_parser("artifacts")
    artifacts.add_argument("identity", nargs="?")

    retry = sub.add_parser("retry")
    retry.add_argument("run_id")
    retry.add_argument("--task", required=True)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("run_id")
    cancel.add_argument("--task")

    policy = sub.add_parser("set-policy")
    policy.add_argument("run_id")
    policy.add_argument("policy")
    decide = sub.add_parser("decide")
    decide.add_argument("run_id")
    decide.add_argument("permission_id")
    decision = decide.add_mutually_exclusive_group(required=True)
    decision.add_argument("--allow", action="store_true")
    decision.add_argument("--deny", action="store_true")
    decide.add_argument("--rationale")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command in {"compile", "plan"}:
        resource_policy = {"model": args.model, "reasoning": args.reasoning, "external_action_policy": "deny"}
        compilation = SinglePublicSkillAPI().compile(
            {"goal": args.goal, "done_when": args.done_when or ["requested goal satisfied"], "pack": args.pack, "resource_envelope": resource_policy}
        )
        if args.command == "compile":
            result = compilation.to_dict()
        else:
            graph = compilation.task_graph
            result = {
                "kind": "plan", "run_id": graph.run_id, "pack": str(compilation.intent.pack.id),
                "tasks": [task.to_dict() for task in graph.ready_tasks()],
                "work_graphs": {key: value.to_dict() for key, value in graph.work_graphs.items()},
                "writes": False,
            }
        print(_json(result))
        return 0
    store = Store(args.db)
    if args.command == "start":
        resource_policy = {"model": args.model, "reasoning": args.reasoning, "external_action_policy": "deny"}
    else:
        run = store.get_run(getattr(args, "run_id", ""))
        resource_policy = dict((run or {}).get("policy") or {})
    engine = CoordinatorEngine(store, resource_broker=ResourceBroker(resource_policy))
    try:
        if args.command == "start":
            graph = _load_json(args.task_graph) if args.task_graph else None
            if graph is None:
                compilation = SinglePublicSkillAPI().compile({"intent_id": _intent_id(args.intent_id, args.goal), "goal": args.goal, "done_when": args.done_when or ["requested goal satisfied"], "resource_envelope": resource_policy, "pack": args.pack})
                graph = compilation.task_graph.to_dict()
                intent = compilation.intent.to_dict()
            else:
                intent = {"intent_id": _intent_id(args.intent_id, args.goal), "goal": args.goal, "done_when": args.done_when or ["requested goal satisfied"], "resource_envelope": resource_policy, "pack": {"id": args.pack, "version": "1.0.0"}}
            result = engine.start(intent, graph)
            run_id = str(result["run_ref"]).removeprefix("run://")
            tick = engine.tick(run_id)
            result = {"run_ref": result["run_ref"], "status": tick.status, "actions": list(tick.actions), "receipts": list(tick.receipts)}
        elif args.command == "status":
            result = engine.status(args.run_id)
        elif args.command == "next-actions":
            result = engine.tick(args.run_id, dispatch=False).actions
        elif args.command == "dispatch":
            tick = engine.tick(args.run_id, dispatch=True)
            result = {"run_id": args.run_id, "actions": list(tick.actions), "receipts": list(tick.receipts), "status": tick.status}
        elif args.command == "pause":
            result = engine.pause(args.run_id)
        elif args.command == "resume":
            result = engine.resume(args.run_id)
        elif args.command == "retry":
            result = engine.retry(args.run_id, args.task)
        elif args.command == "cancel":
            result = engine.cancel(args.run_id, args.task)
        elif args.command == "set-policy":
            result = engine.set_policy(args.run_id, _load_json(args.policy))
        elif args.command == "decide":
            result = store.decide_permission(args.permission_id, allowed=bool(args.allow), rationale=args.rationale)
        elif args.command == "reconcile":
            result = engine.reconcile(args.run_id)
        elif args.command == "ingest-receipt":
            result = engine.ingest_receipt(_load_json(args.receipt))
        elif args.command == "inspect":
            identity = getattr(args, "identity", None)
            if args.inspect_kind == "run":
                result = {"run": store.get_run(identity), "status": engine.status(identity), "metrics": store.runtime_metrics(identity)}
            elif args.inspect_kind == "task":
                result = store.get_task(identity)
            elif args.inspect_kind == "work":
                result = store.get_work_unit(identity)
            elif args.inspect_kind == "context":
                result = store.inspect_snapshot(identity)
            elif args.inspect_kind == "outbox":
                result = store.inspect_outbox(identity)
            elif args.inspect_kind == "receipt":
                result = store.get_host_receipt(identity)
            else:
                result = store.inspect_artifacts(identity)
        else:  # pragma: no cover - argparse enforces command set.
            parser.error(f"unknown command {args.command}")
            return 2
        print(_json(result))
        return 0
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = ["build_parser", "main"]
