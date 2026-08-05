#!/usr/bin/env python3
"""Small neutral host conformance diagnostics.

The checker validates host-facing traces for bounded task tools. It intentionally
covers a small surface: identity, create/read/wait/cancel operations, and
idempotency.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROTOCOL = "allinluna.host_conformance"
SCHEMA_VERSION = "1.0"
REQUIRED_ACTIONS = ("create", "read", "wait", "cancel")
ACTION_FIELDS = (
    "op",
    "thread_id",
    "requested_tool",
    "resolved_tool",
    "actual_tool",
    "requested_capability",
    "resolved_capability",
    "actual_capability",
    "identity",
    "idempotency",
)
REQUIRED_IDENTITY_FIELDS = ("thread_id", "host_id", "worktree", "repo", "branch", "commit")
VALID_IDEMPOTENCY = {"no-op", "reuse", "wait"}


def _issue(class_name: str, message: str, *, path: str) -> dict[str, str]:
    return {"class": class_name, "message": message, "path": path}


def _identity(*, thread_suffix: str, host: str) -> dict[str, str]:
    return {
        "thread_id": f"host-thread-{thread_suffix}",
        "host_id": host,
        "worktree": f"host-worktree/{thread_suffix}",
        "repo": "D:/repos/allinluna",
        "branch": "main",
        "commit": "local-host-conformance-commit",
    }


def _operation(name: str, identity: dict[str, str], *, idempotency: str) -> dict[str, str]:
    return {
        "op": name,
        "thread_id": identity["thread_id"],
        "requested_tool": "codex_app__create_thread",
        "resolved_tool": "codex_app__create_thread",
        "actual_tool": "codex_app__create_thread",
        "requested_capability": "top-level-task",
        "resolved_capability": "top-level-task",
        "actual_capability": "top-level-task",
        "identity": dict(identity),
        "idempotency": idempotency,
    }


def build_fixture_trace() -> dict[str, Any]:
    identity = _identity(thread_suffix="fixture", host="fixture-host")
    return {
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "verification_mode": "fixture",
        "checked_at": "2026-08-05T00:00:00Z",
        "identity": dict(identity),
        "operations": [
            _operation("create", identity, idempotency="no-op"),
            _operation("read", identity, idempotency="reuse"),
            _operation("wait", identity, idempotency="wait"),
            _operation("cancel", identity, idempotency="no-op"),
        ],
    }


def _validate_identity(value: Any, *, path: str, failures: list[dict[str, str]]) -> dict[str, str] | None:
    if not isinstance(value, dict):
        failures.append(_issue("schema", f"{path} must be an object", path=path))
        return None
    for field in REQUIRED_IDENTITY_FIELDS:
        if not isinstance(value.get(field), str) or not value[field].strip():
            failures.append(_issue("schema", f"{path}.{field} must be a non-empty string", path=f"{path}.{field}"))
    return {field: str(value.get(field, "")) for field in REQUIRED_IDENTITY_FIELDS}


def validate(trace: Any, *, mode: str) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    if not isinstance(trace, dict):
        failures.append(_issue("schema", "trace must be a JSON object", path="$"))
        return failures

    if trace.get("protocol") != PROTOCOL:
        failures.append(_issue("schema", "invalid protocol", path="protocol"))
    if trace.get("schema_version") != SCHEMA_VERSION:
        failures.append(_issue("schema", "invalid schema_version", path="schema_version"))
    if trace.get("verification_mode") != mode:
        failures.append(_issue("schema", "verification_mode must match execution mode", path="verification_mode"))

    top_identity = _validate_identity(trace.get("identity"), path="identity", failures=failures)

    checked_at = trace.get("checked_at")
    if not isinstance(checked_at, str):
        failures.append(_issue("schema", "checked_at must be a string", path="checked_at"))
    else:
        try:
            parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        except ValueError:
            failures.append(_issue("schema", "checked_at must be iso8601", path="checked_at"))
        else:
            if parsed.tzinfo is None:
                failures.append(_issue("schema", "checked_at must include timezone", path="checked_at"))

    operations = trace.get("operations")
    if not isinstance(operations, list) or len(operations) < len(REQUIRED_ACTIONS):
        failures.append(_issue("schema", "operations must contain create/read/wait/cancel", path="operations"))
        return failures

    seen_ops: set[str] = set()
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            failures.append(_issue("schema", "operation must be an object", path=f"operations[{index}]"))
            continue
        op_name = operation.get("op")
        if not isinstance(op_name, str) or not op_name.strip():
            failures.append(_issue("schema", "operation.op must be a non-empty string", path=f"operations[{index}].op"))
            continue
        seen_ops.add(op_name)
        if op_name not in REQUIRED_ACTIONS:
            failures.append(_issue("schema", f"unsupported operation {op_name}", path=f"operations[{index}].op"))
            continue
        for field in ACTION_FIELDS:
            if not operation.get(field):
                failures.append(_issue("schema", f"operation.{field} is required", path=f"operations[{index}].{field}"))
        op_identity = _validate_identity(operation.get("identity"), path=f"operations[{index}].identity", failures=failures)
        if top_identity and op_identity:
            for field in REQUIRED_IDENTITY_FIELDS:
                if op_identity.get(field) != top_identity.get(field):
                    failures.append(
                        _issue(
                            "schema",
                            "operation identity must match trace identity",
                            path=f"operations[{index}].identity.{field}",
                        )
                    )
        if isinstance(operation.get("idempotency"), str):
            if operation.get("idempotency") not in VALID_IDEMPOTENCY:
                failures.append(_issue("schema", f"unsupported idempotency for operation {op_name}", path=f"operations[{index}].idempotency"))
        else:
            failures.append(_issue("schema", f"operation {op_name} idempotency is required", path=f"operations[{index}].idempotency"))

    missing = [action for action in REQUIRED_ACTIONS if action not in seen_ops]
    for action in missing:
        failures.append(_issue("schema", f"required action missing: {action}", path="operations"))

    return failures


def evaluate(trace: Any, *, mode: str) -> dict[str, Any]:
    failures = validate(trace, mode=mode)
    operations = trace.get("operations") if isinstance(trace, dict) else []
    checks = {
        "identity": bool(
            isinstance(trace, dict)
            and isinstance(trace.get("identity"), dict)
            and all(bool(trace["identity"].get(field)) for field in REQUIRED_IDENTITY_FIELDS)
        ),
        "idempotency": not any(failure["path"].endswith("idempotency") for failure in failures),
    }
    if isinstance(operations, list):
        for op_name in REQUIRED_ACTIONS:
            checks[op_name] = any(isinstance(item, dict) and item.get("op") == op_name for item in operations)
    status = "PASS" if not failures else "FAIL"
    return {
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "verification_mode": mode,
        "status": status,
        "checked_at": trace.get("checked_at") if isinstance(trace, dict) else None,
        "identity": trace.get("identity") if isinstance(trace, dict) else {},
        "operations": operations,
        "checks": checks,
        "failures": failures,
        "summary": {
            "sufficient": not failures,
            "missing": sorted({failure["message"] for failure in failures}),
            "stop_reason": None if status == "PASS" else "host conformance surface incomplete",
        },
    }


def _read_trace(path: Path | None, mode: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if path is None:
        return {}, [_issue("blocked", "real mode requires a host trace file", path="trace")]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {}, [_issue("blocked", f"cannot read trace: {exc}", path="trace")]
    except json.JSONDecodeError as exc:
        return {}, [_issue("schema", f"trace is not valid JSON: {exc}", path="trace")]
    if not isinstance(value, dict):
        return {}, [_issue("schema", "trace JSON must be an object", path="trace")]
    value["verification_mode"] = mode
    return value, []


def run(mode: str, *, trace_path: Path | None = None) -> dict[str, Any]:
    if mode == "fixture":
        return evaluate(build_fixture_trace(), mode=mode)
    trace, read_failures = _read_trace(trace_path, mode=mode)
    if read_failures:
        report = evaluate({}, mode=mode)
        report["status"] = "BLOCKED"
        report["failures"] = read_failures
        report["summary"] = {
            "sufficient": False,
            "missing": [item["message"] for item in read_failures],
            "stop_reason": "blocked waiting for host trace",
        }
        return report
    return evaluate(trace, mode=mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixture", "real"), required=True)
    parser.add_argument("--trace", type=Path, help="host trace JSON path for real mode")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = run(args.mode, trace_path=args.trace)
    encoded = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
