#!/usr/bin/env python3
"""Check a bounded first-use receipt without creating Codex tasks.

The fixture mode is deterministic CI evidence.  Real mode only reads a receipt
written by the Codex host; it never calls a Codex App tool or creates a sidebar
task.  A fixture result can therefore never be reported as ``REAL_PASS``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROTOCOL = "allinluna.first_use"
SCHEMA_VERSION = "1.0"
EVENT_ORDER = (
    "sponsor_started",
    "coordinator_created",
    "owner_dispatch_requested",
    "owner_thread_receipt",
    "repeated_tick",
    "owner_monitor_tick",
    "integration_boundary",
    "stop",
)
FAILURE_CLASSES = {"product_failure", "host_tool_unavailable", "checker_error"}
IDEMPOTENCY_ACTIONS = {"no-op", "reuse", "wait"}


def _identity(
    *,
    thread_id: str,
    role: str,
    host_id: str,
    worktree: str,
    repo: str,
    branch: str,
    commit: str,
) -> dict[str, str]:
    return {
        "thread_id": thread_id,
        "role": role,
        "host_id": host_id,
        "worktree": worktree,
        "repo": repo,
        "branch": branch,
        "commit": commit,
    }


def _event(seq: int, name: str, identity: dict[str, str], **fields: Any) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_id": f"fixture-event-{seq:02d}",
        "event": name,
        "identity": identity,
        **fields,
    }


def build_fixture_receipt(scenario: str = "success") -> dict[str, Any]:
    """Return a deterministic, explicitly synthetic receipt for CI tests."""

    if scenario not in {"success", "failure-recovery"}:
        raise ValueError(f"unsupported fixture scenario: {scenario}")
    host = "fixture-host"
    repo = "fixture://allinluna"
    worktree = "fixture://first-use-isolated"
    branch = "fixture/first-use"
    commit = "fixture-commit"
    sponsor = _identity(
        thread_id="fixture-sponsor",
        role="sponsor",
        host_id=host,
        worktree=worktree,
        repo=repo,
        branch=branch,
        commit=commit,
    )
    coordinator = _identity(
        thread_id="fixture-coordinator",
        role="coordinator",
        host_id=host,
        worktree=worktree,
        repo=repo,
        branch=branch,
        commit=commit,
    )
    owner_identities = [
        _identity(
            thread_id=f"fixture-owner-{name}",
            role="owner",
            host_id=host,
            worktree=f"{worktree}/{name}",
            repo=repo,
            branch=f"{branch}/{name}",
            commit=commit,
        )
        for name in ("api", "docs")
    ]
    events: list[dict[str, Any]] = [_event(1, "sponsor_started", sponsor, scenario=scenario)]
    events.append(
        _event(
            2,
            "coordinator_created",
            coordinator,
            parent_thread_id=sponsor["thread_id"],
            distinct_from=[sponsor["thread_id"]],
            tool_capability={
                "requested_tool": "codex_app__create_thread",
                "resolved_tool": "fixture.codex_app__create_thread",
                "actual_tool": "fixture-simulated",
                "requested_capability": "top-level-task",
                "resolved_capability": "fixture-top-level-task",
                "actual_capability": "fixture-simulated-top-level-task",
                "source": "fixture",
            },
        )
    )
    dispatch_ids: list[str] = []
    for seq, owner in enumerate(owner_identities, start=3):
        dispatch_id = f"fixture-dispatch-{owner['thread_id'].rsplit('-', 1)[-1]}"
        dispatch_ids.append(dispatch_id)
        events.append(
            _event(
                seq,
                "owner_dispatch_requested",
                coordinator,
                dispatch_id=dispatch_id,
                owner_thread_id=owner["thread_id"],
                tool_capability={
                    "requested_tool": "codex_app__create_thread",
                    "resolved_tool": "fixture.codex_app__create_thread",
                    "actual_tool": "fixture-simulated",
                    "requested_capability": "top-level-task",
                    "resolved_capability": "fixture-top-level-task",
                    "actual_capability": "fixture-simulated-top-level-task",
                    "source": "fixture",
                },
            )
        )
    next_seq = 5
    if scenario == "failure-recovery":
        failed = owner_identities[0]
        events.append(
            _event(
                next_seq,
                "owner_thread_receipt",
                failed,
                dispatch_id=dispatch_ids[0],
                receipt={
                    "source": "fixture",
                    "thread_id": failed["thread_id"],
                    "status": "failed",
                    "failure_class": "product_failure",
                },
            )
        )
        next_seq += 1
        events.append(
            _event(
                next_seq,
                "owner_recovery",
                coordinator,
                dispatch_id=dispatch_ids[0],
                recovery_action="reuse",
                failure_class="product_failure",
            )
        )
        next_seq += 1
    for owner, dispatch_id in zip(owner_identities, dispatch_ids, strict=True):
        events.append(
            _event(
                next_seq,
                "owner_thread_receipt",
                owner,
                dispatch_id=dispatch_id,
                receipt={
                    "source": "fixture",
                    "thread_id": owner["thread_id"],
                    "host_id": owner["host_id"],
                    "worktree": owner["worktree"],
                    "repo": owner["repo"],
                    "status": "completed",
                    "actual_tool": "fixture-simulated",
                },
            )
        )
        next_seq += 1
    events.append(
        _event(
            next_seq,
            "repeated_tick",
            coordinator,
            tick=2,
            dispatch_ids=dispatch_ids,
            idempotency={"duplicate_dispatch": "no-op", "completed_owner": "reuse", "pending_owner": "wait"},
        )
    )
    next_seq += 1
    events.append(
        _event(
            next_seq,
            "owner_monitor_tick",
            coordinator,
            cursor="fixture-cursor-2",
            receipts=[owner["thread_id"] for owner in owner_identities],
            wait_action="wait",
        )
    )
    next_seq += 1
    events.append(
        _event(
            next_seq,
            "integration_boundary",
            coordinator,
            boundary="mechanical-only",
            owner_outputs=[owner["thread_id"] for owner in owner_identities],
            allowed_actions=["reconcile receipts", "verify paths", "run cross-lane checks"],
            forbidden_actions=["rewrite owner semantics", "create new owner", "publish", "push"],
        )
    )
    next_seq += 1
    events.append(
        _event(
            next_seq,
            "stop",
            sponsor,
            reason="fixture evidence complete",
            evidence_sufficiency={"sufficient": True, "missing": [], "real_pass": False},
        )
    )
    return {
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "verification_mode": "fixture",
        "scenario": scenario,
        "identities": {
            "sponsor": sponsor,
            "coordinator": coordinator,
            "owners": owner_identities,
            "host_id": host,
            "worktree": worktree,
            "repo": repo,
        },
        "capability_evidence": [
            {
                "requested_tool": "codex_app__create_thread",
                "resolved_tool": "fixture.codex_app__create_thread",
                "actual_tool": "fixture-simulated",
                "requested_capability": "top-level-task",
                "resolved_capability": "fixture-top-level-task",
                "actual_capability": "fixture-simulated-top-level-task",
                "source": "fixture",
            }
        ],
        "events": events,
        "monitor": {"cursor": "fixture-cursor-2", "receipts": ["fixture-owner-api", "fixture-owner-docs"]},
        "integration_boundary": {
            "boundary": "mechanical-only",
            "source": "fixture",
            "semantic_owner": "original-owner",
        },
        "failures": (
            [{"class": "product_failure", "event": "owner_thread_receipt", "recovered": True}]
            if scenario == "failure-recovery"
            else []
        ),
    }


def _issue(failure_class: str, message: str, *, path: str | None = None) -> dict[str, str]:
    result = {"class": failure_class, "message": message}
    if path:
        result["path"] = path
    return result


def validate_receipt(receipt: Any, *, mode: str) -> list[dict[str, str]]:
    """Return typed checker issues; this function has no external side effects."""

    errors: list[dict[str, str]] = []
    if not isinstance(receipt, dict):
        return [_issue("checker_error", "receipt must be a JSON object", path="$")]
    if receipt.get("protocol") != PROTOCOL:
        errors.append(_issue("checker_error", "unexpected protocol", path="protocol"))
    if receipt.get("schema_version") != SCHEMA_VERSION:
        errors.append(_issue("checker_error", "unsupported schema version", path="schema_version"))
    if receipt.get("verification_mode") != mode:
        errors.append(_issue("checker_error", "receipt verification_mode does not match checker mode", path="verification_mode"))
    if mode == "real" and receipt.get("verification_mode") == "fixture":
        errors.append(_issue("checker_error", "fixture evidence cannot be promoted to real evidence", path="verification_mode"))
    identities = receipt.get("identities")
    if not isinstance(identities, dict):
        errors.append(_issue("checker_error", "identities object is required", path="identities"))
        identities = {}
    sponsor = identities.get("sponsor", {})
    coordinator = identities.get("coordinator", {})
    owners = identities.get("owners", [])
    if not isinstance(sponsor, dict) or not sponsor.get("thread_id"):
        errors.append(_issue("checker_error", "sponsor thread identity is required", path="identities.sponsor"))
    if not isinstance(coordinator, dict) or not coordinator.get("thread_id"):
        errors.append(_issue("checker_error", "coordinator thread identity is required", path="identities.coordinator"))
    if isinstance(sponsor, dict) and isinstance(coordinator, dict) and sponsor.get("thread_id") == coordinator.get("thread_id"):
        errors.append(_issue("product_failure", "Sponsor and Coordinator must be distinct threads", path="identities.coordinator.thread_id"))
    if not isinstance(owners, list) or len(owners) < 2:
        errors.append(_issue("product_failure", "at least two top-level Owners are required", path="identities.owners"))
        owners = []
    thread_ids = [item.get("thread_id") for item in [sponsor, coordinator, *owners] if isinstance(item, dict)]
    if len(thread_ids) != len(set(thread_ids)):
        errors.append(_issue("product_failure", "control-plane and Owner thread IDs must be unique", path="identities"))
    events = receipt.get("events")
    if not isinstance(events, list) or not events:
        errors.append(_issue("checker_error", "events must be a non-empty array", path="events"))
        events = []
    names = [event.get("event") for event in events if isinstance(event, dict)]
    if names[:2] != list(EVENT_ORDER[:2]):
        errors.append(_issue("product_failure", "receipt must start with Sponsor then Coordinator", path="events"))
    required = set(EVENT_ORDER)
    missing = required.difference(names)
    for name in sorted(missing):
        errors.append(_issue("product_failure", f"required protocol event is missing: {name}", path="events"))
    sequences = [event.get("seq") for event in events if isinstance(event, dict)]
    if sequences != list(range(1, len(sequences) + 1)):
        errors.append(_issue("checker_error", "event seq values must be contiguous from 1", path="events.seq"))
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(_issue("checker_error", "event must be an object", path=f"events[{index}]"))
            continue
        if not event.get("event_id") or not isinstance(event.get("identity"), dict):
            errors.append(_issue("checker_error", "event_id and identity are required", path=f"events[{index}]"))
        if event.get("event") == "owner_dispatch_requested":
            capability = event.get("tool_capability")
            for field in ("requested_tool", "resolved_tool", "actual_tool", "requested_capability", "resolved_capability", "actual_capability"):
                if not isinstance(capability, dict) or not capability.get(field):
                    errors.append(_issue("checker_error", f"dispatch evidence missing {field}", path=f"events[{index}].tool_capability"))
        if event.get("event") == "owner_thread_receipt":
            receipt_data = event.get("receipt")
            if not isinstance(receipt_data, dict):
                errors.append(_issue("checker_error", "Owner thread receipt must be an object", path=f"events[{index}].receipt"))
            elif receipt_data.get("client_thread_id") and not receipt_data.get("thread_id"):
                errors.append(_issue("host_tool_unavailable", "pending clientThreadId has no host thread receipt yet", path=f"events[{index}].receipt"))
            elif not receipt_data.get("thread_id"):
                errors.append(_issue("host_tool_unavailable", "host did not return a real thread_id", path=f"events[{index}].receipt"))
            if mode == "real" and isinstance(receipt_data, dict):
                if receipt_data.get("source") != "codex_app":
                    errors.append(_issue("host_tool_unavailable", "real mode requires a Codex App receipt", path=f"events[{index}].receipt.source"))
                if receipt_data.get("actual_tool") != "codex_app__create_thread":
                    errors.append(_issue("host_tool_unavailable", "real mode requires actual Codex App tool evidence", path=f"events[{index}].receipt.actual_tool"))
    repeated = next((event for event in events if isinstance(event, dict) and event.get("event") == "repeated_tick"), None)
    if repeated:
        idem = repeated.get("idempotency", {})
        if not isinstance(idem, dict) or not set(idem.values()).intersection(IDEMPOTENCY_ACTIONS):
            errors.append(_issue("product_failure", "repeated tick has no no-op/reuse/wait evidence", path="repeated_tick.idempotency"))
    monitor = receipt.get("monitor")
    if not isinstance(monitor, dict) or not monitor.get("cursor") or not monitor.get("receipts"):
        errors.append(_issue("host_tool_unavailable" if mode == "real" else "checker_error", "monitor cursor and receipts are required", path="monitor"))
    if mode == "real":
        capabilities = receipt.get("capability_evidence")
        if not isinstance(capabilities, list) or not capabilities:
            errors.append(_issue("host_tool_unavailable", "real receipt lacks capability evidence", path="capability_evidence"))
        else:
            for index, capability in enumerate(capabilities):
                if not isinstance(capability, dict) or capability.get("source") != "codex_app" or capability.get("actual_tool") != "codex_app__create_thread":
                    errors.append(_issue("host_tool_unavailable", "real capability evidence must come from Codex App", path=f"capability_evidence[{index}]"))
        if isinstance(monitor, dict) and monitor.get("source") != "codex_app":
            errors.append(_issue("host_tool_unavailable", "real monitor evidence must come from the Codex host", path="monitor.source"))
    boundary = receipt.get("integration_boundary")
    if not isinstance(boundary, dict) or boundary.get("boundary") != "mechanical-only":
        errors.append(_issue("product_failure", "integration boundary must be mechanical-only", path="integration_boundary"))
    elif mode == "real" and boundary.get("source") != "codex_app":
        errors.append(_issue("host_tool_unavailable", "real integration evidence must come from the Codex host", path="integration_boundary.source"))
    if mode == "real":
        for name, identity in (("sponsor", sponsor), ("coordinator", coordinator), *[("owner", owner) for owner in owners]):
            if isinstance(identity, dict) and (str(identity.get("repo", "")).startswith("fixture://") or str(identity.get("worktree", "")).startswith("fixture://")):
                errors.append(_issue("host_tool_unavailable", f"real {name} identity cannot use fixture paths", path=f"identities.{name}"))
        for event in events:
            if isinstance(event, dict) and event.get("event") == "owner_thread_receipt":
                receipt_data = event.get("receipt", {})
                if isinstance(receipt_data, dict) and receipt_data.get("worktree") and receipt_data.get("repo"):
                    continue
                errors.append(_issue("host_tool_unavailable", "real receipt lacks worktree/repo identity", path="events.receipt"))
    return errors


def evaluate_receipt(receipt: dict[str, Any], *, mode: str) -> dict[str, Any]:
    errors = validate_receipt(receipt, mode=mode)
    failures = list(receipt.get("failures", [])) if isinstance(receipt.get("failures"), list) else []
    failures.extend(errors)
    classes = {item.get("class") for item in failures if isinstance(item, dict)}
    if mode == "fixture":
        status = "FIXTURE_PASS" if not errors else "FIXTURE_FAIL"
        failure_class = next(iter(classes), None)
        real_pass = False
    elif not receipt:
        status, failure_class, real_pass = "BLOCKED", "host_tool_unavailable", False
    elif "checker_error" in classes:
        status, failure_class, real_pass = "CHECKER_ERROR", "checker_error", False
    elif "host_tool_unavailable" in classes:
        status, failure_class, real_pass = "BLOCKED", "host_tool_unavailable", False
    elif "product_failure" in classes:
        status, failure_class, real_pass = "FAIL", "product_failure", False
    else:
        status, failure_class, real_pass = "REAL_PASS", None, True
    missing = sorted({item.get("message", "unknown evidence") for item in errors if isinstance(item, dict)})
    return {
        "protocol": PROTOCOL,
        "schema_version": SCHEMA_VERSION,
        "verification_mode": mode,
        "status": status,
        "real_pass": real_pass,
        "failure_class": failure_class,
        "failures": failures,
        "identities": receipt.get("identities", {}),
        "capability_evidence": receipt.get("capability_evidence", []),
        "events": receipt.get("events", []),
        "idempotency": next((event.get("idempotency", {}) for event in receipt.get("events", []) if isinstance(event, dict) and event.get("event") == "repeated_tick"), {}),
        "monitor": receipt.get("monitor", {}),
        "integration_boundary": receipt.get("integration_boundary", {}),
        "evidence_sufficiency": {
            "sufficient": not errors and (mode == "fixture" or real_pass),
            "missing": missing,
            "stop_reason": "fixture evidence is intentionally not real host evidence" if mode == "fixture" else ("complete host receipt" if real_pass else "stop at first-use boundary"),
        },
    }


def _read_real_receipt(path: Path | None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if path is None:
        return {}, [_issue("host_tool_unavailable", "real mode requires an external Codex host receipt")]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {}, [_issue("host_tool_unavailable", f"cannot read host receipt: {exc}")]
    except json.JSONDecodeError as exc:
        return {}, [_issue("checker_error", f"host receipt is not valid JSON: {exc}")]
    if not isinstance(value, dict):
        return {}, [_issue("checker_error", "host receipt JSON must be an object")]
    return value, []


def run(mode: str, *, scenario: str = "success", receipt_path: Path | None = None) -> dict[str, Any]:
    if mode == "fixture":
        return evaluate_receipt(build_fixture_receipt(scenario), mode=mode)
    receipt, read_errors = _read_real_receipt(receipt_path)
    if read_errors:
        report = evaluate_receipt({}, mode=mode)
        report["failures"] = read_errors
        report["failure_class"] = read_errors[0]["class"]
        report["status"] = "BLOCKED" if read_errors[0]["class"] == "host_tool_unavailable" else "CHECKER_ERROR"
        report["evidence_sufficiency"] = {"sufficient": False, "missing": [item["message"] for item in read_errors], "stop_reason": "stop at first-use boundary"}
        return report
    return evaluate_receipt(receipt, mode=mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixture", "real"), required=True)
    parser.add_argument("--scenario", choices=("success", "failure-recovery"), default="success")
    parser.add_argument("--receipt", type=Path, help="external Codex host receipt (real mode only)")
    parser.add_argument("--output", type=Path, help="optional report path; stdout is the default")
    args = parser.parse_args(argv)
    report = run(args.mode, scenario=args.scenario, receipt_path=args.receipt)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0 if report["status"] in {"FIXTURE_PASS", "REAL_PASS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
