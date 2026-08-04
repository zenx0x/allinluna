"""Shared read-only compatibility result types."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CompatibilityReport:
    source_kind: str
    source_digest: str
    losses: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    read_only: bool = True
    model_evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_digest": self.source_digest,
            "losses": list(self.losses),
            "unknowns": list(self.unknowns),
            "warnings": list(self.warnings),
            "read_only": self.read_only,
            "model_evidence": dict(self.model_evidence),
        }


__all__ = ["CompatibilityReport", "digest"]
