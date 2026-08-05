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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from .artifacts import ArtifactStore, ArtifactStoreAPI
from .store import Store


RAW_KEYS = frozenset({
    "raw_logs", "raw_tool_log", "raw_tool_logs", "tool_logs", "stdout", "stderr",
    "transcript", "raw_transcript", "tool_output", "tool_outputs", "chain_of_thought",
    "hidden_reasoning", "internal_reasoning", "reasoning_trace", "scratchpad",
})
INTERNAL_META_KEY = "__context_meta__"
CAUSAL_PRIORITY = ("contract_ref", "accepted_decisions", "imports", "exports", "known_facts", "active_work", "blockers", "failed_assumptions", "file_index", "open_questions")

# A typed view is a projection, not a renamed copy of the materialized snapshot.
# Metadata added by ``_TypedView.to_dict`` is deliberately outside these content
# fields and cannot be supplied by snapshot content.
COMMON_VIEW_FIELDS = frozenset({
    "objective", "goal", "status", "summary", "accepted_decisions", "known_facts",
    "blockers", "open_questions", "exports", "artifact_refs", "pinned_artifacts",
})
VIEW_FIELDS: Mapping[str, frozenset[str]] = {
    "ConversationSnapshot": COMMON_VIEW_FIELDS | frozenset({"progress", "next_actions", "user_message"}),
    "CoordinatorSnapshot": COMMON_VIEW_FIELDS | frozenset({
        "contract_ref", "imports", "active_work", "failed_assumptions", "file_index",
        "lane_summaries", "task_summaries", "checks", "promotion_requests", "dependencies",
    }),
    "LaneSnapshot": COMMON_VIEW_FIELDS | frozenset({
        "task_id", "lane_id", "contract", "contract_ref", "imports", "active_work",
        "failed_assumptions", "file_index", "files", "checks", "ownership", "dependencies",
    }),
    "WorkUnitSlice": COMMON_VIEW_FIELDS | frozenset({
        "task_id", "lane_id", "work_unit_id", "parent_ref", "contract", "contract_ref",
        "imports", "active_work", "failed_assumptions", "file_index", "files", "checks",
        "ownership", "authority", "scope", "dependencies",
    }),
}

VIEW_ARTIFACT_VISIBILITY: Mapping[str, frozenset[str]] = {
    "ConversationSnapshot": frozenset({"user"}),
    "CoordinatorSnapshot": frozenset({"coordinator", "user"}),
    "LaneSnapshot": frozenset({"lane", "coordinator", "user"}),
    "WorkUnitSlice": frozenset({"local", "lane", "coordinator", "user"}),
}

_DROP = object()


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


def _normalized_key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_")


def _is_private_key(value: Any) -> bool:
    key = _normalized_key(value)
    return (
        key == INTERNAL_META_KEY
        or key in RAW_KEYS
        or key.endswith("_raw")
        or "transcript" in key
        or key.startswith("raw_log")
        or key.startswith("tool_log")
        or key.startswith("hidden_reasoning")
    )


