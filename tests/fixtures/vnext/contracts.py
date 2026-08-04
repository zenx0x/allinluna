"""Protocol-shaped records used to exercise integration boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Correction:
    target: str
    expected_contract_revision: int
    issue: str
    evidence_refs: tuple[str, ...] = ()
    required_change: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContractDelta:
    target: str
    previous_revision: int
    next_revision: int
    changed_exports: tuple[str, ...]
    reason: str
    artifact_refs: tuple[str, ...] = ()
    delta_id: str = ""

    def __post_init__(self) -> None:
        if self.next_revision <= self.previous_revision:
            raise ValueError("contract delta must advance the contract revision")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["changed_exports"] = list(self.changed_exports)
        result["artifact_refs"] = list(self.artifact_refs)
        return result


@dataclass(frozen=True)
class PromotionRequest:
    work_unit_id: str
    requested_by: str
    reason: str
    requested_scope: tuple[str, ...]
    requested_authority: tuple[str, ...] = ()
    requested_ownership: tuple[str, ...] = ()
    request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for field_name in (
            "requested_scope",
            "requested_authority",
            "requested_ownership",
        ):
            result[field_name] = list(getattr(self, field_name))
        return result


@dataclass(frozen=True)
class ContextInvalidation:
    snapshot_ref: str
    reason: str
    invalidated_by: str
    replacement_required: bool = True
    invalidation_id: str = ""
    dependent_refs: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["dependent_refs"] = list(self.dependent_refs)
        return result
