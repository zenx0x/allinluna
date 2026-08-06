"""Typed, declarative verification procedures for task contracts.

``done_when`` states the semantic outcome a task must reach.  A
``VerificationSpec`` describes the independent, executable or observable
procedure which may establish evidence for that outcome.  The two concepts are
intentionally not interchangeable: human language is never promoted into a
shell command.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


VERIFICATION_KINDS = frozenset({"command", "artifact", "workspace", "human", "pack"})


class VerificationSpecError(ValueError):
    """A verification procedure is incomplete or has an invalid shape."""


def _strings(value: Any, field_name: str, *, required: bool = False) -> tuple[str, ...]:
    if value is None:
        result: tuple[str, ...] = ()
    elif isinstance(value, str):
        result = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        result = tuple(str(item).strip() for item in value)
    else:
        raise VerificationSpecError(f"{field_name} must be a string or sequence of strings")
    if any(not item for item in result):
        raise VerificationSpecError(f"{field_name} entries must be non-empty strings")
    if required and not result:
        raise VerificationSpecError(f"{field_name} must not be empty")
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True)
class VerificationSpec:
    """One durable independent verification procedure.

    ``command`` is the only procedure that invokes a subprocess.  ``artifact``
    and ``workspace`` are observable collector checks, ``human`` explicitly
    requests manual evidence, and ``pack`` is a declarative Pack assertion.
    """

    id: str
    kind: str
    satisfies: tuple[str, ...] = ()
    command: str | tuple[str, ...] | None = None
    timeout_seconds: float | None = None
    artifact_ref: str | None = None
    assertion: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    source: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    trust: Mapping[str, Any] = field(default_factory=dict)
    execution: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        identifier = str(self.id).strip()
        if not identifier:
            raise VerificationSpecError("verification spec id must be non-empty")
        object.__setattr__(self, "id", identifier)
        kind = str(self.kind).strip().lower()
        if kind not in VERIFICATION_KINDS:
            raise VerificationSpecError(f"unsupported verification kind: {kind!r}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "satisfies", _strings(self.satisfies, "verification spec satisfies"))
        if self.command is not None:
            if isinstance(self.command, str):
                command: str | tuple[str, ...] = self.command.strip()
                if not command:
                    raise VerificationSpecError("verification command must be non-empty")
            elif isinstance(self.command, Sequence) and not isinstance(self.command, (bytes, bytearray)):
                command = _strings(self.command, "verification command", required=True)
            else:
                raise VerificationSpecError("verification command must be a string or sequence")
            object.__setattr__(self, "command", command)
        if kind == "command" and self.command is None:
            raise VerificationSpecError("command verification requires command")
        if kind != "command" and self.command is not None:
            raise VerificationSpecError(f"{kind} verification cannot declare command")
        if self.timeout_seconds is not None:
            if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not 0 < float(self.timeout_seconds) <= 900:
                raise VerificationSpecError("timeout_seconds must be between 0 and 900")
            object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        if self.artifact_ref is not None:
            ref = str(self.artifact_ref).strip()
            if not ref:
                raise VerificationSpecError("artifact_ref must be non-empty")
            object.__setattr__(self, "artifact_ref", ref)
        if kind == "artifact" and self.artifact_ref is None:
            raise VerificationSpecError("artifact verification requires artifact_ref")
        if self.assertion is not None:
            assertion = str(self.assertion).strip()
            if not assertion:
                raise VerificationSpecError("assertion must be non-empty")
            object.__setattr__(self, "assertion", assertion)
        if kind == "pack" and self.assertion is None:
            raise VerificationSpecError("pack verification requires assertion")
        if not isinstance(self.details, Mapping):
            raise VerificationSpecError("verification details must be an object")
        object.__setattr__(self, "details", dict(self.details))
        if self.source is not None:
            source = str(self.source).strip()
            if not source:
                raise VerificationSpecError("verification source must be non-empty")
            object.__setattr__(self, "source", source)
        for field_name in ("provenance", "trust", "execution"):
            value = getattr(self, field_name)
            if value is None:
                value = {}
            if not isinstance(value, Mapping):
                raise VerificationSpecError(f"verification {field_name} must be an object")
            object.__setattr__(self, field_name, dict(value))
        if self.timeout_seconds is None and self.execution.get("timeout_seconds") is not None:
            try:
                timeout = float(self.execution["timeout_seconds"])
            except (TypeError, ValueError) as exc:
                raise VerificationSpecError("verification execution timeout must be numeric") from exc
            if not 0 < timeout <= 900:
                raise VerificationSpecError("verification execution timeout must be between 0 and 900")
            object.__setattr__(self, "timeout_seconds", timeout)

    @property
    def executable(self) -> bool:
        return self.kind == "command"

    @property
    def manual_evidence_required(self) -> bool:
        return self.kind == "human"

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"id": self.id, "kind": self.kind, "satisfies": list(self.satisfies)}
        if self.command is not None:
            value["command"] = list(self.command) if isinstance(self.command, tuple) else self.command
        if self.timeout_seconds is not None:
            value["timeout_seconds"] = self.timeout_seconds
        if self.artifact_ref is not None:
            value["artifact_ref"] = self.artifact_ref
        if self.assertion is not None:
            value["assertion"] = self.assertion
        if self.details:
            value["details"] = dict(self.details)
        if self.source is not None:
            value["source"] = self.source
        if self.provenance:
            value["provenance"] = dict(self.provenance)
        if self.trust:
            value["trust"] = dict(self.trust)
        if self.execution:
            value["execution"] = dict(self.execution)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | "VerificationSpec") -> "VerificationSpec":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise VerificationSpecError("verification spec must be an object")
        data = dict(value)
        return cls(
            id=str(data.get("id") or data.get("name") or ""),
            # Compatibility input may omit ``kind`` only when it already
            # supplies an executable command.  A bare natural-language name
            # is never inferred as a command.
            kind=str(data.get("kind") or ("command" if data.get("command") is not None else "")),
            satisfies=_strings(data.get("satisfies", data.get("done_when", ())), "verification spec satisfies"),
            command=data.get("command"),
            timeout_seconds=data.get("timeout_seconds"),
            artifact_ref=data.get("artifact_ref") or data.get("ref"),
            assertion=data.get("assertion"),
            details=data.get("details", {}),
            source=data.get("source"),
            provenance=data.get("provenance", {}),
            trust=data.get("trust", {}),
            execution=data.get("execution", {}),
        )


# Pack verification uses the exact same typed shape.  The alias is retained so
# Pack APIs communicate intent without reintroducing a callable verifier type.
VerifierSpec = VerificationSpec


def verification_specs(value: Any) -> tuple[VerificationSpec, ...]:
    if value is None:
        return ()
    if isinstance(value, (VerificationSpec, Mapping)):
        value = (value,)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise VerificationSpecError("verification_specs must be a sequence")
    result = tuple(VerificationSpec.from_dict(item) for item in value)
    ids = [item.id for item in result]
    if len(ids) != len(set(ids)):
        raise VerificationSpecError("verification spec ids must be unique")
    return result


__all__ = ["VERIFICATION_KINDS", "VerificationSpec", "VerificationSpecError", "VerifierSpec", "verification_specs"]