def _input_refs(value: Any) -> tuple[str, ...]:
    """Extract exact causal identities without retaining raw payload text."""

    prefixes = ("artifact://", "snapshot://", "context://", "contract://", "decision://", "baseline://")
    found: list[str] = []
    if isinstance(value, str) and value.startswith(prefixes):
        found.append(_ref_value(value))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if not _is_private_key(key):
                found.extend(_input_refs(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.extend(_input_refs(item))
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
        if artifact_root is None and str(getattr(self.store, "path", ":memory:")) != ":memory:":
            artifact_root = self.store.path.parent / "artifacts"
        self.artifacts = ArtifactStore(self.store, root=artifact_root, resolver=resolver)
        self._replacements: dict[str, str] = {}
        self._materialized_cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self._projection_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._metrics: dict[str, int | float] = {
            "materializations": 0,
            "cache_hits": 0,
            "projection_cache_hits": 0,
            "materialization_seconds": 0.0,
            "max_snapshot_depth": 0,
            "raw_leakage_count": 0,
            "stale_rebuild_count": 0,
        }

    @property
    def connection(self) -> sqlite3.Connection:
        return self.store.connection

    def close(self) -> None:
        self.artifacts.close()
        if self._owns_store:
            self.store.close()

    def metrics(self) -> dict[str, int | float]:
        """Return process-local performance observations, never authority."""

        result = dict(self._metrics)
        materializations = int(result["materializations"])
        result["average_materialization_seconds"] = (
            float(result["materialization_seconds"]) / materializations
            if materializations else 0.0
        )
        result["hot_entries"] = len(self._materialized_cache)
        result["projection_entries"] = len(self._projection_cache)
        return result

    def _evict(self, refs: Iterable[str] = ()) -> None:
        targets = {_ref_value(ref) for ref in refs}
        if not targets:
            self._materialized_cache.clear()
            self._projection_cache.clear()
            return
        for ref in targets:
            self._materialized_cache.pop(ref, None)
        for key in tuple(self._projection_cache):
            if key[0] in targets:
                self._projection_cache.pop(key, None)

    @staticmethod
    def _storage_scope(scope: str) -> str:
        return {"lane": "task", "coordinator": "run", "conversation": "run"}.get(scope, scope)

    def _next_revision(self, scope: str, scope_id: str) -> int:
        row = self.connection.execute("SELECT COALESCE(MAX(revision), 0) AS revision FROM snapshots WHERE scope_type = ? AND scope_id = ?", (self._storage_scope(scope), scope_id)).fetchone()
        return int(row["revision"] if row is not None else 0) + 1

    def _make_ref(self, scope: str, scope_id: str, revision: int) -> str:
        return f"snapshot://{scope}/{scope_id}@{revision}"

    def _sanitize_value(self, value: Any, *, lane_id: str | None = None) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = str(raw_key)
                if _is_private_key(key) or _normalized_key(key) == "unrelated_lanes":
                    continue
                if key == "lane_summary" and lane_id is not None and isinstance(item, Mapping):
                    if item.get("lane_id") not in {None, lane_id}:
                        continue
                clean = self._sanitize_value(item, lane_id=lane_id)
                if clean is not _DROP:
                    result[key] = clean
            return result
        if isinstance(value, (list, tuple, set)):
            result = []
            for item in value:
                clean = self._sanitize_value(item, lane_id=lane_id)
                if clean is not _DROP:
                    result.append(clean)
            return result
        return copy.deepcopy(value)

    def _sanitize(self, content: Mapping[str, Any], *, lane_id: str | None = None) -> dict[str, Any]:
        clean = self._sanitize_value(content, lane_id=lane_id)
        return clean if isinstance(clean, dict) else {}

    def _artifact_visible(self, ref: str, view_name: str) -> bool:
        try:
            artifact = self.artifacts.get(ref)
        except Exception:
            # Unknown references are not evidence and fail closed at a typed boundary.
            return False
        return artifact.visibility in VIEW_ARTIFACT_VISIBILITY[view_name]

    def _filter_artifacts(self, value: Any, view_name: str) -> Any:
        if isinstance(value, str) and (value.startswith("artifact://") or value.startswith("sha256:")):
            return value if self._artifact_visible(value, view_name) else _DROP
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                filtered = self._filter_artifacts(item, view_name)
                if filtered is not _DROP:
                    result[str(key)] = filtered
            return result
        if isinstance(value, (list, tuple, set)):
            result = []
            for item in value:
                filtered = self._filter_artifacts(item, view_name)
                if filtered is not _DROP:
                    result.append(filtered)
            return result
        return copy.deepcopy(value)

    def _project(self, content: Mapping[str, Any], view_name: str) -> dict[str, Any]:
        allowed = VIEW_FIELDS[view_name]
        projected = {key: copy.deepcopy(value) for key, value in content.items() if key in allowed}
        filtered = self._filter_artifacts(projected, view_name)
        return self._ordered_content(filtered if isinstance(filtered, Mapping) else {})

    def _ordered_content(self, content: Mapping[str, Any]) -> dict[str, Any]:
        ordered: dict[str, Any] = {}
        for key in CAUSAL_PRIORITY:
            if key in content:
                ordered[key] = copy.deepcopy(content[key])
        for key, value in content.items():
            if key not in ordered and key not in RAW_KEYS:
                ordered[key] = copy.deepcopy(value)
        return ordered

    def _write(
        self,
        scope: str,
        scope_id: str,
        delta: Mapping[str, Any],
        *,
        base: str | None = None,
        validity: str = "current",
        reason: str | None = None,
        revision: int | None = None,
        inputs: Sequence[str] = (),
        replaces: str | None = None,
    ) -> SnapshotRecord:
        revision = revision or self._next_revision(scope, scope_id)
        ref = self._make_ref(scope, scope_id, revision)
        causal_inputs = tuple(dict.fromkeys([*map(_ref_value, inputs), *_input_refs(delta), *([_ref_value(base)] if base else [])]))
        source = {"scope": scope, "scope_id": scope_id, "base": base, "delta": delta, "artifacts": _artifact_refs(delta), "inputs": causal_inputs, "replaces": replaces}
        record = SnapshotRecord(ref, scope, scope_id, revision, base, copy.deepcopy(dict(delta)), _digest(source), _tokens(delta), validity, reason, _artifact_refs(delta), _now())
        stored_body = copy.deepcopy(dict(delta))
        stored_body[INTERNAL_META_KEY] = {"inputs": list(causal_inputs), "replaces": _ref_value(replaces) if replaces else None}
        self.connection.execute(
            "INSERT INTO snapshots (id, scope_type, scope_id, revision, base_snapshot_id, body_json, source_digest, token_estimate, validity, invalidation_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ref, self._storage_scope(scope), scope_id, revision, _ref_value(base) if base else None, _canonical(stored_body), record.source_digest, record.token_estimate, validity, reason, record.created_at),
        )
        replace_inputs = getattr(self.store, "replace_snapshot_inputs", None)
        if callable(replace_inputs):
            replace_inputs(ref, causal_inputs)
        if replaces:
            put_supersession = getattr(self.store, "put_snapshot_supersession", None)
            if callable(put_supersession):
                put_supersession(_ref_value(replaces), ref, reason or "snapshot replaced")
        for artifact_ref in record.artifact_refs:
            try:
                self.artifacts.link(artifact_ref, scope_type="snapshot", scope_id=ref, relation="context-source")
            except Exception:
                pass
        self._evict((ref,))
        return record

    def build_snapshot(self, scope: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.build(scope).to_dict()

    def build(self, scope: Mapping[str, Any] | str | None = None, *, scope_id: str | None = None, contract: Any = None, causal_refs: Sequence[Any] = (), inputs: Sequence[Any] = (), excluded: Sequence[str] = (), content: Mapping[str, Any] | None = None, **extra: Any) -> SnapshotRecord:
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
        return self._write(
            scope_name,
            actual_id,
            self._ordered_content(self._sanitize(content, lane_id=actual_id)),
            inputs=tuple(map(str, inputs)),
        )

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

    @staticmethod
    def _metadata(body: Mapping[str, Any]) -> Mapping[str, Any]:
        value = body.get(INTERNAL_META_KEY, {})
        return value if isinstance(value, Mapping) else {}

    def _resolve_replacement(self, snapshot_ref: str) -> str:
        ref = _ref_value(snapshot_ref)
        seen: set[str] = set()
        resolver = getattr(self.store, "resolve_snapshot_ref", None)
        while ref not in seen:
            seen.add(ref)
            resolved: Any = resolver(ref) if callable(resolver) else None
            if isinstance(resolved, Mapping):
                resolved = resolved.get("replacement_snapshot_id") or resolved.get("snapshot_id") or resolved.get("id")
            if resolved and _ref_value(str(resolved)) != ref:
                ref = _ref_value(str(resolved))
                continue
            memory = self._replacements.get(ref)
            if memory and memory != ref:
                ref = memory
                continue
            replacement: str | None = None
            # Current stores own an indexed supersession table.  Scanning all
            # snapshot JSON is retained only for pre-v2 compatibility stores.
            rows = [] if callable(resolver) else self.connection.execute(
                "SELECT id, body_json FROM snapshots ORDER BY created_at, id"
            ).fetchall()
            for row in rows:
                body = json.loads(row["body_json"])
                if self._metadata(body).get("replaces") == ref:
                    replacement = str(row["id"])
            if replacement and replacement != ref:
                ref = replacement
                continue
            break
        return ref

    def snapshot(self, snapshot_ref: str) -> SnapshotRecord:
        ref = self._resolve_replacement(snapshot_ref)
        row = self.connection.execute("SELECT * FROM snapshots WHERE id = ?", (_db_id(ref),)).fetchone()
        if row is None:
            row = self.connection.execute("SELECT * FROM snapshots WHERE id = ?", (ref,)).fetchone()
        if row is None:
            raise KeyError(snapshot_ref)
        stored_scope = str(row["scope_type"])
        ref_scope = ref.removeprefix("snapshot://").split("/", 1)[0]
        scope = ref_scope if ref_scope in {"lane", "coordinator", "conversation", "work_unit", "task", "run"} else stored_scope
        body = json.loads(row["body_json"])
        body.pop(INTERNAL_META_KEY, None)
        return SnapshotRecord(ref, scope, row["scope_id"], int(row["revision"]), (str(row["base_snapshot_id"]) if row["base_snapshot_id"] else None), body, row["source_digest"], int(row["token_estimate"]), row["validity"], row["invalidation_reason"], _artifact_refs(body), row["created_at"])

    @staticmethod
    def _coerce_ref(value: str | Mapping[str, Any] | SnapshotRecord) -> str:
        if isinstance(value, SnapshotRecord):
            return value.snapshot_ref
        if isinstance(value, Mapping):
            return str(value.get("snapshot_ref") or value.get("id"))
        return str(value)

    def reconstruct_content(self, snapshot_ref: str) -> dict[str, Any]:
        started = time.perf_counter()
        ref = self._resolve_replacement(snapshot_ref)
        row = self.connection.execute(
            "SELECT source_digest FROM snapshots WHERE id = ?", (ref,)
        ).fetchone()
        if row is None:
            raise KeyError(snapshot_ref)
        digest = str(row["source_digest"])
        cached = self._materialized_cache.get(ref)
        if cached is not None and cached[0] == digest:
            self._metrics["cache_hits"] = int(self._metrics["cache_hits"]) + 1
            return copy.deepcopy(cached[1])

        # One recursive query loads the complete COW chain.  The former
        # recursive Python implementation performed a snapshot query and
        # replacement lookup for every depth.
        rows = self.connection.execute(
            """WITH RECURSIVE chain(id, base_snapshot_id, body_json, depth) AS (
                 SELECT id, base_snapshot_id, body_json, 0 FROM snapshots WHERE id = ?
                 UNION ALL
                 SELECT s.id, s.base_snapshot_id, s.body_json, chain.depth + 1
                 FROM snapshots s JOIN chain ON s.id = chain.base_snapshot_id
               ) SELECT id, body_json, depth FROM chain ORDER BY depth DESC""",
            (ref,),
        ).fetchall()
        if not rows:
            raise KeyError(snapshot_ref)
        content: dict[str, Any] = {}
        for item in rows:
            body = json.loads(item["body_json"])
            body.pop(INTERNAL_META_KEY, None)
            content = _deep_merge(content, body)
        content = self._ordered_content(self._sanitize(content))
        leakage = sum(1 for key in content if _is_private_key(key))
        self._metrics["raw_leakage_count"] = int(self._metrics["raw_leakage_count"]) + leakage
        self._metrics["materializations"] = int(self._metrics["materializations"]) + 1
        self._metrics["max_snapshot_depth"] = max(int(self._metrics["max_snapshot_depth"]), len(rows))
        self._metrics["materialization_seconds"] = float(self._metrics["materialization_seconds"]) + (time.perf_counter() - started)
        self._materialized_cache[ref] = (digest, copy.deepcopy(content))
        return content

    def reconstruct_snapshot(self, snapshot_ref: str | Mapping[str, Any] | SnapshotRecord, **kwargs: Any) -> Mapping[str, Any]:
        return self.reconstruct(snapshot_ref, **kwargs)

    def reconstruct(self, snapshot_ref: str | Mapping[str, Any] | SnapshotRecord, **kwargs: Any) -> Mapping[str, Any]:
        if isinstance(snapshot_ref, Mapping) and not ("id" in snapshot_ref or "snapshot_ref" in snapshot_ref):
            body = copy.deepcopy(dict(snapshot_ref))
            return {"content": body, "source_digest": _digest(body), **body}
        record = self.snapshot(self._coerce_ref(snapshot_ref))
        content = self.reconstruct_content(record.snapshot_ref)
        if record.validity in {"stale", "invalid"}:
            replacement = self._write(
                record.scope,
                record.scope_id,
                content,
                validity="current",
                reason="invalidated snapshot reconstructed",
                inputs=_input_refs(content),
                replaces=record.snapshot_ref,
            )
            self._replacements[record.snapshot_ref] = replacement.snapshot_ref
            self._metrics["stale_rebuild_count"] = int(self._metrics["stale_rebuild_count"]) + 1
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
        cache_key = (record.snapshot_ref, cls.view_name, record.source_digest)
        projected = self._projection_cache.get(cache_key)
        if projected is None:
            projected = self._project(content, cls.view_name)
            self._projection_cache[cache_key] = copy.deepcopy(projected)
        else:
            self._metrics["projection_cache_hits"] = int(self._metrics["projection_cache_hits"]) + 1
        return cls(record, projected)

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
        return self._write(
            record.scope,
            record.scope_id,
            content,
            base=None,
            validity="current",
            reason="context compacted",
            inputs=_input_refs(content),
            replaces=record.snapshot_ref,
        )

    def invalidate(self, change: Mapping[str, Any] | str | SnapshotRecord, *, reason: str | None = None) -> ContextInvalidation | SnapshotRecord:
        if isinstance(change, SnapshotRecord) or (isinstance(change, Mapping) and ("id" in change or "snapshot_ref" in change)):
            record = self.snapshot(self._coerce_ref(change))
            invalid_reason = reason or "context invalidated"
            self.connection.execute("UPDATE snapshots SET validity = ?, invalidation_reason = ? WHERE id = ?", ("invalid", invalid_reason, record.snapshot_ref))
            self._evict((record.snapshot_ref,))
            return self.snapshot(record.snapshot_ref)
        data = {"change": change, "reason": reason} if isinstance(change, str) else dict(change)
        marker = str(data.get("contract_ref") or data.get("target") or data.get("artifact_ref") or data.get("ownership") or data.get("decision_ref") or data.get("baseline") or data.get("reason") or "context-change")
        event_identity = str(data.get("delta_id") or data.get("signal_id") or marker)
        rows = self.connection.execute("SELECT * FROM snapshots WHERE validity = 'current'").fetchall()
        inputs_by_snapshot: dict[str, set[str]] = {}
        bodies: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            ref = str(row["id"])
            body = json.loads(row["body_json"])
            bodies[ref] = body
            metadata = self._metadata(body)
            inputs_by_snapshot[ref] = set(map(str, metadata.get("inputs", ())))

        list_dependents = getattr(self.store, "list_snapshot_dependents", None)
        invalidated_set: set[str] = set()
        if callable(list_dependents):
            for dependent in list_dependents((marker,), transitive=True):
                if isinstance(dependent, Mapping):
                    dependent = dependent.get("snapshot_id") or dependent.get("id")
                if dependent:
                    invalidated_set.add(str(dependent))

        # Compatibility for a v1 database: use persisted exact input identities,
        # then traverse snapshot-to-snapshot edges.  Old rows without metadata use
        # exact structured equality only; there is no substring scan of JSON text.
        def contains_exact(value: Any, expected: str) -> bool:
            if isinstance(value, str):
                return value == expected or value.startswith(expected + "@")
            if isinstance(value, Mapping):
                return any(contains_exact(item, expected) for key, item in value.items() if key != INTERNAL_META_KEY)
            if isinstance(value, (list, tuple, set)):
                return any(contains_exact(item, expected) for item in value)
            return False

        scope_ids = {str(row["id"]): str(row["scope_id"]) for row in rows}
        for ref, body in bodies.items():
            input_matches = any(item == marker or item.startswith(marker + "@") for item in inputs_by_snapshot[ref])
            scope_matches = data.get("scope_id") is not None and str(data.get("scope_id")) == scope_ids[ref]
            if input_matches or contains_exact(body, marker) or scope_matches:
                invalidated_set.add(ref)
        changed = True
        while changed:
            changed = False
            for ref, inputs in inputs_by_snapshot.items():
                if ref not in invalidated_set and inputs.intersection(invalidated_set):
                    invalidated_set.add(ref)
                    changed = True

        invalidated = [str(row["id"]) for row in rows if str(row["id"]) in invalidated_set]
        for ref in invalidated:
            self.connection.execute("UPDATE snapshots SET validity = ?, invalidation_reason = ? WHERE id = ?", ("stale", event_identity, ref))
        self._evict(invalidated)
        event = ContextInvalidation(_digest(data), event_identity, str(data.get("reason") or f"context invalidated by {marker}"), tuple(invalidated))
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
