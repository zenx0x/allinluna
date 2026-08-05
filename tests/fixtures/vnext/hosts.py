"""Deterministic fake host adapters for vNext integration tests.

The fakes record the action sent to them and return test-host receipts.  They never claim
to be a receipt from the current Codex App, which keeps provenance assertions meaningful.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .contracts import Correction, PromotionRequest


class HostLostError(RuntimeError):
    """Raised when a test host is deliberately made unavailable."""


@dataclass(frozen=True)
class HostAction:
    action_id: str
    kind: str
    host_id: str
    task_id: str
    dispatch_id: str
    idempotency_key: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HostReceipt:
    receipt_id: str
    kind: str
    source: str
    host_id: str
    task_id: str
    dispatch_id: str
    idempotency_key: str
    status: str
    client_thread_id: str | None = None
    thread_id: str | None = None
    worktree: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    is_real_codex_app_receipt: bool = False
    duplicate_of: str | None = None
    subagent_created: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["provenance"] = "test-fixture"
        return result


def _stable_id(prefix: str, *parts: object) -> str:
    material = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _require_text(action: Mapping[str, Any], key: str) -> str:
    value = action.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"host action requires non-empty {key}")
    return value


class FakeCodexHost:
    """A fake Codex App boundary with observable idempotency and failure modes."""

    source = "test.fake_codex_host"
    actual_create_tool = "codex_app__create_thread"

    def __init__(self, host_id: str = "fake-codex-host-1") -> None:
        self.host_id = host_id
        self._available = True
        self._actions: list[HostAction] = []
        self._invocations: list[dict[str, Any]] = []
        self._receipts: dict[str, HostReceipt] = {}
        self._pending: dict[str, HostReceipt] = {}
        self._pending_responses: dict[str, HostReceipt] = {}
        self._tasks: dict[str, HostReceipt] = {}

    @property
    def actions(self) -> tuple[HostAction, ...]:
        return tuple(self._actions)

    @property
    def invocations(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._invocations)

    @property
    def receipts(self) -> tuple[HostReceipt, ...]:
        return tuple(self._receipts.values())

    def discover(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "host_kind": "fake-codex-app",
            "source": self.source,
            "available": self._available,
            "tools": [
                self.actual_create_tool,
                "codex_app__wait_threads",
                "codex_app__read_thread",
                "codex_app__send_message_to_thread",
            ],
            "receipt_provenance": "test-fixture",
            "is_real_codex_app": False,
        }

    def lose_host(self) -> None:
        self._available = False

    def restore_host(self) -> None:
        self._available = True

    def create_top_level_task(self, action: Mapping[str, Any]) -> HostReceipt:
        task_id = _require_text(action, "task_id")
        dispatch_id = _require_text(action, "dispatch_id")
        idempotency_key = _require_text(action, "idempotency_key")
        self._invocations.append(dict(action))
        if idempotency_key in self._receipts:
            original = self._receipts[idempotency_key]
            return replace(original, duplicate_of=original.receipt_id)
        if idempotency_key in self._pending_responses:
            return replace(
                self._pending_responses[idempotency_key],
                duplicate_of=self._pending_responses[idempotency_key].receipt_id,
            )
        if not self._available:
            raise HostLostError(f"host {self.host_id} is unavailable")

        action_record = HostAction(
            action_id=_stable_id("action", self.host_id, dispatch_id, idempotency_key),
            kind="create_top_level_task",
            host_id=self.host_id,
            task_id=task_id,
            dispatch_id=dispatch_id,
            idempotency_key=idempotency_key,
            payload=dict(action),
        )
        self._actions.append(action_record)
        client_thread_id = _stable_id("client-thread", self.host_id, idempotency_key)
        thread_id = _stable_id("thread", self.host_id, idempotency_key)
        receipt = HostReceipt(
            receipt_id=_stable_id("receipt", self.host_id, idempotency_key),
            kind="thread-receipt",
            source=self.source,
            host_id=self.host_id,
            task_id=task_id,
            dispatch_id=dispatch_id,
            idempotency_key=idempotency_key,
            status="active",
            client_thread_id=client_thread_id,
            thread_id=thread_id,
            worktree=action.get("worktree"),
            branch=action.get("branch"),
            base_commit=action.get("base_commit"),
        )
        if action.get("delay_receipt"):
            self._pending[idempotency_key] = receipt
            pending = HostReceipt(
                receipt_id=_stable_id("pending", self.host_id, idempotency_key),
                kind="dispatch-receipt",
                source=self.source,
                host_id=self.host_id,
                task_id=task_id,
                dispatch_id=dispatch_id,
                idempotency_key=idempotency_key,
                status="pending",
                client_thread_id=client_thread_id,
            )
            self._pending_responses[idempotency_key] = pending
            return pending
        self._receipts[idempotency_key] = receipt
        self._tasks[task_id] = receipt
        return receipt

    def release_delayed_receipt(self, idempotency_key: str) -> HostReceipt:
        try:
            receipt = self._pending.pop(idempotency_key)
        except KeyError as exc:
            raise KeyError(f"no delayed receipt for {idempotency_key}") from exc
        self._pending_responses.pop(idempotency_key, None)
        self._receipts[idempotency_key] = receipt
        self._tasks[receipt.task_id] = receipt
        return receipt

    def mark_task_lost(self, task_id: str) -> HostReceipt:
        previous = self._tasks[task_id]
        lost = replace(previous, status="lost", kind="host-lost-receipt")
        self._tasks[task_id] = lost
        return lost

    def wait_tasks(
        self, targets: Sequence[Mapping[str, Any]], cursor: str | None = None
    ) -> dict[str, Any]:
        return {
            "kind": "wait-receipt",
            "source": self.source,
            "cursor": cursor,
            "targets": [dict(target) for target in targets],
            "statuses": {
                task_id: self._tasks[task_id].status
                for target in targets
                if (task_id := target.get("task_id")) in self._tasks
            },
            "is_real_codex_app_receipt": False,
        }

    def read_task(
        self, target: Mapping[str, Any], cursor: str | None = None
    ) -> dict[str, Any]:
        task_id = _require_text(target, "task_id")
        receipt = self._tasks[task_id]
        return {
            "kind": "task-read-receipt",
            "source": self.source,
            "cursor": cursor,
            "task_id": task_id,
            "receipt": receipt.to_dict(),
            "is_real_codex_app_receipt": False,
        }

    def send_message(
        self, target: Mapping[str, Any], envelope: Mapping[str, Any]
    ) -> HostReceipt:
        task_id = _require_text(target, "task_id")
        correction_id = _require_text(envelope, "correction_id")
        previous = self._tasks[task_id]
        return HostReceipt(
            receipt_id=_stable_id("message-receipt", self.host_id, task_id, correction_id),
            kind="correction-receipt",
            source=self.source,
            host_id=self.host_id,
            task_id=task_id,
            dispatch_id=previous.dispatch_id,
            idempotency_key=_stable_id("correction-key", task_id, correction_id),
            status="accepted",
            client_thread_id=previous.client_thread_id,
            thread_id=previous.thread_id,
        )


class FakeDistributedCodexHost:
    """A public ``create_thread`` host whose child owns the Lane runtime.

    This fixture intentionally has no ``create_top_level_task`` method.  The
    coordinator can cross the host boundary only with the public five-field
    create-thread shape.  Once a thread is waited/read, the fake child opens
    the shared Store, parses the prompt's bootstrap, and constructs its own
    ``LaneDriver``.  The parent never receives a LaneEngine object or a
    hidden action payload.
    """

    source = "test.fake_distributed_codex_host"
    actual_create_tool = "codex_app__create_thread"

    def __init__(
        self,
        *,
        blocked_tasks: Sequence[str] = (),
        workspace_base_commit: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        self.host_id = "fake-distributed-codex-host-1"
        self.blocked_tasks = {str(item) for item in blocked_tasks}
        self.workspace_base_commit = workspace_base_commit
        self.workspace_path = workspace_path
        self.public_calls: list[dict[str, Any]] = []
        self.call_log: list[dict[str, Any]] = []
        self.child_events: list[dict[str, Any]] = []
        self.child_bootstraps: list[dict[str, Any]] = []
        self._threads: dict[str, dict[str, Any]] = {}
        self._work_units: dict[str, dict[str, Any]] = {}
        self._created_task_ids: list[str] = []
        self._cursor = 0

    @property
    def created_task_ids(self) -> tuple[str, ...]:
        return tuple(self._created_task_ids)

    @property
    def child_handoffs(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(thread["handoff"])
            for thread in self._threads.values()
            if isinstance(thread.get("handoff"), Mapping)
        )

    @property
    def child_run_count(self) -> int:
        return sum(1 for event in self.child_events if event.get("event") == "child-start")

    def discover(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "host_kind": "fake-codex-app-distributed",
            "source": self.source,
            "available": True,
            "tools": [
                self.actual_create_tool,
                "codex_app__list_projects",
                "codex_app__wait_threads",
                "codex_app__read_thread",
            ],
            "receipt_provenance": "test-fixture",
            "is_real_codex_app": False,
        }

    def list_projects(self) -> dict[str, Any]:
        """Return the explicit project-resolution receipt used by v2 tests."""

        root = str(self.workspace_path or "")
        project_id = _stable_id("project", root or self.host_id)
        environment = {
            "worktree": root,
            "root": root,
            "branch": "lane-fixture",
        }
        return {
            "protocol": "host-receipt/v1",
            "kind": "project-resolution-receipt",
            "receipt_id": _stable_id("project-receipt", project_id),
            "status": "completed",
            "source": self.source,
            "host_id": self.host_id,
            "actual_tool": "codex_app__list_projects",
            "actual_capability": "codex_app__list_projects",
            "projects": [{"projectId": project_id, "root": root, "environment": environment}],
        }

    def create_thread(
        self,
        target: Mapping[str, Any],
        prompt: str,
        model: str,
        thinking: str,
        title: str,
    ) -> dict[str, Any]:
        """Accept exactly the public create_thread arguments.

        The method signature is the regression boundary: passing a durable
        action, payload, task envelope, or idempotency key raises TypeError.
        The child recovers those values from the canonical bootstrap instead.
        """

        public = {
            "target": dict(target),
            "prompt": str(prompt),
            "model": str(model),
            "thinking": str(thinking),
            "title": str(title),
        }
        self.public_calls.append(public)
        match = re.search(r'"task_id"\s*:\s*"([^"]+)"', public["prompt"])
        task_id = str(match.group(1) if match else "public-call-" + str(len(self.public_calls)))
        self.call_log.append({"method": "create_thread", "task_id": task_id})
        if not task_id:
            raise ValueError("public create_thread target requires task_id")
        thread_id = _stable_id("distributed-thread", self.host_id, task_id, prompt)
        if thread_id not in self._threads:
            self._threads[thread_id] = {
                "thread_id": thread_id,
                "task_id": task_id,
                "public": public,
                "handoff": None,
                "status": "active",
                "child_error": None,
            }
            self._created_task_ids.append(task_id)
        return {
            "protocol": "host-receipt/v1",
            "receipt_id": _stable_id("distributed-receipt", thread_id),
            "thread_id": thread_id,
            "status": "active",
            "source": self.source,
            "host_id": self.host_id,
            "actual": True,
            "actual_tool": self.actual_create_tool,
            "actual_capability": self.actual_create_tool,
        }

    def wait_tasks(
        self, targets: Sequence[Mapping[str, Any]], cursor: str | None = None
    ) -> dict[str, Any]:
        """Parent-side wait; child work happens behind this host boundary."""

        target_values = [dict(target) for target in targets]
        self.call_log.append({"method": "wait_tasks", "targets": target_values})
        statuses: dict[str, str] = {}
        for target in target_values:
            thread_id = str(target.get("thread_id") or target.get("threadId") or "")
            thread = self._threads.get(thread_id)
            if thread is None:
                continue
            if thread["handoff"] is None and thread["child_error"] is None:
                self._run_child(thread)
            statuses[str(thread["task_id"])] = str(thread["status"])
        self._cursor += 1
        return {
            "kind": "wait-receipt",
            "protocol": "host-receipt/v1",
            "source": self.source,
            "cursor": f"distributed-cursor-{self._cursor}",
            "targets": target_values,
            "statuses": statuses,
        }

    def read_task(
        self, target: Mapping[str, Any], cursor: str | None = None
    ) -> dict[str, Any]:
        """Parent-side read; only the typed child handoff crosses upward."""

        target_value = dict(target)
        thread_id = str(target_value.get("thread_id") or target_value.get("threadId") or "")
        thread = self._threads[thread_id]
        self.call_log.append({"method": "read_task", "thread_id": thread_id})
        result: dict[str, Any] = {
            "kind": "task-read-receipt",
            "protocol": "host-receipt/v1",
            "source": self.source,
            "cursor": cursor,
            "thread_id": thread_id,
            "task_id": thread["task_id"],
            "status": thread["status"],
        }
        if thread["handoff"] is not None:
            result["handoff"] = thread["handoff"]
        if thread["child_error"] is not None:
            raise AssertionError(f"distributed child failed: {thread['child_error']}")
        return result

    def spawn(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        """Child-side local WorkUnit dispatch using the same host boundary."""

        value = dict(envelope)
        work_unit_id = str(value.get("work_unit_id") or "")
        if not work_unit_id:
            raise ValueError("work-unit envelope requires work_unit_id")
        task_id = str(value.get("task_ref") or "").removeprefix("task://")
        if not task_id:
            task_id = str(value.get("parent_work_unit_id") or "")
        key = str(value.get("idempotency_key") or work_unit_id)
        self.call_log.append({"method": "spawn", "work_unit_id": work_unit_id, "task_id": task_id})
        prior = self._work_units.get(work_unit_id)
        if prior is not None:
            return dict(prior["receipt"])
        thread_id = _stable_id("distributed-work-thread", self.host_id, key)
        receipt = {
            "protocol": "host-receipt/v1",
            "receipt_id": _stable_id("distributed-work-receipt", thread_id),
            "thread_id": thread_id,
            "status": "active",
            "source": self.source,
            "host_id": self.host_id,
            "actual": True,
            "work_unit_id": work_unit_id,
            "idempotency_key": key,
        }
        self._work_units[work_unit_id] = {
            "thread_id": thread_id,
            "task_id": task_id,
            "envelope": value,
            "receipt": receipt,
        }
        return dict(receipt)

    def wait(self, work_unit_ids: Sequence[str], cursor: str | None = None) -> dict[str, Any]:
        ids = [str(item) for item in work_unit_ids]
        self.call_log.append({"method": "wait", "work_unit_ids": ids})
        return {
            "kind": "work-wait-receipt",
            "protocol": "host-receipt/v1",
            "source": self.source,
            "cursor": cursor,
            "work_unit_ids": ids,
        }

    def read(self, work_unit_id: str, cursor: str | None = None) -> dict[str, Any]:
        unit_id = str(work_unit_id)
        unit = self._work_units[unit_id]
        self.call_log.append({"method": "read", "work_unit_id": unit_id, "task_id": unit["task_id"]})
        status = "blocked" if unit["task_id"] in self.blocked_tasks else "completed"
        handoff = {
            "kind": "handoff",
            "schema_version": "1.0",
            "protocol": "work-handoff/v1",
            "handoff_kind": "work",
            "handoff_id": _stable_id("distributed-work-handoff", unit_id),
            "work_unit_id": unit_id,
            "status": status,
            "summary": f"distributed work unit {unit_id} {status}",
            "changed_paths": [],
            "blockers": [] if status == "completed" else [{"code": "fixture.blocked", "message": "fixture blocked lane", "recoverable": True}],
        }
        return {
            "kind": "work-read-receipt",
            "protocol": "host-receipt/v1",
            "source": self.source,
            "cursor": cursor,
            "thread_id": unit["thread_id"],
            "work_unit_id": unit_id,
            "handoff": handoff,
        }

    @staticmethod
    def _bootstrap_from_prompt(prompt: str) -> Any:
        from allinluna_runtime.protocols.lane_bootstrap import LaneBootstrapEnvelope

        marker = "```json"
        start = prompt.find(marker)
        if start < 0:
            raise ValueError("child prompt does not contain a JSON LaneBootstrapEnvelope")
        start += len(marker)
        end = prompt.find("```", start)
        if end < 0:
            raise ValueError("child prompt has an unterminated bootstrap JSON fence")
        value = json.loads(prompt[start:end].strip())
        return LaneBootstrapEnvelope.from_value(value)

    def _run_child(self, thread: dict[str, Any]) -> None:
        """Execute the child thread as a separate Store/driver lifecycle."""

        from allinluna_runtime.artifacts import ArtifactStore
        from allinluna_runtime.evidence import CheckRunner, EvidenceCollector
        from allinluna_runtime.engine.lane_driver import LaneDriver
        from allinluna_runtime.store import Store

        thread_id = str(thread["thread_id"])
        public = thread["public"]
        self.child_events.append({"event": "child-start", "thread_id": thread_id, "task_id": thread["task_id"]})
        child_store = None
        try:
            bootstrap = self._bootstrap_from_prompt(str(public["prompt"]))
            self.child_bootstraps.append(bootstrap.to_dict())
            child_store = Store(bootstrap.runtime_path)
            loaded = bootstrap.validate_store(child_store)
            self.child_events.append(
                {
                    "event": "child-bootstrap-loaded",
                    "thread_id": thread_id,
                    "loaded": sorted(loaded),
                }
            )
            driver = LaneDriver.from_bootstrap(child_store, bootstrap, host=self)
            driven = driver.drive(max_cycles=8)
            self.child_events.append(
                {
                    "event": "child-lane-driven",
                    "thread_id": thread_id,
                    "cycles": len(driven.get("cycles", ())),
                    "boundary": (driven.get("boundary") or {}).get("kind"),
                }
            )
            handoff = driven.get("handoff")
            if str(thread["task_id"]) in self.blocked_tasks:
                handoff = driver.lane.synthesize_handoff(
                    status="blocked", summary="distributed child lane is blocked by fixture"
                )
            elif not isinstance(handoff, Mapping):
                raise AssertionError(f"child LaneDriver did not return a lane handoff: {driven}")
            else:
                artifacts = ArtifactStore(child_store, root=Path(bootstrap.runtime_db).parent / "artifacts")
                workspace_adapter = None
                evidence_profile = "projectless-analysis"
                if self.workspace_base_commit:
                    from allinluna_runtime.adapters.workspace.git import GitWorktreeAdapter

                    workspace_adapter = GitWorktreeAdapter(
                        self.workspace_path or bootstrap.workspace,
                        base_commit=self.workspace_base_commit,
                    )
                    evidence_profile = "software"
                collector = EvidenceCollector(
                    child_store,
                    artifact_store=artifacts,
                    workspace_adapter=workspace_adapter,
                    check_runner=CheckRunner(artifacts),
                    profile=evidence_profile,
                )
                # LaneDriver owns execution; the child may attach its
                # independently-created collector before synthesizing the
                # evidence-bearing response.
                driver.lane.evidence_collector = collector
                task = child_store.get_task(bootstrap.task_id) or {}
                contract = child_store.get_contract(
                    str(task.get("contract_id") or ""), int(task.get("contract_version", 1))
                ) or {}
                conditions = [str(item) for item in contract.get("done_when", ()) or ()]
                checks = [
                    {
                        "name": condition,
                        "command": [sys.executable, "-c", "print('distributed-child-check')"],
                        "satisfies": [condition],
                    }
                    for condition in conditions
                ] or [
                    {
                        "name": "distributed child lane completed",
                        "command": [sys.executable, "-c", "print('distributed-child-check')"],
                    }
                ]
                exports = []
                for item in contract.get("exports", ()) or ():
                    name = str(item.get("name") if isinstance(item, Mapping) else item)
                    if not name:
                        continue
                    artifact = artifacts.put(
                        f"{bootstrap.task_id}:{name}".encode("utf-8"),
                        kind="summary",
                        produced_by="fake-distributed-child",
                    )
                    exports.append({"name": name, "artifact_ref": artifact.ref, "version": 1})
                workspace_scope = None
                if workspace_adapter is not None:
                    ownership = [
                        str(item.get("path") if isinstance(item, Mapping) else item)
                        for item in task.get("ownership", ()) or ()
                    ]
                    workspace_scope = {
                        "worktree": str(self.workspace_path or bootstrap.workspace),
                        "base_commit": self.workspace_base_commit,
                        "ownership": ownership,
                        "protected_paths": [],
                    }
                handoff = driver.lane.collect_handoff_evidence(
                    handoff,
                    checks=checks,
                    exports=exports,
                    workspace_scope=workspace_scope,
                    profile=evidence_profile,
                )
            thread["handoff"] = dict(handoff)
            thread["status"] = str(handoff.get("status") or "active")
            self.child_events.append(
                {
                    "event": "child-handoff-ready",
                    "thread_id": thread_id,
                    "protocol": handoff.get("protocol"),
                    "status": handoff.get("status"),
                }
            )
        except Exception as exc:
            thread["child_error"] = f"{type(exc).__name__}: {exc}"
            thread["status"] = "failed"
            self.child_events.append(
                {"event": "child-failed", "thread_id": thread_id, "error": thread["child_error"]}
            )
        finally:
            if child_store is not None:
                child_store.close()


def _path_is_within(child: str, parent: str) -> bool:
    child = child.rstrip("/")
    parent = parent.rstrip("/")
    if parent.endswith("/**"):
        parent = parent[:-3].rstrip("/")
    if child.endswith("/**"):
        child = child[:-3].rstrip("/")
    return child == parent or child.startswith(parent + "/")


def _all_paths_within(children: Sequence[str], parents: Sequence[str]) -> bool:
    return all(any(_path_is_within(child, parent) for parent in parents) for child in children)


class FakeSubagentHost:
    """Fake native subagent host enforcing recursive ownership narrowing."""

    source = "test.fake_subagent_host"

    def __init__(self, host_id: str = "fake-subagent-host-1", native: bool = True) -> None:
        self.host_id = host_id
        self.native = native
        self._available = True
        self._actions: list[HostAction] = []
        self._receipts: dict[str, HostReceipt] = {}

    @property
    def actions(self) -> tuple[HostAction, ...]:
        return tuple(self._actions)

    def discover(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "host_kind": "fake-native-subagent",
            "source": self.source,
            "available": self._available,
            "native_subagent": self.native,
            "receipt_provenance": "test-fixture",
        }

    def lose_host(self) -> None:
        self._available = False

    def spawn(self, envelope: Mapping[str, Any]) -> HostReceipt:
        work_unit_id = _require_text(envelope, "work_unit_id")
        parent_id = _require_text(envelope, "parent_work_unit_id")
        key = _require_text(envelope, "idempotency_key")
        parent_scope = tuple(envelope.get("parent_scope", ())) or tuple(envelope.get("scope", ()))
        child_scope = tuple(envelope.get("scope", ()))
        parent_ownership = tuple(envelope.get("parent_ownership", ())) or tuple(envelope.get("ownership", ()))
        child_ownership = tuple(envelope.get("ownership", ()))
        parent_authority = tuple(envelope.get("parent_authority", ())) or tuple(envelope.get("authority", ()))
        child_authority = tuple(envelope.get("authority", ()))
        if not _all_paths_within(child_scope, parent_scope):
            raise ValueError("child scope must be a subset of parent scope")
        if not _all_paths_within(child_ownership, parent_ownership):
            raise ValueError("child ownership must be a subset of parent ownership")
        if not set(child_authority).issubset(parent_authority):
            raise ValueError("child authority must be a subset of parent authority")
        if key in self._receipts:
            return self._receipts[key]
        if not self._available:
            raise HostLostError(f"host {self.host_id} is unavailable")

        action = HostAction(
            action_id=_stable_id("subagent-action", self.host_id, key),
            kind="spawn_subagent" if self.native else "lane-direct-fallback",
            host_id=self.host_id,
            task_id=parent_id,
            dispatch_id=_stable_id("subagent-dispatch", work_unit_id, key),
            idempotency_key=key,
            payload=dict(envelope),
        )
        self._actions.append(action)
        receipt = HostReceipt(
            receipt_id=_stable_id("subagent-receipt", self.host_id, key),
            kind="subagent-receipt" if self.native else "lane-direct-fallback",
            source=self.source,
            host_id=self.host_id,
            task_id=parent_id,
            dispatch_id=action.dispatch_id,
            idempotency_key=key,
            status="active" if self.native else "direct-execution",
            thread_id=_stable_id("subagent-thread", self.host_id, key) if self.native else None,
            subagent_created=self.native,
        )
        self._receipts[key] = receipt
        return receipt

    def correct(self, correction: Correction, task_id: str, thread_id: str) -> HostReceipt:
        key = _stable_id("subagent-correction", task_id, correction.target, correction.issue)
        return HostReceipt(
            receipt_id=_stable_id("subagent-correction-receipt", self.host_id, key),
            kind="correction-receipt",
            source=self.source,
            host_id=self.host_id,
            task_id=task_id,
            dispatch_id=key,
            idempotency_key=key,
            status="accepted",
            thread_id=thread_id,
            subagent_created=self.native,
        )

    def request_promotion(self, request: PromotionRequest) -> dict[str, Any]:
        return {
            "kind": "promotion-request",
            "source": self.source,
            "request": request.to_dict(),
            "status": "requested",
        }
