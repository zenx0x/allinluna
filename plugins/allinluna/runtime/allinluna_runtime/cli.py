"""Small vNext CLI surface backed only by Store and runtime engines."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .engine.coordinator import CoordinatorEngine
from .store import Store


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def _load_json(value: str) -> Any:
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="allinluna", description="All in Luna vNext runtime")
    parser.add_argument("--db", default="runtime.db", help="runtime.db path")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("--goal", required=True)
    start.add_argument("--done-when", action="append", default=[])
    start.add_argument("--task-graph")
    start.add_argument("--intent-id", default=None)

    for name in ("status", "next-actions", "pause", "resume", "reconcile"):
        command = sub.add_parser(name)
        command.add_argument("run_id")
    ingest = sub.add_parser("ingest-receipt")
    ingest.add_argument("run_id")
    ingest.add_argument("receipt")

    retry = sub.add_parser("retry")
    retry.add_argument("run_id")
    retry.add_argument("--task", required=True)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("run_id")
    cancel.add_argument("--task")

    policy = sub.add_parser("set-policy")
    policy.add_argument("run_id")
    policy.add_argument("policy")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    store = Store(args.db)
    engine = CoordinatorEngine(store)
    try:
        if args.command == "start":
            graph = _load_json(args.task_graph) if args.task_graph else None
            result = engine.start({"intent_id": args.intent_id or args.goal[:48], "goal": args.goal, "done_when": args.done_when or ["requested goal satisfied"], "resource_envelope": {"model": "gpt-5.6-luna", "reasoning": "high", "external_action_policy": "deny"}}, graph)
            run_id = str(result["run_ref"]).removeprefix("run://")
            tick = engine.tick(run_id)
            result = {"run_ref": result["run_ref"], "status": tick.status, "actions": list(tick.actions), "receipts": list(tick.receipts)}
        elif args.command == "status":
            result = engine.status(args.run_id)
        elif args.command == "next-actions":
            result = engine.tick(args.run_id, dispatch=False).actions
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
        elif args.command == "reconcile":
            result = engine.reconcile(args.run_id)
        elif args.command == "ingest-receipt":
            result = engine.ingest_receipt(_load_json(args.receipt))
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
