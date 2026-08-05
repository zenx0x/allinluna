"""Canonical requested/resolved/actual host-resource observation.

Policy selection is not host evidence.  This value object keeps the three
resource lanes distinct and is deliberately shared by the broker, host adapter,
and Store so they cannot apply divergent rules to the same receipt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _route(value: Any) -> dict[str, str] | None:
    raw = _mapping(value)
    model = _text(raw.get("model"))
    reasoning = _text(raw.get("reasoning") or raw.get("thinking"))
    return {"model": model, "reasoning": reasoning} if model and reasoning else None


def valid_observed_at(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    """The sole validated representation of host resource observation.

    A resolved ``actual`` requires a complete matching route, a named evidence
    source, and a timestamp.  Otherwise the value is normalized to unresolved;
    callers cannot promote policy resolution or diagnostic telemetry to a host
    fact by accident.
    """

    requested: Mapping[str, Any]
    resolved: Mapping[str, Any]
    actual: Mapping[str, Any] | None = None
    actual_state: str = "unresolved"
    evidence_source: str | None = None
    observed_at: str | None = None
    diagnostics: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        requested = _mapping(self.requested)
        resolved = _mapping(self.resolved)
        actual = _route(self.actual)
        state = str(self.actual_state or "unresolved")
        source = _text(self.evidence_source)
        observed_at = self.observed_at if valid_observed_at(self.observed_at) else None
        resolved_route = _route(resolved)
        valid = (
            state == "resolved"
            and actual is not None
            and resolved_route is not None
            and actual == resolved_route
            and source is not None
            and observed_at is not None
        )
        object.__setattr__(self, "requested", requested)
        object.__setattr__(self, "resolved", resolved)
        object.__setattr__(self, "actual", actual if valid else None)
        object.__setattr__(self, "actual_state", "resolved" if valid else "unresolved")
        object.__setattr__(self, "evidence_source", source if valid else None)
        object.__setattr__(self, "observed_at", observed_at if valid else None)
        object.__setattr__(
            self,
            "diagnostics",
            dict(self.diagnostics) if isinstance(self.diagnostics, Mapping) else None,
        )

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        requested: Mapping[str, Any] | None = None,
        resolved: Mapping[str, Any] | None = None,
    ) -> "ResourceObservation":
        raw = _mapping(value)
        return cls(
            requested=_mapping(requested) if requested is not None else _mapping(raw.get("requested")),
            resolved=_mapping(resolved) if resolved is not None else _mapping(raw.get("resolved")),
            actual=raw.get("actual"),
            actual_state=str(raw.get("actual_state", raw.get("actualState", "unresolved"))),
            evidence_source=raw.get("evidence_source", raw.get("evidenceSource")),
            observed_at=raw.get("observed_at", raw.get("observedAt")),
            diagnostics=raw.get("diagnostics"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "requested": dict(self.requested),
            "resolved": dict(self.resolved),
            "actual": dict(self.actual) if self.actual is not None else None,
            "actual_state": self.actual_state,
            "evidence_source": self.evidence_source,
            "observed_at": self.observed_at,
        }
        if self.diagnostics is not None:
            result["diagnostics"] = dict(self.diagnostics)
        return result


__all__ = ["ResourceObservation", "valid_observed_at"]
