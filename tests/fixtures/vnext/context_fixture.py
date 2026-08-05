"""Small context fixture modelling base+delta, invalidation, and reconstruction."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .contracts import ContractDelta, ContextInvalidation


@dataclass(frozen=True)
class ContextSnapshot:
    snapshot_ref: str
    base_snapshot_ref: str | None
    delta: dict[str, Any]
    source_digest: str
    validity: str = "current"


class ContextFixture:
    """Test-only context store; raw logs remain addressable but outside upper views."""

    def __init__(self) -> None:
        self._snapshots: dict[str, ContextSnapshot] = {}
        self._invalidation_events: list[ContextInvalidation] = []

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def create_base(self, snapshot_ref: str, content: dict[str, Any]) -> ContextSnapshot:
        snapshot = ContextSnapshot(
            snapshot_ref=snapshot_ref,
            base_snapshot_ref=None,
            delta=copy.deepcopy(content),
            source_digest=self._digest(content),
        )
        self._snapshots[snapshot_ref] = snapshot
        return snapshot

    def build_child(
        self, snapshot_ref: str, base_snapshot_ref: str, delta: dict[str, Any]
    ) -> ContextSnapshot:
        if base_snapshot_ref not in self._snapshots:
            raise KeyError(base_snapshot_ref)
        snapshot = ContextSnapshot(
            snapshot_ref=snapshot_ref,
            base_snapshot_ref=base_snapshot_ref,
            delta=copy.deepcopy(delta),
            source_digest=self._digest({"base": base_snapshot_ref, "delta": delta}),
        )
        self._snapshots[snapshot_ref] = snapshot
        return snapshot

    def reconstruct(self, snapshot_ref: str) -> dict[str, Any]:
        snapshot = self._snapshots[snapshot_ref]
        result = (
            self.reconstruct(snapshot.base_snapshot_ref)
            if snapshot.base_snapshot_ref is not None
            else {}
        )
        result.update(copy.deepcopy(snapshot.delta))
        result.pop("raw_logs", None)
        return result

    def invalidate_from_contract_delta(self, delta: ContractDelta) -> ContextInvalidation:
        dependent = tuple(
            snapshot.snapshot_ref
            for snapshot in self._snapshots.values()
            if delta.target in json.dumps(snapshot.delta, sort_keys=True)
        )
        event = ContextInvalidation(
            snapshot_ref=dependent[0] if dependent else "snapshot://unknown",
            reason=f"contract revision {delta.previous_revision}->{delta.next_revision}",
            invalidated_by=delta.delta_id or delta.target,
            dependent_refs=dependent,
            invalidation_id=self._digest(delta.to_dict()),
        )
        self._invalidation_events.append(event)
        for ref in dependent:
            old = self._snapshots[ref]
            self._snapshots[ref] = ContextSnapshot(
                snapshot_ref=old.snapshot_ref,
                base_snapshot_ref=old.base_snapshot_ref,
                delta=old.delta,
                source_digest=old.source_digest,
                validity="stale",
            )
        return event

    def snapshot(self, snapshot_ref: str) -> ContextSnapshot:
        return self._snapshots[snapshot_ref]

    @property
    def invalidation_events(self) -> tuple[ContextInvalidation, ...]:
        return tuple(self._invalidation_events)
