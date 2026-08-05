"""Immutable, content-addressed artifacts for the vNext context kernel.

The store is deliberately a small adapter over the T1 SQLite authority.  Artifact
rows are immutable facts; links are append-only relationships used to make an
artifact traceable from a run, task, work unit, or snapshot.  Payload bytes may be
local, external, or lazy: the row records the digest and URI while ``resolve``
optionally obtains and verifies the bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlparse

try:
    from .domain import Artifact, ArtifactKind, ArtifactVisibility
except ImportError:  # pragma: no cover - direct module loading during assembly
    Artifact = None  # type: ignore[assignment,misc]
    ArtifactKind = ArtifactVisibility = None  # type: ignore[assignment]


class ArtifactError(RuntimeError):
    """Base error for artifact operations."""


class ArtifactNotFoundError(ArtifactError, KeyError):
    """The requested content-addressed artifact does not exist."""


class ArtifactIntegrityError(ArtifactError):
    """Resolved content does not match its immutable digest."""


class ArtifactImmutableError(ArtifactError):
    """An existing artifact identity was presented with different metadata."""


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    kind: str
    uri: str
    sha256: str
    produced_by: str | None = None
    source_refs: tuple[str, ...] = ()
    visibility: str = "local"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = None

    @property
    def ref(self) -> str:
        return self.id if self.id.startswith("artifact://") else f"artifact://{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "uri": self.uri,
            "sha256": self.sha256,
            "produced_by": self.produced_by,
            "source_refs": list(self.source_refs),
            "visibility": self.visibility,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ArtifactLink:
    artifact_ref: str
    scope_type: str
    scope_id: str
    relation: str
    created_at: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _artifact_id(ref: str) -> str:
    text = str(ref)
    return text.removeprefix("artifact://")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class ArtifactStore:
    """Content-addressed artifact API compatible with the T1 SQLite schema.

    ``db`` accepts a T1 ``Store``, a sqlite connection, a database path, or an
    object exposing ``connection`` and ``transaction``.  The optional ``resolver``
    is called only for external/lazy URIs and must return bytes.
    """

    def __init__(
        self,
        db: Any,
        *,
        root: str | Path | None = None,
        resolver: Callable[[str], bytes] | None = None,
    ) -> None:
        self._owner = False
        self._store = db if hasattr(db, "connection") else None
        if hasattr(db, "connection"):
            self.connection = db.connection
        elif isinstance(db, sqlite3.Connection):
            self.connection = db
            if self.connection.row_factory is None:
                self.connection.row_factory = sqlite3.Row
        else:
            self.connection = sqlite3.connect(str(db), check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self._owner = True
        self.root = Path(root) if root is not None else None
        self.resolver = resolver

    def close(self) -> None:
        if self._owner:
            self.connection.close()

    def _transaction(self):
        if self._store is not None and hasattr(self._store, "transaction"):
            return self._store.transaction()
        from contextlib import nullcontext
        return nullcontext(self)

    def put(
        self,
        content: bytes | bytearray | memoryview,
        *,
        kind: str = "source",
        uri: str | None = None,
        produced_by: str | None = None,
        source_refs: Sequence[str] = (),
        visibility: str = "local",
        metadata: Mapping[str, Any] | None = None,
        link: tuple[str, str, str] | None = None,
    ) -> ArtifactRecord:
        raw = bytes(content)
        sha = _digest(raw)
        artifact_id = f"sha256:{sha}"
        if uri is None:
            uri = f"artifact://{sha}"
        row = ArtifactRecord(
            id=artifact_id,
            kind=str(getattr(kind, "value", kind)),
            uri=str(uri),
            sha256=sha,
            produced_by=produced_by,
            source_refs=tuple(map(str, source_refs)),
            visibility=str(getattr(visibility, "value", visibility)),
            metadata=dict(metadata or {}),
            created_at=_now(),
        )
        with self._transaction():
            existing = self.connection.execute(
                "SELECT * FROM artifacts WHERE id = ? OR sha256 = ?", (artifact_id, sha)
            ).fetchone()
            if existing is not None:
                current = self._row(existing)
                if current.to_dict() | {"created_at": None} != row.to_dict() | {"created_at": None}:
                    raise ArtifactImmutableError(f"artifact {artifact_id} already exists with different metadata")
                row = current
            else:
                self.connection.execute(
                    "INSERT INTO artifacts (id, kind, uri, sha256, produced_by, source_refs_json, visibility, metadata_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row.id, row.kind, row.uri, row.sha256, row.produced_by, _json(list(row.source_refs)), row.visibility, _json(row.metadata), row.created_at),
                )
            if link is not None:
                self.link(row.ref, scope_type=link[0], scope_id=link[1], relation=link[2])
        if self.root is not None:
            target = self.root / sha
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != raw:
                raise ArtifactIntegrityError(f"local artifact payload mismatch: {target}")
            if not target.exists():
                target.write_bytes(raw)
        return row

    def register(
        self,
        *,
        sha256: str,
        kind: str,
        uri: str,
        produced_by: str | None = None,
        source_refs: Sequence[str] = (),
        visibility: str = "local",
        metadata: Mapping[str, Any] | None = None,
        link: tuple[str, str, str] | None = None,
    ) -> ArtifactRecord:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256.lower()):
            raise ValueError("sha256 must be a hexadecimal SHA-256 digest")
        row = ArtifactRecord(f"sha256:{sha256.lower()}", str(getattr(kind, "value", kind)), str(uri), sha256.lower(), produced_by, tuple(source_refs), str(getattr(visibility, "value", visibility)), dict(metadata or {}), _now())
        with self._transaction():
            existing = self.connection.execute("SELECT * FROM artifacts WHERE id = ?", (row.id,)).fetchone()
            if existing is not None:
                current = self._row(existing)
                if current.to_dict() | {"created_at": None} != row.to_dict() | {"created_at": None}:
                    raise ArtifactImmutableError(f"artifact {row.id} already exists with different metadata")
                row = current
            else:
                self.connection.execute(
                    "INSERT INTO artifacts (id, kind, uri, sha256, produced_by, source_refs_json, visibility, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row.id, row.kind, row.uri, row.sha256, row.produced_by, _json(list(row.source_refs)), row.visibility, _json(row.metadata), row.created_at),
                )
            if link is not None:
                self.link(row.ref, scope_type=link[0], scope_id=link[1], relation=link[2])
        return row

    def get(self, ref: str) -> ArtifactRecord:
        row = self.connection.execute("SELECT * FROM artifacts WHERE id = ? OR id = ? OR sha256 = ?", (_artifact_id(ref), str(ref), _artifact_id(ref).removeprefix("sha256:"))).fetchone()
        if row is None:
            raise ArtifactNotFoundError(ref)
        return self._row(row)

    def link(self, ref: str, *, scope_type: str, scope_id: str, relation: str = "references") -> ArtifactLink:
        artifact = self.get(ref)
        link = ArtifactLink(artifact.ref, str(scope_type), str(scope_id), str(relation), _now())
        self.connection.execute(
            "INSERT OR IGNORE INTO artifact_links (artifact_id, scope_type, scope_id, relation, created_at) VALUES (?, ?, ?, ?, ?)",
            (_artifact_id(artifact.id), link.scope_type, link.scope_id, link.relation, link.created_at),
        )
        return link

    def links(self, ref: str) -> tuple[ArtifactLink, ...]:
        artifact = self.get(ref)
        rows = self.connection.execute("SELECT * FROM artifact_links WHERE artifact_id = ? ORDER BY created_at", (_artifact_id(artifact.id),)).fetchall()
        return tuple(ArtifactLink(artifact.ref, row["scope_type"], row["scope_id"], row["relation"], row["created_at"]) for row in rows)

    def resolve(self, ref: str, *, verify: bool = True) -> bytes:
        artifact = self.get(ref)
        parsed = urlparse(artifact.uri)
        content: bytes | None = None
        if parsed.scheme == "artifact":
            if self.root is not None:
                path = self.root / artifact.sha256
                if path.exists():
                    content = path.read_bytes()
        elif parsed.scheme in {"file", ""}:
            path = Path(unquote(parsed.path if parsed.scheme == "file" else artifact.uri))
            if path.exists():
                content = path.read_bytes()
        if content is None and self.root is not None:
            local_path = self.root / artifact.sha256
            if local_path.exists():
                content = local_path.read_bytes()
        if content is None and self.resolver is not None:
            content = bytes(self.resolver(artifact.uri))
        if content is None:
            raise ArtifactNotFoundError(f"payload unavailable for {artifact.ref} ({artifact.uri})")
        if verify and _digest(content) != artifact.sha256:
            raise ArtifactIntegrityError(f"digest mismatch for {artifact.ref}")
        return content

    def verify(self, ref: str) -> bool:
        self.resolve(ref, verify=True)
        return True

    def _row(self, row: sqlite3.Row | Mapping[str, Any]) -> ArtifactRecord:
        get = row.__getitem__
        return ArtifactRecord(
            id=str(get("id")), kind=str(get("kind")), uri=str(get("uri")), sha256=str(get("sha256")),
            produced_by=get("produced_by"), source_refs=tuple(json.loads(get("source_refs_json") or "[]")),
            visibility=str(get("visibility")), metadata=dict(json.loads(get("metadata_json") or "{}")), created_at=get("created_at"),
        )


ArtifactStoreAPI = ArtifactStore

__all__ = [
    "ArtifactError", "ArtifactNotFoundError", "ArtifactIntegrityError", "ArtifactImmutableError",
    "ArtifactRecord", "ArtifactLink", "ArtifactStore", "ArtifactStoreAPI",
]
