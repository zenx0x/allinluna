#!/usr/bin/env python3
"""Operation-specific host conformance diagnostics.

The checker consumes a durable-receipt-shaped trace or a fixture.  It validates
the tool actually used for each operation; it does not require Git/worktree
metadata from projectless hosts and is intentionally diagnostic unless a caller
explicitly selects a strict route-assurance policy.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROTOCOL = "allinluna.host_conformance"
SCHEMA_VERSION = "2.0"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0", SCHEMA_VERSION})
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
REQUIRED_IDENTITY_FIELDS = ("thread_id", "host_id")
OPTIONAL_IDENTITY_FIELDS = ("worktree", "repo", "branch", "commit")
VALID_IDEMPOTENCY = {"no-op", "reuse", "wait"}
_TOOLS = {
    "create": "codex_app__create_thread",
    "read": "codex_app__read_thread",
    "wait": "codex_app__wait_threads",
    "cancel": "codex_app__cancel_thread",
}


def _issue(class_name: str, message: str, *, path: str) -> dict[str, str]:
    return {"class": class_name, "message": message, "path": path}


def _identity(*, thread_suffix: str, host: str) -> dict[str, str]:
    return {"thread_id": f"host-thread-{thread_suffix}", "host_id": host}


def _operation(name: str, identity: dict[str, str], *, idempotency: str) -> dict[str, Any]:
    tool = _TOOLS[name]
    return {
        "op": name,
        "thread_id": identity["thread_id"],
        "requested_tool": tool,
        "resolved_tool": tool,
        "actual_tool": tool,
        "requested_capability": tool,
        "resolved_capability": tool,
        "actual_capability": tool,
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
    result = {field: str(value.get(field, "")) for field in REQUIRED_IDENTITY_FIELDS}
    for field in OPTIONAL_IDENTITY_FIELDS:
        if field in value and value[field] is not None:
            if not isinstance(value[field], str) or not value[field].strip():
                failures.append(_issue("schema", f"{path}.{field} must be a non-empty string when supplied", path=f"{path}.{field}"))
            else:
                result[field] = value[field]
    return result


def _validate_route(operation: dict[str, Any], *, index: int, failures: list[dict[str, str]]) -> None:
    path = f"operations[{index}]"
    op_name = str(operation.get("op") or "")
    expected_tool = _TOOLS.get(op_name)
    for field in ("requested_tool", "resolved_tool", "actual_tool"):
        if not isinstance(operation.get(field), str) or not operation[field].strip():
            continue
        if expected_tool and operation[field] != expected_tool:
            failures.append(_issue("operation", f"{op_name} must use {expected_tool}, got {operation[field]!r}", path=f"{path}.{field}"))
    if operation.get("actual_tool") != operation.get("resolved_tool"):
        failures.append(_issue("operation", "actual_tool must match resolved_tool", path=f"{path}.actual_tool"))
    if operation.get("actual_capability") != operation.get("resolved_capability"):
        failures.append(_issue("operation", "actual_capability must match resolved_capability", path=f"{path}.actual_capability"))


def validate(trace: Any, *, mode: str) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    if not isinstance(trace, dict):
        return [_issue("schema", "trace must be a JSON object", path="$")]
    if trace.get("protocol") != PROTOCOL:
        failures.append(_issue("schema", "invalid protocol", path="protocol"))
    if trace.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
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
            if parsed.tzinfo is None:
                raise ValueError("timezone")
        except ValueError:
            failures.append(_issue("schema", "checked_at must be iso8601 with timezone", path="checked_at"))
    operations = trace.get("operations")
    if not isinstance(operations, list) or len(operations) < len(REQUIRED_ACTIONS):
        failures.append(_issue("schema", "operations must contain create/read/wait/cancel", path="operations"))
        return failures
    seen_ops: set[str] = set()
    for index, operation in enumerate(operations):
        path = f"operations[{index}]"
        if not isinstance(operation, dict):
            failures.append(_issue("schema", "operation must be an object", path=path))
            continue
        op_name = operation.get("op")
        if not isinstance(op_name, str) or not op_name.strip():
            failures.append(_issue("schema", "operation.op must be a non-empty string", path=f"{path}.op"))
            continue
        seen_ops.add(op_name)
        if op_name not in REQUIRED_ACTIONS:
            failures.append(_issue("schema", f"unsupported operation {op_name}", path=f"{path}.op"))
            continue
        for field in ACTION_FIELDS:
            if not operation.get(field):
                failures.append(_issue("schema", f"operation.{field} is required", path=f"{path}.{field}"))
        operation_identity = _validate_identity(operation.get("identity"), path=f"{path}.identity", failures=failures)
        if top_identity and operation_identity:
            for field in REQUIRED_IDENTITY_FIELDS:
                if operation_identity.get(field) != top_identity.get(field):
                    failures.append(_issue("schema", "operation identity must match trace identity", path=f"{path}.identity.{field}"))
            for field in OPTIONAL_IDENTITY_FIELDS:
                if field in top_identity and field in operation_identity and operation_identity[field] != top_identity[field]:
                    failures.append(_issue("schema", "operation optional identity must match trace identity when supplied", path=f"{path}.identity.{field}"))
        if operation.get("idempotency") not in VALID_IDEMPOTENCY:
            failures.append(_issue("schema", f"unsupported idempotency for operation {op_name}", path=f"{path}.idempotency"))
        _validate_route(operation, index=index, failures=failures)
    for action in REQUIRED_ACTIONS:
        if action not in seen_ops:
            failures.append(_issue("schema", f"required action missing: {action}", path="operations"))
    return failures


def evaluate(trace: Any, *, mode: str) -> dict[str, Any]:
    failures = validate(trace, mode=mode)
    operations = trace.get("operations") if isinstance(trace, dict) else []
    checks = {
        "identity": bool(isinstance(trace, dict) and isinstance(trace.get("identity"), dict) and all(bool(trace["identity"].get(field)) for field in REQUIRED_IDENTITY_FIELDS)),
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
        report["summary"] = {"sufficient": False, "missing": [item["message"] for item in read_failures], "stop_reason": "blocked waiting for host trace"}
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
