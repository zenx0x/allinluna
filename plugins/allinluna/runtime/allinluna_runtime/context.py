"""The single Context Kernel and its four typed activity views.

Snapshots are replaceable COW views over T1 facts.  Only a base snapshot plus a
delta is persisted; reconstruction follows that chain, removes raw operational
noise, and exposes causal fields in contract/decision/export order.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from .artifacts import ArtifactStore, ArtifactStoreAPI
from .store import Store


RAW_KEYS = frozenset({"raw_logs", "tool_logs", "stdout", "stderr", "transcript", "raw_transcript", "tool_output"})
CAUSAL_PRIORITY = ("contract_ref", "accepted_decisions", "imports", "exports", "known_facts", "active_work", "blockers", "failed_assumptions", "file_index", "open_questions")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _tokens(value: Any) -> int:
    return max(0, (len(_canonical(value)) + 3) // 4)


def _ref_value(ref: str) -> str:
    text = str(ref)
    if text.startswith("context://"):
        text = "snapshot://" + text.removeprefix("context://")
    return text


def _db_id(ref: str) -> str:
    return _ref_value(ref).removeprefix("snapshot://")


def _artifact_refs(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, str) and (value.startswith("artifact://") or value.startswith("sha256:")):
        found.append(value if value.startswith("artifact://") else f"artifact://{value}")
    elif isinstance(value, Mapping):
        for item in value.values():
            found.extend(_artifact_refs(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.extend(_artifact_refs(item))
    return tuple(dict.fromkeys(found))


def _deep_merge(base: Mapping[str, Any], delta: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in delta.items():
        if value is None and key in result:
            result.pop(key, None)
        elif isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


@dataclass(frozen=True)
class SnapshotRecord(Mapping[str, Any]):
    snapshot_ref: str
    scope: str
    scope_id: str
    revision: int
    base_snapshot_ref: str | None
    delta: Mapping[str, Any]
    source_digest: str
    token_estimate: int
    validity: str = "current"
    invalidation_reason: str | None = None
    artifact_refs: tuple[str, ...] = ()
    created_at: str | None = None

    @property
    def id(self) -> str:
        return self.snapshot_ref

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.snapshot_ref, "snapshot_ref": self.snapshot_ref, "scope": self.scope,
            "scope_id": self.scope_id, "revision": self.revision,
            "base_snapshot_ref": self.base_snapshot_ref, "delta": copy.deepcopy(dict(self.delta)),
            "context_delta": copy.deepcopy(dict(self.delta)),
            "source_digest": self.source_digest, "token_estimate": self.token_estimate,
            "validity": self.validity, "invalidation_reason": self.invalidation_reason,
            "artifact_refs": list(self.artifact_refs), "created_at": self.created_at,
        }
        result.update(copy.deepcopy(dict(self.delta)))
        return result

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class _TypedView(Mapping[str, Any]):
    view_name = "ContextView"

    def __init__(self, snapshot: SnapshotRecord, content: Mapping[str, Any]) -> None:
        self.snapshot = snapshot
        self.content = copy.deepcopy(dict(content))

    def __getitem__(self, key: str) -> Any:
        return self.content[key]

    def __iter__(self):
        return iter(self.content)

    def __len__(self) -> int:
        return len(self.content)

    @property
    def snapshot_ref(self) -> str:
        return self.snapshot.snapshot_ref

    def to_dict(self) -> dict[str, Any]:
        result = copy.deepcopy(self.content)
        result.update({"snapshot_ref": self.snapshot_ref, "view": self.view_name, "source_digest": self.snapshot.source_digest})
        return result


class ConversationSnapshot(_TypedView):
    view_name = "ConversationSnapshot"


class CoordinatorSnapshot(_TypedView):
    view_name = "CoordinatorSnapshot"


class LaneSnapshot(_TypedView):
    view_name = "LaneSnapshot"


class WorkUnitSlice(_TypedView):
    view_name = "WorkUnitSlice"


@dataclass(frozen=True)
class ContextBundle(Mapping[str, Any]):
    id: str
    scope: str
    objective: Any = None
    contract_ref: Any = None
    imports: tuple[Any, ...] = ()
    accepted_decisions: tuple[Any, ...] = ()
    known_facts: tuple[Any, ...] = ()
    active_work: tuple[Any, ...] = ()
    blockers: tuple[Any, ...] = ()
    failed_assumptions: tuple[Any, ...] = ()
    open_questions: tuple[Any, ...] = ()
    file_index: tuple[Any, ...] = ()
    exports: tuple[Any, ...] = ()
    pinned_artifacts: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ("raw_tool_logs", "unrelated_lane_transcripts")
    source_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {name: list(value) if isinstance(value, tuple) else value for name, value in self.__dict__.items()}

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


@dataclass(frozen=True)
class ContextInvalidation(Mapping[str, Any]):
    invalidation_id: str
    invalidated_by: str
    reason: str
    dependent_refs: tuple[str, ...]
    replacement_required: bool = True

    @property
    def invalidated_refs(self) -> tuple[str, ...]:
        return self.dependent_refs

    def to_dict(self) -> dict[str, Any]:
        return {"invalidation_id": self.invalidation_id, "invalidated_by": self.invalidated_by, "reason": self.reason, "dependent_refs": list(self.dependent_refs), "invalidated_refs": list(self.dependent_refs), "replacement_required": self.replacement_required}

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class ContextKernel:
    """One source-backed kernel with view, COW, recovery, and invalidation APIs."""

    def __init__(self, path: str | Path | sqlite3.Connection | Store, *, artifact_root: str | Path | None = None, resolver: Any = None) -> None:
        self._owns_store = not isinstance(path, Store)
        self.store = path if isinstance(path, Store) else Store(path)
        self.artifacts = ArtifactStore(self.store, root=artifact_root, resolver=resolver)
        self._replacements: dict[str, str] = {}

    @property
    def connection(self) -> sqlite3.Connection:
        return self.store.connection

    def close(self) -> None:
        self.artifacts.close()
        if self._owns_store:
            self.store.close()

    @staticmethod
    def _storage_scope(scope: str) -> str:
        return {"lane": "task", "coordinator": "run", "conversation": "run"}.get(scope, scope)

    def _next_revision(self, scope: str, scope_id: str) -> int:
        row = self.connection.execute("SELECT COALESCE(MAX(revision), 0) AS revision FROM snapshots WHERE scope_type = ? AND scope_id = ?", (self._storage_scope(scope), scope_id)).fetchone()
        return int(row["revision"] if row is not None else 0) + 1

    def _make_ref(self, scope: str, scope_id: str, revision: int) -> str:
        return f"snapshot://{scope}/{scope_id}@{revision}"

    def _sanitize(self, content: Mapping[str, Any], *, lane_id: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in content.items():
            if key in RAW_KEYS or key.endswith("_raw"):
                continue
            if key in {"unrelated_lane_transcripts", "unrelated_lanes", "raw_logs"}:
                continue
            if key in {"lane_transcript", "lane_summary"} and lane_id is not None:
                if isinstance(value, Mapping) and value.get("lane_id") not in {None, lane_id}:
                    continue
            result[str(key)] = copy.deepcopy(value)
        return result

    def _ordered_content(self, content: Mapping[str, Any]) -> dict[str, Any]:
        ordered: dict[str, Any] = {}
        for key in CAUSAL_PRIORITY:
            if key in content:
                ordered[key] = copy.deepcopy(content[key])
        for key, value in content.items():
            if key not in ordered and key not in RAW_KEYS:
                ordered[key] = copy.deepcopy(value)
        return ordered

    def _write(self, scope: str, scope_id: str, delta: Mapping[str, Any], *, base: str | None = None, validity: str = "current", reason: str | None = None, revision: int | None = None) -> SnapshotRecord:
        revision = revision or self._next_revision(scope, scope_id)
        ref = self._make_ref(scope, scope_id, revision)
        source = {"scope": scope, "scope_id": scope_id, "base": base, "delta": delta, "artifacts": _artifact_refs(delta)}
        record = SnapshotRecord(ref, scope, scope_id, revision, base, copy.deepcopy(dict(delta)), _digest(source), _tokens(delta), validity, reason, _artifact_refs(delta), _now())
        self.connection.execute(
            "INSERT INTO snapshots (id, scope_type, scope_id, revision, base_snapshot_id, body_json, source_digest, token_estimate, validity, invalidation_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ref, self._storage_scope(scope), scope_id, revision, _ref_value(base) if base else None, _canonical(delta), record.source_digest, record.token_estimate, validity, reason, record.created_at),
        )
        for artifact_ref in record.artifact_refs:
            try:
                self.artifacts.link(artifact_ref, scope_type="snapshot", scope_id=ref, relation="context-source")
            except Exception:
                pass
        return record

    def build_snapshot(self, scope: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.build(scope).to_dict()

    def build(self, scope: Mapping[str, Any] | str | None = None, *, scope_id: str | None = None, contract: Any = None, causal_refs: Sequence[Any] = (), excluded: Sequence[str] = (), content: Mapping[str, Any] | None = None, **extra: Any) -> SnapshotRecord:
        if isinstance(scope, Mapping):
            values = dict(scope)
            scope_name = str(values.pop("scope", values.pop("scope_type", "task")))
            actual_id = str(values.pop("scope_id", values.pop("id", scope_id or "context")))
            supplied = values.pop("content", values)
        else:
            scope_name = str(scope or "task")
            actual_id = str(scope_id or "context")
            supplied = dict(content or {})
            supplied.update(extra)
            if contract is not None:
                supplied["contract"] = copy.deepcopy(contract)
            if causal_refs:
                supplied["imports"] = list(causal_refs)
            if excluded:
                supplied["excluded"] = list(excluded)
        content = supplied
        if not isinstance(content, Mapping):
            raise TypeError("context content must be a mapping")
        return self._write(scope_name, actual_id, self._ordered_content(self._sanitize(content, lane_id=actual_id)))

    def derive(self, base_snapshot_ref: str | Mapping[str, Any] | SnapshotRecord, delta: Mapping[str, Any], *, scope: str | None = None, scope_id: str | None = None) -> SnapshotRecord:
        base = self.snapshot(self._coerce_ref(base_snapshot_ref))
        target_scope, target_id = scope or base.scope, scope_id or base.scope_id
        clean = self._ordered_content(self._sanitize(delta, lane_id=target_id))
        return self._write(target_scope, target_id, clean, base=base.snapshot_ref)

    def promote(self, snapshot_ref: str | Mapping[str, Any] | SnapshotRecord, *, scope: str = "task", scope_id: str | None = None, requested_ownership: Sequence[str] = (), reason: str = "independent deliverable", user_decision_required: bool = False) -> Mapping[str, Any]:
        """Create an explicit promotion request seeded by a valid local snapshot."""

        record = self.snapshot(self._coerce_ref(snapshot_ref))
        promoted = self._write(scope, scope_id or record.scope_id, self.reconstruct_content(record.snapshot_ref), base=record.snapshot_ref)
        return {"request_id": _digest({"from": record.snapshot_ref, "to": promoted.snapshot_ref, "reason": reason}), "from_snapshot_ref": record.snapshot_ref, "promoted_snapshot_ref": promoted.snapshot_ref, "requested_ownership": list(requested_ownership), "reason": reason, "user_decision_required": user_decision_required, "context_seed_refs": [record.snapshot_ref, *record.artifact_refs]}

    def snapshot(self, snapshot_ref: str) -> SnapshotRecord:
        ref = self._replacements.get(_ref_value(snapshot_ref), _ref_value(snapshot_ref))
        row = self.connection.execute("SELECT * FROM snapshots WHERE id = ?", (_db_id(ref),)).fetchone()
        if row is None:
            row = self.connection.execute("SELECT * FROM snapshots WHERE id = ?", (ref,)).fetchone()
        if row is None:
            raise KeyError(snapshot_ref)
        stored_scope = str(row["scope_type"])
        ref_scope = ref.removeprefix("snapshot://").split("/", 1)[0]
        scope = ref_scope if ref_scope in {"lane", "coordinator", "conversation", "work_unit", "task", "run"} else stored_scope
        body = json.loads(row["body_json"])
        return SnapshotRecord(ref, scope, row["scope_id"], int(row["revision"]), (str(row["base_snapshot_id"]) if row["base_snapshot_id"] else None), body, row["source_digest"], int(row["token_estimate"]), row["validity"], row["invalidation_reason"], _artifact_refs(body), row["created_at"])

    @staticmethod
    def _coerce_ref(value: str | Mapping[str, Any] | SnapshotRecord) -> str:
        if isinstance(value, SnapshotRecord):
            return value.snapshot_ref
        if isinstance(value, Mapping):
            return str(value.get("snapshot_ref") or value.get("id"))
        return str(value)

    def reconstruct_content(self, snapshot_ref: str) -> dict[str, Any]:
        record = self.snapshot(snapshot_ref)
        if record.base_snapshot_ref:
            content = _deep_merge(self.reconstruct_content(record.base_snapshot_ref), record.delta)
        else:
            content = copy.deepcopy(dict(record.delta))
        return self._ordered_content(self._sanitize(content))

    def reconstruct_snapshot(self, snapshot_ref: str | Mapping[str, Any] | SnapshotRecord, **kwargs: Any) -> Mapping[str, Any]:
        return self.reconstruct(snapshot_ref, **kwargs)

    def reconstruct(self, snapshot_ref: str | Mapping[str, Any] | SnapshotRecord, **kwargs: Any) -> Mapping[str, Any]:
        if isinstance(snapshot_ref, Mapping) and not ("id" in snapshot_ref or "snapshot_ref" in snapshot_ref):
            body = copy.deepcopy(dict(snapshot_ref))
            return {"content": body, "source_digest": _digest(body), **body}
        record = self.snapshot(self._coerce_ref(snapshot_ref))
        content = self.reconstruct_content(record.snapshot_ref)
        if record.validity in {"stale", "invalid"}:
            replacement = self._write(record.scope, record.scope_id, content, validity="current")
            self._replacements[record.snapshot_ref] = replacement.snapshot_ref
            content = self.reconstruct_content(replacement.snapshot_ref)
            record = replacement
        return record

    def view(self, snapshot_ref: str | Mapping[str, Any], view: str | type[_TypedView] = "lane", *, kind: str | None = None) -> _TypedView:
        if isinstance(snapshot_ref, Mapping):
            record = self.build(snapshot_ref)
        else:
            record = self.snapshot(snapshot_ref)
        content = self.reconstruct_content(record.snapshot_ref)
        name = kind or (view if isinstance(view, str) else view.__name__)
        cls = {"conversation": ConversationSnapshot, "ConversationSnapshot": ConversationSnapshot, "coordinator": CoordinatorSnapshot, "CoordinatorSnapshot": CoordinatorSnapshot, "lane": LaneSnapshot, "LaneSnapshot": LaneSnapshot, "work_unit": WorkUnitSlice, "WorkUnitSlice": WorkUnitSlice}.get(name, LaneSnapshot)
        return cls(record, content)

    def bundle(self, snapshot_ref: str) -> ContextBundle:
        content = self.reconstruct_content(snapshot_ref)
        record = self.snapshot(snapshot_ref)
        values = {key: content.get(key, ()) for key in CAUSAL_PRIORITY}
        return ContextBundle(id=record.snapshot_ref, scope=record.scope, objective=content.get("objective"), contract_ref=content.get("contract_ref"), imports=tuple(values["imports"] or ()), accepted_decisions=tuple(values["accepted_decisions"] or ()), known_facts=tuple(values["known_facts"] or ()), active_work=tuple(values["active_work"] or ()), blockers=tuple(values["blockers"] or ()), failed_assumptions=tuple(values["failed_assumptions"] or ()), open_questions=tuple(values["open_questions"] or ()), file_index=tuple(values["file_index"] or ()), exports=tuple(values["exports"] or ()), pinned_artifacts=record.artifact_refs, source_digest=record.source_digest)

    def compact(self, snapshot_ref: str | Mapping[str, Any] | SnapshotRecord, *, preserve: Sequence[str] = ()) -> SnapshotRecord:
        record = self.snapshot(self._coerce_ref(snapshot_ref))
        content = self.reconstruct_content(record.snapshot_ref)
        if preserve:
            keep = set(preserve)
            content = {key: value for key, value in content.items() if key in keep or key in {"contract", "contract_ref", "exports", "blockers", "accepted_decisions"}}
        return self._write(record.scope, record.scope_id, content, base=record.snapshot_ref, validity="current")

    def invalidate(self, change: Mapping[str, Any] | str | SnapshotRecord, *, reason: str | None = None) -> ContextInvalidation | SnapshotRecord:
        if isinstance(change, SnapshotRecord) or (isinstance(change, Mapping) and ("id" in change or "snapshot_ref" in change)):
            record = self.snapshot(self._coerce_ref(change))
            invalid_reason = reason or "context invalidated"
            self.connection.execute("UPDATE snapshots SET validity = ?, invalidation_reason = ? WHERE id = ?", ("invalid", invalid_reason, record.snapshot_ref))
            return self.snapshot(record.snapshot_ref)
        data = {"change": change, "reason": reason} if isinstance(change, str) else dict(change)
        marker = str(data.get("contract_ref") or data.get("target") or data.get("artifact_ref") or data.get("ownership") or data.get("decision_ref") or data.get("baseline") or data.get("reason") or "context-change")
        invalidated: list[str] = []
        rows = self.connection.execute("SELECT * FROM snapshots WHERE validity = 'current'").fetchall()
        for row in rows:
            ref = str(row["id"])
            content = json.loads(row["body_json"])
            scope_matches = data.get("scope_id") is not None and data.get("scope_id") == row["scope_id"]
            if marker in _canonical(content) or scope_matches:
                self.connection.execute("UPDATE snapshots SET validity = ?, invalidation_reason = ? WHERE id = ?", ("stale", marker, ref))
                invalidated.append(ref)
        event = ContextInvalidation(_digest(data), marker, str(data.get("reason") or f"context invalidated by {marker}"), tuple(invalidated))
        return event

    def invalidate_from_contract_delta(self, delta: Mapping[str, Any]) -> Mapping[str, Any]:
        data = dict(delta)
        target = str(data.get("target") or data.get("contract_ref") or "contract-change")
        data.setdefault("contract_ref", target)
        return self.invalidate(data)

    def invalidate_from_ownership_change(self, change: Mapping[str, Any] | str) -> ContextInvalidation | SnapshotRecord:
        return self.invalidate({"ownership": change, "reason": "ownership changed"})

    def invalidate_from_baseline_change(self, change: Mapping[str, Any] | str) -> ContextInvalidation | SnapshotRecord:
        return self.invalidate({"baseline": change, "reason": "baseline changed"})

    def invalidate_from_decision_change(self, change: Mapping[str, Any] | str) -> ContextInvalidation | SnapshotRecord:
        return self.invalidate({"decision_ref": change, "reason": "accepted decision changed"})

    def invalidate_from_artifact(self, artifact_ref: str) -> ContextInvalidation | SnapshotRecord:
        return self.invalidate({"artifact_ref": artifact_ref, "reason": "imported artifact changed"})

    def invalidate_from_signal(self, signal: Mapping[str, Any]) -> ContextInvalidation | SnapshotRecord:
        return self.invalidate({"signal": dict(signal), "reason": "source signal changed"})

    def trace_artifact(self, snapshot_ref: str | Mapping[str, Any] | SnapshotRecord, artifact_ref: str | None = None) -> Mapping[str, Any]:
        record = self.snapshot(self._coerce_ref(snapshot_ref))
        refs = (artifact_ref,) if artifact_ref else record.artifact_refs
        traces: list[dict[str, Any]] = []
        for ref in refs:
            try:
                artifact = self.artifacts.get(ref)
            except Exception:
                continue
            traces.append({"artifact": artifact.to_dict(), "links": [link.__dict__ for link in self.artifacts.links(ref)], "snapshot_ref": record.snapshot_ref})
        return {"snapshot_ref": record.snapshot_ref, "snapshot_id": record.snapshot_ref, "artifact_ref": artifact_ref, "artifacts": traces}

    def trace(self, snapshot_ref: str) -> Mapping[str, Any]:
        return {"snapshot": self.snapshot(snapshot_ref).to_dict(), "artifact_trace": self.trace_artifact(snapshot_ref)}


ContextKernelAPI = ContextKernel
SnapshotReconstructionAPI = ContextKernel
ContextInvalidationAPI = ContextKernel

__all__ = [
    "SnapshotRecord", "ConversationSnapshot", "CoordinatorSnapshot", "LaneSnapshot", "WorkUnitSlice", "ContextBundle", "ContextInvalidation", "ContextKernel", "ContextKernelAPI", "SnapshotReconstructionAPI", "ContextInvalidationAPI", "ArtifactStoreAPI",
]
