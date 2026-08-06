"""Repository-aware planning of typed, trusted verification procedures."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .verification import VerificationSpec, VerificationSpecError, verification_specs


class VerificationPlanningError(ValueError):
    """A verification plan cannot be made without inventing evidence."""


def _raw(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        result = method()
        if isinstance(result, Mapping):
            return dict(result)
    return dict(vars(value))


def _text_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise VerificationPlanningError("verification conditions must be a sequence of strings")
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9]+", "-", str(value).lower()).strip("-")
    return result[:80] or "condition"


def _repo_roots(repository_context: Any) -> tuple[Path, ...]:
    value = _raw(repository_context) if not isinstance(repository_context, Mapping) else dict(repository_context)
    roots = value.get("roots") or ()
    result: list[Path] = []
    for item in roots:
        raw = item.get("path") if isinstance(item, Mapping) else item
        if raw:
            path = Path(str(raw))
            if path.exists() and path.is_dir():
                result.append(path)
    return tuple(dict.fromkeys(result))


def _metadata(domain: Any) -> dict[str, Any]:
    value = _raw(domain)
    metadata = value.get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _origin_metadata(domain: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = _metadata(domain)
    raw = metadata.get("verification_provenance") or metadata.get("provenance")
    provenance = {"source_kind": "user-approved", "generated_by": "VerificationPlanner"}
    if isinstance(raw, str):
        provenance["source_kind"] = raw
    elif isinstance(raw, Mapping):
        provenance.update(dict(raw))
    trust = metadata.get("verification_trust") or metadata.get("trust")
    trust_value = dict(trust) if isinstance(trust, Mapping) else {}
    return provenance, trust_value


def _pack_id(pack: Any) -> str:
    if isinstance(pack, Mapping):
        return str(pack.get("id") or pack.get("pack_id") or "pack")
    return str(getattr(pack, "id", None) or pack or "pack")


def _pack_specs(pack: Any, domain: Any) -> tuple[VerificationSpec, ...]:
    verifier = getattr(pack, "verifiers", None)
    if not callable(verifier):
        return ()
    try:
        raw = verifier(type("VerificationTask", (), _raw(domain))())
        specs = verification_specs(raw)
    except (TypeError, VerificationSpecError, AttributeError):
        return ()
    result: list[VerificationSpec] = []
    for spec in specs:
        data = spec.to_dict()
        provenance = dict(data.get("provenance") or {})
        provenance.setdefault("source_kind", "pack-signed")
        provenance.setdefault("source_ref", f"pack://{_pack_id(pack)}")
        provenance.setdefault("generated_by", "VerificationPlanner")
        trust = dict(data.get("trust") or {})
        trust.setdefault("state", "trusted")
        trust.setdefault("reason", "declared by the selected Workflow Pack")
        data.update(
            source="pack-signed",
            provenance=provenance,
            trust=trust,
            execution={"sandbox": "none", "network": "deny", "destructive": False},
        )
        result.append(VerificationSpec.from_dict(data))
    return tuple(result)


@dataclass(frozen=True)
class VerificationPlan:
    """A typed plan and its unresolved/permission boundary."""

    goal: str
    conditions: tuple[str, ...]
    specs: tuple[VerificationSpec, ...] = ()
    unresolved_conditions: tuple[str, ...] = ()
    decision_required: bool = False
    repository_context: Mapping[str, Any] = field(default_factory=dict)
    source: str = "VerificationPlanner"

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal", str(self.goal).strip())
        object.__setattr__(self, "conditions", _text_tuple(self.conditions))
        object.__setattr__(self, "specs", verification_specs(self.specs))
        object.__setattr__(self, "unresolved_conditions", _text_tuple(self.unresolved_conditions))
        object.__setattr__(self, "repository_context", dict(self.repository_context))
        if not self.goal:
            raise VerificationPlanningError("verification plan goal must be non-empty")

    @property
    def trusted_specs(self) -> tuple[VerificationSpec, ...]:
        return tuple(
            spec
            for spec in self.specs
            if str(spec.trust.get("state", "trusted")) == "trusted" and spec.kind != "human"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "verification-plan",
            "schema_version": "1.0",
            "protocol": "verification-plan/v1",
            "goal": self.goal,
            "conditions": list(self.conditions),
            "specs": [spec.to_dict() for spec in self.specs],
            "unresolved_conditions": list(self.unresolved_conditions),
            "decision_required": bool(self.decision_required),
            "repository_context": dict(self.repository_context),
            "source": self.source,
        }


class VerificationPlanner:
    """Discover safe existing project checks without inventing a pass command."""

    API_VERSION = 1
    MAX_SPECS = 8
    DEFAULT_TIMEOUT_SECONDS = 900.0
    ENV_ALLOWLIST = ("PATH", "PYTHONPATH", "VIRTUAL_ENV", "SystemDrive", "SystemRoot", "WINDIR", "TEMP", "TMP")

    def plan(
        self,
        *,
        goal: str,
        repository_context: Mapping[str, Any] | Any = None,
        outcome_domain: Mapping[str, Any] | Any = None,
        ownership: Sequence[str] = (),
        pack: Any = None,
    ) -> VerificationPlan:
        domain = _raw(outcome_domain or {})
        conditions = _text_tuple(domain.get("done_when") or domain.get("checks")) or (str(goal).strip() + " is evidenced",)
        explicit = verification_specs(domain.get("verification_specs", ()))
        pack_specs = _pack_specs(pack, domain) if pack is not None else ()
        # Explicit typed specs are retained exactly by DeliveryPack.  Planner
        # annotations are added only when the caller omitted provenance/trust,
        # preserving the RC1 compatibility contract for user-authored specs.
        explicit_specs = tuple(self._annotate_explicit(spec, domain) for spec in explicit)
        if explicit_specs:
            unresolved = self._unresolved(conditions, explicit_specs)
            if unresolved:
                unresolved = tuple(item for item in unresolved if item not in {""})
            return VerificationPlan(
                str(goal), conditions, explicit_specs, unresolved,
                bool(unresolved) or any(self._approval_required(spec) for spec in explicit_specs),
                dict(repository_context or {}),
            )

        discovered = self._discover_repository(repository_context, conditions, ownership)
        specs = tuple((*discovered, *pack_specs))[: self.MAX_SPECS]
        unresolved = self._unresolved(conditions, specs)
        decision_required = bool(unresolved) or any(self._approval_required(spec) for spec in specs)
        if unresolved:
            specs = (*specs, self._manual_spec(unresolved))
        return VerificationPlan(
            str(goal), conditions, tuple(specs), unresolved, decision_required,
            dict(repository_context or {}),
        )

    create = plan

    @staticmethod
    def _approval_required(spec: VerificationSpec) -> bool:
        return str(spec.trust.get("state") or "trusted") != "trusted"

    def _annotate_explicit(self, spec: VerificationSpec, domain: Mapping[str, Any]) -> VerificationSpec:
        data = spec.to_dict()
        provenance, trust = _origin_metadata(domain)
        data.setdefault("source", "user-approved")
        data.setdefault("provenance", provenance)
        if not data.get("trust"):
            state = "approval_required" if provenance.get("source_kind") in {"model-proposed", "legacy-imported", "external-packet"} else "trusted"
            data["trust"] = {"state": state, "reason": "explicit typed verification procedure" if state == "trusted" else "untrusted source requires approval"}
        if trust:
            data["trust"] = {**trust, **dict(data.get("trust") or {})}
        execution = dict(data.get("execution") or {})
        if spec.kind == "command":
            execution.setdefault("sandbox", "worktree")
            execution.setdefault("network", "deny")
            execution.setdefault("env_allowlist", list(self.ENV_ALLOWLIST))
            execution.setdefault("destructive", False)
            if spec.timeout_seconds is not None:
                execution.setdefault("timeout_seconds", spec.timeout_seconds)
            data["execution"] = execution
        return VerificationSpec.from_dict(data)

    def _discover_repository(
        self,
        repository_context: Mapping[str, Any] | Any,
        conditions: tuple[str, ...],
        ownership: Sequence[str],
    ) -> tuple[VerificationSpec, ...]:
        candidates: list[VerificationSpec] = []
        for root in _repo_roots(repository_context):
            candidates.extend(self._root_candidates(root, conditions, ownership))
            if len(candidates) >= self.MAX_SPECS:
                break
        return tuple(candidates[: self.MAX_SPECS])

    def _root_candidates(self, root: Path, conditions: tuple[str, ...], ownership: Sequence[str]) -> list[VerificationSpec]:
        result: list[VerificationSpec] = []
        pyproject = root / "pyproject.toml"
        if pyproject.is_file() and (root / "tests").is_dir():
            result.append(self._command_spec(
                "pytest", [sys.executable, "-m", "pytest", "-q"], conditions, root,
                pyproject, "repository test entrypoint",
            ))
        package = root / "package.json"
        if package.is_file():
            try:
                package_data = json.loads(package.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                package_data = {}
            scripts = package_data.get("scripts", {}) if isinstance(package_data, Mapping) else {}
            for name in ("test", "lint", "typecheck", "build"):
                if isinstance(scripts, Mapping) and name in scripts:
                    result.append(self._command_spec(
                        f"npm-{name}", ["npm", "run", name], conditions, root,
                        package, f"package.json {name} script",
                    ))
        if (root / "Cargo.toml").is_file():
            result.append(self._command_spec("cargo-test", ["cargo", "test"], conditions, root, root / "Cargo.toml", "Cargo test entrypoint"))
        if (root / "go.mod").is_file():
            result.append(self._command_spec("go-test", ["go", "test", "./..."], conditions, root, root / "go.mod", "Go test entrypoint"))
        makefile = next((root / name for name in ("Makefile", "makefile") if (root / name).is_file()), None)
        if makefile is not None:
            try:
                make_text = makefile.read_text(encoding="utf-8", errors="replace")
            except OSError:
                make_text = ""
            if re.search(r"(?m)^\s*test\s*:", make_text):
                result.append(self._command_spec("make-test", ["make", "test"], conditions, root, makefile, "Makefile test target"))
        for filename, command, label in (
            ("tox.ini", [sys.executable, "-m", "tox"], "tox test entrypoint"),
            ("noxfile.py", [sys.executable, "-m", "nox"], "nox test entrypoint"),
        ):
            path = root / filename
            if path.is_file():
                result.append(self._command_spec(_slug(filename), command, conditions, root, path, label))
        return result

    def _command_spec(
        self,
        identifier: str,
        command: Sequence[str],
        conditions: tuple[str, ...],
        root: Path,
        source_path: Path,
        reason: str,
    ) -> VerificationSpec:
        return VerificationSpec(
            id=identifier,
            kind="command",
            command=tuple(map(str, command)),
            timeout_seconds=self.DEFAULT_TIMEOUT_SECONDS,
            satisfies=conditions,
            source="repository-discovered",
            provenance={
                "source_kind": "repository-discovered",
                "source_ref": str(source_path),
                "generated_by": "VerificationPlanner",
            },
            trust={"state": "trusted", "reason": reason},
            execution={
                "sandbox": "worktree",
                "network": "deny",
                "env_allowlist": list(self.ENV_ALLOWLIST),
                "destructive": False,
                "timeout_seconds": self.DEFAULT_TIMEOUT_SECONDS,
                "cwd": str(root),
                "workspace": str(root),
            },
            details={"cwd": str(root)},
        )

    @staticmethod
    def _unresolved(conditions: Sequence[str], specs: Sequence[VerificationSpec]) -> tuple[str, ...]:
        covered: set[str] = set()
        for spec in specs:
            if spec.kind in {"command", "artifact", "workspace"} and str(spec.trust.get("state") or "trusted") == "trusted":
                covered.update(map(str, spec.satisfies))
        return tuple(str(condition) for condition in conditions if str(condition) not in covered)

    @staticmethod
    def _manual_spec(unresolved: Sequence[str]) -> VerificationSpec:
        conditions = tuple(map(str, unresolved))
        return VerificationSpec(
            id="manual-evidence-" + _slug(conditions[0] if conditions else "decision"),
            kind="human",
            satisfies=conditions,
            source="planner-decision",
            provenance={"source_kind": "user-approved", "generated_by": "VerificationPlanner"},
            trust={"state": "approval_required", "reason": "no safe repository verification entrypoint was found"},
            execution={"sandbox": "none", "network": "deny", "destructive": False},
        )


__all__ = [
    "VerificationPlan",
    "VerificationPlanner",
    "VerificationPlanningError",
]
