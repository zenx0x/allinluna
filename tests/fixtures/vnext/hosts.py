"""Deterministic fake host adapters for vNext integration tests.

The fakes record the action sent to them and return test-host receipts.  They never claim
to be a receipt from the current Codex App, which keeps provenance assertions meaningful.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
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
        parent_scope = tuple(envelope.get("parent_scope", ()))
        child_scope = tuple(envelope.get("scope", ()))
        parent_ownership = tuple(envelope.get("parent_ownership", ()))
        child_ownership = tuple(envelope.get("ownership", ()))
        parent_authority = tuple(envelope.get("parent_authority", ()))
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
