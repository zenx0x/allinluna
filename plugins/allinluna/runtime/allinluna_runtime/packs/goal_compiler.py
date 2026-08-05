"""Natural-language goal compilation for the vNext public Skill.

The public API deliberately accepts a sentence rather than a resource
questionnaire or an implementation-shaped task list.  This module turns that
sentence into outcome domains first.  Workflow Packs then add their lane-local
recipe to each domain without changing the global dependency layer.

The decomposition is deterministic by design.  It is a small product
compiler, not a pretend LLM receipt: explicit domain lists and dependency
phrases are preserved, while an unambiguous atomic sentence remains one
domain.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import islice
from pathlib import Path
import re
from typing import Any, Protocol

from ..domain import RunIntent
from ..verification import VerificationSpec, verification_specs


_DOMAIN_MARKERS = (
    "api",
    "backend",
    "service",
    "frontend",
    "front-end",
    "ui",
    "ux",
    "dashboard",
    "website",
    "web app",
    "mobile",
    "database",
    "schema",
    "migration",
    "documentation",
    "docs",
    "readme",
    "test",
    "tests",
    "testing",
    "deployment",
    "deploy",
    "release",
    "monitoring",
    "observability",
    "authentication",
    "auth",
    "payments",
    "billing",
    "research",
    "experiment",
    "analysis",
    "report",
    "model",
    "library",
    "package",
    "plugin",
    "cli",
    "integration",
)

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "complete",
    "completed",
    "complete",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "once",
    "ready",
    "set",
    "the",
    "then",
    "to",
    "up",
    "when",
    "with",
}

_LEADING_VERBS = {
    "add",
    "build",
    "create",
    "define",
    "deploy",
    "design",
    "develop",
    "document",
    "implement",
    "integrate",
    "migrate",
    "prepare",
    "refactor",
    "ship",
    "test",
    "write",
}


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return " ".join(value.strip().split())


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise ValueError("expected an array of strings")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _slug(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value.lower())
    useful = [word for word in words if word not in _STOP_WORDS and word not in _LEADING_VERBS]
    if not useful:
        useful = words or ["outcome"]
    return "-".join(useful)[:48].strip("-") or "outcome"


def _clean_clause(value: str) -> str:
    value = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", value)
    value = re.sub(r"^\s*(?:first|next|finally|also|then)\s*[:,-]?\s*", "", value, flags=re.I)
    value = value.strip(" \t\r\n,.;:。；")
    return " ".join(value.split())


def _clean_dependency(value: str) -> str:
    value = re.sub(r"\s+(?:is|are)\s+(?:ready|complete|completed|done|finished)\s*$", "", value, flags=re.I)
    value = re.sub(r"^\s*(?:the|a|an)\s+", "", value.strip(), flags=re.I)
    return _clean_clause(value)


def _looks_like_domain(value: str) -> bool:
    lowered = value.lower()
    return any(
        (re.search(rf"\b{re.escape(marker)}\b", lowered) is not None)
        if " " not in marker and "-" not in marker
        else marker in lowered
        for marker in _DOMAIN_MARKERS
    )


class RepositoryContextInspector:
    """Bounded, read-only observation of declared repository roots.

    The inspector deliberately observes directory names only.  It never
    walks a repository, reads source contents, invokes Git, or invents a
    surface for a missing/projectless root.  Direct children and one bounded
    level below common containers are enough to distinguish independent
    product surfaces such as ``backend`` and ``frontend`` for a broad goal.
    """

    _CONTAINERS = {"apps", "components", "modules", "packages", "services"}
    _EXCLUDED = {
        ".git",
        ".github",
        ".idea",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "build",
        "cache",
        "coverage",
        "dist",
        "node_modules",
        "target",
        "tmp",
        "vendor",
        "venv",
    }
    _SURFACE_HINTS = {
        "api",
        "app",
        "auth",
        "backend",
        "client",
        "component",
        "data",
        "database",
        "db",
        "docs",
        "documentation",
        "experiment",
        "frontend",
        "integration",
        "library",
        "mobile",
        "research",
        "server",
        "service",
        "test",
        "ui",
        "web",
    }

    def __init__(self, *, max_entries: int = 128, max_nested_entries: int = 32) -> None:
        if max_entries < 1 or max_nested_entries < 1:
            raise ValueError("repository inspection bounds must be positive")
        self.max_entries = int(max_entries)
        self.max_nested_entries = int(max_nested_entries)

    def inspect(self, repository: Any = None) -> dict[str, Any]:
        mode = self._field(repository, "mode")
        mode = getattr(mode, "value", mode)
        roots = tuple(self._field(repository, "roots") or ())
        base = {
            "inspector": "RepositoryContextInspector",
            "version": "2.1",
            "mode": str(mode or "projectless"),
            "scan_policy": {
                "max_depth": 2,
                "max_entries_per_root": self.max_entries,
                "max_entries_per_container": self.max_nested_entries,
                "content_reads": 0,
                "git_commands": 0,
            },
            "roots": [],
            "surfaces": [],
            "independent_surfaces": [],
        }
        if not roots:
            base["status"] = "projectless" if base["mode"] == "projectless" else "no-roots"
            base["evidence"] = [{"kind": "repository-roots", "observed": False, "reason": "no-declared-roots"}]
            return base

        multi_root = len(roots) > 1
        all_surfaces: list[dict[str, Any]] = []
        for index, root in enumerate(roots):
            root_record, surfaces = self._inspect_root(root, index=index, multi_root=multi_root)
            base["roots"].append(root_record)
            all_surfaces.extend(surfaces)
        base["surfaces"] = all_surfaces
        base["independent_surfaces"] = list(all_surfaces)
        root_statuses = {str(item["status"]) for item in base["roots"]}
        if all(status == "observed" for status in root_statuses):
            base["status"] = "observed" if all_surfaces else "observed-no-surfaces"
        elif all(status in {"missing", "not-directory", "unreadable"} for status in root_statuses):
            base["status"] = "missing-root"
        else:
            base["status"] = "partial"
        base["evidence"] = [
            {
                "kind": "repository-root-observation",
                "root": item["path"],
                "status": item["status"],
                "observed_entries": list(item.get("observed_entries", ())),
                "surface_paths": [surface["path"] for surface in all_surfaces if surface["root"] == item["path"]],
            }
            for item in base["roots"]
        ]
        return base

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(name, default)
        return getattr(value, name, default)

    def _inspect_root(self, root: Any, *, index: int, multi_root: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        raw_path = self._field(root, "path")
        path_text = str(raw_path or "")
        root_path = Path(path_text) if path_text else None
        root_record: dict[str, Any] = {
            "index": index,
            "path": path_text,
            "declared_git": bool(self._field(root, "git", False)),
            "declared_dirty_state": str(self._field(root, "dirty_state", "unknown")),
            "status": "missing",
            "observed_entries": [],
            "entries_truncated": False,
        }
        if root_path is None or not root_path.exists():
            return root_record, []
        if not root_path.is_dir():
            root_record["status"] = "not-directory"
            return root_record, []
        try:
            observed_with_probe = list(islice(root_path.iterdir(), self.max_entries + 1))
            root_record["entries_truncated"] = len(observed_with_probe) > self.max_entries
            observed = sorted(observed_with_probe[: self.max_entries], key=lambda item: item.name.lower())
            root_record["observed_entries"] = [item.name for item in observed]
        except OSError as exc:
            root_record.update({"status": "unreadable", "error": type(exc).__name__})
            return root_record, []

        root_record["status"] = "observed"
        root_label = root_path.name or f"root-{index + 1}"
        root_prefix = _slug(root_label) if multi_root else ""
        surfaces: list[dict[str, Any]] = []
        for entry in observed:
            if not entry.is_dir() or entry.name.lower() in self._EXCLUDED:
                continue
            entry_name = entry.name
            if self._is_surface_name(entry_name):
                surfaces.append(self._surface(entry_name, entry, root_path, root_prefix, index, "root-entry"))
            if entry_name.lower() in self._CONTAINERS:
                try:
                    nested_entries = sorted(
                        list(islice(entry.iterdir(), self.max_nested_entries)),
                        key=lambda item: item.name.lower(),
                    )
                except OSError:
                    nested_entries = []
                for nested in nested_entries:
                    if nested.is_dir() and nested.name.lower() not in self._EXCLUDED and self._is_surface_name(nested.name):
                        surfaces.append(self._surface(f"{entry_name}/{nested.name}", nested, root_path, root_prefix, index, "container-entry"))
        return root_record, surfaces

    @classmethod
    def _is_surface_name(cls, value: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return any(hint in normalized.split("-") for hint in cls._SURFACE_HINTS) or any(
            hint in normalized for hint in ("backend", "frontend", "service", "component", "module")
        )

    @staticmethod
    def _surface(label: str, path: Path, root: Path, root_prefix: str, root_index: int, evidence_kind: str) -> dict[str, Any]:
        relative = label.replace("\\", "/")
        ownership = f"{root_prefix}/{relative}/**" if root_prefix else f"{relative}/**"
        surface_id = _slug(f"{root_prefix}-{relative}" if root_prefix else relative)
        return {
            "id": surface_id,
            "name": relative.split("/")[-1],
            "path": relative,
            "ownership": ownership,
            "root": str(root),
            "root_index": root_index,
            "kind": "directory",
            "evidence": {"kind": evidence_kind, "path": relative, "observed": True},
        }


@dataclass(frozen=True)
class OutcomeDomain:
    """One global outcome domain, which becomes one top-level Task/Lane."""

    id: str
    outcome: str
    done_when: tuple[str, ...] = ()
    verification_specs: tuple[VerificationSpec | Mapping[str, Any], ...] = ()
    dependencies: tuple[str, ...] = ()
    dependency_exports: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    ownership: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()
    priority: int = 0
    resource_envelope: Mapping[str, Any] = field(default_factory=dict)
    work_unit_resource_envelope: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "outcome domain id"))
        object.__setattr__(self, "outcome", _text(self.outcome, "outcome domain outcome"))
        object.__setattr__(self, "done_when", _as_tuple(self.done_when))
        object.__setattr__(self, "verification_specs", verification_specs(self.verification_specs))
        object.__setattr__(self, "dependencies", _as_tuple(self.dependencies))
        object.__setattr__(
            self,
            "dependency_exports",
            {str(key): _as_tuple(value) for key, value in dict(self.dependency_exports).items()},
        )
        object.__setattr__(self, "ownership", _as_tuple(self.ownership))
        object.__setattr__(self, "checks", _as_tuple(self.checks))
        object.__setattr__(self, "exports", _as_tuple(self.exports))
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ValueError("outcome domain priority must be an integer")
        object.__setattr__(self, "resource_envelope", dict(self.resource_envelope))
        object.__setattr__(self, "work_unit_resource_envelope", dict(self.work_unit_resource_envelope))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def dependency_ids(self) -> tuple[str, ...]:
        """Compatibility alias for callers that use graph terminology."""

        return self.dependencies

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "outcome": self.outcome,
            "done_when": list(self.done_when),
            "verification_specs": [item.to_dict() for item in self.verification_specs],
            "dependencies": [
                {
                    "id": dependency,
                    "exports": list(self.dependency_exports.get(dependency, ())),
                }
                if self.dependency_exports.get(dependency)
                else dependency
                for dependency in self.dependencies
            ],
            "ownership": list(self.ownership),
            "checks": list(self.checks),
            "exports": list(self.exports),
            "priority": self.priority,
            "resource_envelope": dict(self.resource_envelope),
            "work_unit_resource_envelope": dict(self.work_unit_resource_envelope),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class Decomposition(Sequence[OutcomeDomain]):
    """Compiler output that is both inspectable and sequence-compatible."""

    domains: tuple[OutcomeDomain, ...]
    strategy: str
    source: str
    explicit: bool = False
    repository_context: Mapping[str, Any] = field(default_factory=dict)
    ambiguous: bool = False
    clarification_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.domains:
            raise ValueError("goal decomposition must contain at least one outcome domain")
        if self.strategy not in {"atomic", "outcome-domain", "semantic"}:
            raise ValueError(f"unknown decomposition strategy: {self.strategy}")
        object.__setattr__(self, "repository_context", dict(self.repository_context))
        object.__setattr__(self, "ambiguous", bool(self.ambiguous))
        object.__setattr__(self, "clarification_reasons", _as_tuple(self.clarification_reasons))

    def __len__(self) -> int:
        return len(self.domains)

    def __getitem__(self, index: int | slice) -> OutcomeDomain | tuple[OutcomeDomain, ...]:
        return self.domains[index]

    def __iter__(self) -> Iterator[OutcomeDomain]:
        return iter(self.domains)

    @property
    def parallel_domains(self) -> tuple[OutcomeDomain, ...]:
        return tuple(domain for domain in self.domains if not domain.dependencies)

    @property
    def clarification_required(self) -> bool:
        return self.ambiguous

    def to_dict(self) -> dict[str, Any]:
        edges = [
            {"from": dependency, "to": domain.id, "exports": list(domain.dependency_exports.get(dependency, ())) }
            for domain in self.domains
            for dependency in domain.dependencies
        ]
        return {
            "pipeline": ["goal", "repository-context-inspection", "outcome-domain-decomposition"],
            "strategy": self.strategy,
            "source": self.source,
            "explicit": self.explicit,
            "ambiguous": self.ambiguous,
            "clarification_required": self.ambiguous,
            "clarification_reasons": list(self.clarification_reasons),
            "domain_count": len(self.domains),
            "domains": [domain.to_dict() for domain in self.domains],
            "parallel_domain_ids": [domain.id for domain in self.parallel_domains],
            "edges": edges,
            "repository_context": dict(self.repository_context),
        }


@dataclass
class _Fragment:
    text: str
    dependency_refs: tuple[str, ...] = ()


_REPOSITORY_WORDS = ("repository", "repo", "codebase", "monorepo", "project")
_BROAD_QUANTIFIERS = ("entire", "whole", "every", "all", "across", "throughout", "full")
_BROAD_ACTIONS = ("refactor", "migrate", "modernize", "upgrade", "clean up", "organize", "audit", "review")


class TaskDecomposer:
    """Deterministically decompose a goal into outcome domains.

    Conjunctions only split when both sides name recognizable outcome domains;
    this keeps ordinary atomic language such as ``build and verify the
    product`` as one Task.  Semicolons, list lines, ``then``, and explicit
    ``after``/``depends on`` clauses provide stronger boundaries.
    """

    version = "2.1"

    _AMBIGUITY_PATTERNS = (
        (r"\bambiguous\b", "goal-explicitly-ambiguous"),
        (r"\bunclear\b", "goal-explicitly-unclear"),
        (r"\bnot\s+sure\b", "goal-needs-user-choice"),
        (r"\bunspecified\b", "goal-has-unspecified-scope"),
        (r"\bto\s+be\s+decided\b", "goal-needs-decision"),
        (r"\bwhich\b", "goal-contains-choice"),
        (r"\bwhat\s+should\b", "goal-contains-choice"),
        (r"\bchoose\b", "goal-contains-choice"),
        (r"\bdecide\b", "goal-contains-choice"),
        (r"\bsomething\b", "goal-has-unspecified-object"),
        (r"\bsome\s+kind\s+of\b", "goal-has-unspecified-object"),
        (r"\bthe\s+thing\b", "goal-has-unspecified-object"),
        (r"\bmake\s+it\s+better\b", "goal-needs-success-criteria"),
    )

    def __init__(self, repository_inspector: RepositoryContextInspector | None = None) -> None:
        self.repository_inspector = repository_inspector or RepositoryContextInspector()

    def decompose(
        self,
        request: str | Mapping[str, Any] | RunIntent,
        *,
        done_when: Sequence[str] | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> Decomposition:
        goal, defaults, request_config, repository = self._request_parts(request)
        merged_config = dict(request_config)
        merged_config.update(dict(config or {}))
        default_done = tuple(str(item) for item in (done_when or defaults or (f"{goal} is evidenced",)))
        repository_context = self.repository_inspector.inspect(repository)

        configured = self._configured_domains(merged_config)
        ambiguous, clarification_reasons = self._ambiguity(goal, merged_config, configured is not None)
        if configured is not None:
            domains = self._from_configured(configured, goal=goal, default_done=default_done)
            return Decomposition(
                tuple(domains),
                "atomic" if len(domains) == 1 else "outcome-domain",
                "configured",
                True,
                repository_context,
                ambiguous,
                clarification_reasons,
            )

        fragments = self._parse_goal(goal)
        domains = self._from_fragments(fragments, default_done=default_done)
        domains = self._apply_repository_context(domains, goal=goal, default_done=default_done, context=repository_context)
        return Decomposition(
            tuple(domains),
            "atomic" if len(domains) == 1 else "outcome-domain",
            "natural-language",
            False,
            repository_context,
            ambiguous,
            clarification_reasons,
        )

    @classmethod
    def _ambiguity(
        cls,
        goal: str,
        config: Mapping[str, Any],
        has_explicit_domains: bool,
    ) -> tuple[bool, tuple[str, ...]]:
        """Classify only expansion-relevant ambiguity, not ordinary prose.

        This is deliberately conservative.  A compiler can safely keep an
        ordinary, concrete atomic goal on the historical eager path, while an
        explicit unknown/choice or a caller-declared clarification requirement
        must stop before downstream GSD phases are materialized.
        """

        reasons: list[str] = []
        for key in ("ambiguous", "goal_ambiguous", "clarification_required", "requires_clarification"):
            if config.get(key) is True:
                reasons.append(f"config:{key}")
        if config.get("clarification_questions"):
            reasons.append("config:clarification_questions")
        lowered = goal.lower()
        for pattern, reason in cls._AMBIGUITY_PATTERNS:
            if re.search(pattern, lowered, flags=re.I) and reason not in reasons:
                reasons.append(reason)
        # A generic object is ambiguous unless the caller supplied explicit
        # outcome domains.  Keep "complete product" compatible with the
        # existing atomic GSD recipe while treating "build the product" as a
        # clarify-first request.
        if not has_explicit_domains and not re.search(
            r"\b(?:complete|entire|whole|full)\s+(?:product|platform|system|stack)\b",
            lowered,
        ):
            if re.search(
                r"\b(?:build|create|make|do|fix|improve)\s+(?:(?:the|a|an)\s+)?"
                r"(?:(?:[a-z0-9_-]+)\s+){0,2}(?:product|system|platform|thing|solution|app|application|service|tool|software)\b",
                lowered,
            ):
                reasons.append("goal-generic-object")
        return bool(reasons), tuple(dict.fromkeys(reasons))

    def validate_decomposition(
        self,
        value: Any,
        *,
        request: RunIntent | Mapping[str, Any] | str | None = None,
        done_when: Sequence[str] | None = None,
    ) -> Decomposition:
        """Re-run custom semantic output through the deterministic validator.

        Semantic decomposers are pure proposal providers.  They may return a
        ``Decomposition``, an object carrying ``domains``, or a sequence of
        domain mappings, but they never receive a Store and never own graph
        validation.  IDs, dependency references, ownership, and cycles are
        normalized here before a Workflow Pack can consume the result.
        """

        raw_value = value
        if isinstance(value, Decomposition):
            raw_domains: Sequence[Any] = value.domains
            strategy = value.strategy
            source = value.source
            explicit = value.explicit
            repository_context = value.repository_context
            ambiguous = value.ambiguous
            clarification_reasons = value.clarification_reasons
        elif isinstance(value, Mapping) and "domains" in value:
            raw_domains = value.get("domains") or ()
            strategy = str(value.get("strategy") or ("atomic" if len(raw_domains) == 1 else "outcome-domain"))
            source = str(value.get("source") or "semantic")
            explicit = bool(value.get("explicit", True))
            repository_context = value.get("repository_context", {})
            ambiguous = bool(value.get("ambiguous", value.get("clarification_required", False)))
            clarification_reasons = _as_tuple(value.get("clarification_reasons", ()))
        elif hasattr(value, "domains"):
            raw_domains = getattr(value, "domains") or ()
            strategy = str(getattr(value, "strategy", "semantic"))
            source = str(getattr(value, "source", "semantic"))
            explicit = bool(getattr(value, "explicit", True))
            repository_context = getattr(value, "repository_context", {})
            ambiguous = bool(getattr(value, "ambiguous", getattr(value, "clarification_required", False)))
            clarification_reasons = _as_tuple(getattr(value, "clarification_reasons", ()))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            raw_domains = value
            strategy = "atomic" if len(value) == 1 else "semantic"
            source = "semantic"
            explicit = True
            repository_context = {}
            ambiguous = False
            clarification_reasons = ()
        else:
            raise TypeError("semantic decomposer must return a Decomposition or a sequence of domains")

        default_done = tuple(str(item) for item in (done_when or self._request_parts(request)[1] or ()))
        if not default_done:
            goal = self._request_parts(request)[0] if request is not None else "requested outcome"
            default_done = (f"{goal} is evidenced",)
        if isinstance(raw_domains, Mapping):
            raw_domains = self._configured_domains({"domains": raw_domains}) or ()
        normalized_input: list[Mapping[str, Any]] = []
        for index, item in enumerate(raw_domains):
            if isinstance(item, OutcomeDomain):
                normalized_input.append(item.to_dict())
            elif isinstance(item, Mapping):
                normalized_input.append(dict(item))
            else:
                raise ValueError(f"semantic domain {index} must be an OutcomeDomain or object")
        domains = self._materialize_domains(normalized_input, default_done=default_done)
        if strategy not in {"atomic", "outcome-domain", "semantic"}:
            strategy = "semantic"
        if not repository_context:
            request_repository = self._request_parts(request)[3] if request is not None else None
            repository_context = self.repository_inspector.inspect(request_repository)
        if isinstance(raw_value, Mapping):
            ambiguous = bool(raw_value.get("ambiguous", raw_value.get("clarification_required", ambiguous)))
        if request is not None:
            request_goal, _, request_config, _ = self._request_parts(request)
            inferred_ambiguous, inferred_reasons = self._ambiguity(
                request_goal,
                request_config,
                self._configured_domains(request_config) is not None or bool(raw_domains),
            )
            ambiguous = bool(ambiguous or inferred_ambiguous)
            clarification_reasons = tuple(dict.fromkeys((*clarification_reasons, *inferred_reasons)))
        return Decomposition(
            tuple(domains),
            strategy,
            source,
            explicit,
            repository_context,
            ambiguous,
            clarification_reasons,
        )

    @staticmethod
    def _request_parts(request: str | Mapping[str, Any] | RunIntent) -> tuple[str, tuple[str, ...], Mapping[str, Any], Any]:
        if isinstance(request, RunIntent):
            return request.goal, tuple(request.done_when), request.pack.config, request.repository
        if isinstance(request, str):
            return _text(request, "goal"), (), {}, None
        if not isinstance(request, Mapping):
            raise TypeError("TaskDecomposer expects a goal, request mapping, or RunIntent")
        goal = _text(request.get("goal") or request.get("idea") or request.get("objective"), "goal")
        pack = request.get("pack")
        pack_config = pack.get("config", {}) if isinstance(pack, Mapping) else {}
        config = dict(request.get("pack_config", {}) or {})
        config.update(dict(pack_config or {}))
        for key in ("domains", "outcome_domains", "tasks"):
            if key in request and key not in config:
                config[key] = request[key]
        raw_done = request.get("done_when", ())
        return goal, _as_tuple(raw_done), config, request.get("repository")

    @staticmethod
    def _configured_domains(config: Mapping[str, Any]) -> Sequence[Any] | None:
        for key in ("outcome_domains", "domains", "tasks"):
            value = config.get(key)
            if value is None:
                continue
            if isinstance(value, Mapping):
                return tuple(
                    {**dict(item), "id": str(item_id)} if isinstance(item, Mapping) else {"id": str(item_id), "outcome": str(item)}
                    for item_id, item in value.items()
                )
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return value
            raise ValueError(f"pack.config.{key} must be an array or object")
        return None

    def _from_configured(self, configured: Sequence[Any], *, goal: str, default_done: tuple[str, ...]) -> list[OutcomeDomain]:
        raw_domains: list[dict[str, Any]] = []
        for index, raw in enumerate(configured):
            if not isinstance(raw, Mapping):
                raise ValueError(f"configured outcome domain {index} must be an object")
            value = dict(raw)
            outcome = _text(value.get("outcome") or value.get("title") or value.get("description") or goal, f"domain {index} outcome")
            raw_domains.append({
                "id": str(value.get("id") or value.get("task_id") or f"domain-{_slug(outcome)}"),
                "outcome": outcome,
                "done_when": value.get("done_when", default_done),
                "verification_specs": value.get("verification_specs", ()),
                "dependencies": value.get("dependencies", value.get("depends_on", value.get("after", ()))),
                "ownership": value.get("ownership", ()),
                "checks": value.get("checks", ()),
                "exports": value.get("exports", ()),
                "priority": value.get("priority", 0),
                "resource_envelope": value.get("resource_envelope", {}),
                "work_unit_resource_envelope": value.get("work_unit_resource_envelope", {}),
                "metadata": value.get("metadata", {}),
            })
        return self._materialize_domains(raw_domains, default_done=default_done)

    def _parse_goal(self, goal: str) -> list[_Fragment]:
        # Strong boundaries first.  A plain period is included so a short
        # multi-sentence goal does not remain one accidental mega-domain.
        chunks = [chunk for chunk in re.split(r"(?:\r?\n+|[;；。]\s*)", goal) if chunk.strip()]
        if not chunks:
            chunks = [goal]
        fragments: list[_Fragment] = []
        previous_ref: str | None = None
        for chunk in chunks:
            chunk = chunk.strip()
            sequential_prefix = bool(re.match(r"^(?:then|next|finally)\b", chunk, flags=re.I))
            chunk = _clean_clause(chunk)
            sequence_parts = [part for part in re.split(r"\s+(?:,\s*)?(?:and\s+)?then\s+", chunk, flags=re.I) if part.strip()]
            if not sequence_parts:
                sequence_parts = [chunk]
            for part_index, part in enumerate(sequence_parts):
                clause_fragments = self._expand_dependency_clause(part, fragments)
                if not clause_fragments:
                    continue
                add_sequence_dependency = sequential_prefix or part_index > 0
                split_fragments: list[_Fragment] = []
                for clause_fragment in clause_fragments:
                    pieces = self._split_conjunction(clause_fragment.text)
                    for piece in pieces:
                        refs = list(clause_fragment.dependency_refs)
                        if add_sequence_dependency and previous_ref:
                            refs.append(previous_ref)
                        split_fragments.append(_Fragment(_clean_clause(piece), tuple(dict.fromkeys(refs))))
                split_fragments = [item for item in split_fragments if item.text]
                fragments.extend(split_fragments)
                if split_fragments:
                    previous_ref = split_fragments[-1].text
        return fragments or [_Fragment(_clean_clause(goal))]

    def _expand_dependency_clause(self, value: str, existing: Sequence[_Fragment]) -> list[_Fragment]:
        text = _clean_clause(value)
        dependency_match = re.match(r"^(?P<main>.+?)\s+(?P<link>after|once|following|depends on|requires)\s+(?P<dependency>.+)$", text, flags=re.I)
        if dependency_match:
            main = _clean_clause(dependency_match.group("main"))
            dependency = _clean_dependency(dependency_match.group("dependency"))
            if not main or not dependency:
                return [_Fragment(text)]
            if self._find_reference(dependency, existing) is None:
                prerequisite = _Fragment(dependency)
                return [prerequisite, _Fragment(main, (dependency,))]
            return [_Fragment(main, (dependency,))]

        before_match = re.match(r"^(?P<prerequisite>.+?)\s+before\s+(?P<main>.+)$", text, flags=re.I)
        if before_match:
            prerequisite = _clean_clause(before_match.group("prerequisite"))
            main = _clean_clause(before_match.group("main"))
            return [_Fragment(prerequisite), _Fragment(main, (prerequisite,))]
        return [_Fragment(text)]

    @staticmethod
    def _split_conjunction(value: str) -> list[str]:
        comma_candidates = re.split(r",\s*(?:and\s+)?", value)
        if len(comma_candidates) > 1 and all(_looks_like_domain(item) for item in comma_candidates):
            return TaskDecomposer._restore_leading_verb(comma_candidates)
        candidates = re.split(r"\s+(?:and|plus|&)\s+", value, flags=re.I)
        if len(candidates) > 1 and all(_looks_like_domain(item) for item in candidates):
            return TaskDecomposer._restore_leading_verb(candidates)
        return [value]

    @staticmethod
    def _restore_leading_verb(parts: Sequence[str]) -> list[str]:
        """Keep ``build``/``create`` attached to elided conjunction items."""

        values = [part.strip() for part in parts]
        match = re.match(r"^(?P<verb>[A-Za-z]+)\b", values[0])
        if match and match.group("verb").lower() in _LEADING_VERBS:
            verb = match.group("verb")
            for index in range(1, len(values)):
                if not re.match(r"^(?:[A-Za-z]+)\b", values[index]) or values[index].split()[0].lower() not in _LEADING_VERBS:
                    values[index] = f"{verb} {values[index]}"
        return values

    def _from_fragments(self, fragments: Sequence[_Fragment], *, default_done: tuple[str, ...]) -> list[OutcomeDomain]:
        raw_domains: list[dict[str, Any]] = []
        for index, fragment in enumerate(fragments):
            raw_domains.append({
                "id": "deliver" if len(fragments) == 1 else f"domain-{_slug(fragment.text)}",
                "outcome": fragment.text,
                "done_when": default_done,
                "verification_specs": (),
                "dependencies": fragment.dependency_refs,
                "ownership": (),
                "checks": default_done,
                "metadata": {"source_index": index, "source_text": fragment.text},
            })
        return self._materialize_domains(raw_domains, default_done=default_done)

    def _apply_repository_context(
        self,
        domains: Sequence[OutcomeDomain],
        *,
        goal: str,
        default_done: tuple[str, ...],
        context: Mapping[str, Any],
    ) -> list[OutcomeDomain]:
        surfaces = tuple(item for item in context.get("surfaces", ()) if isinstance(item, Mapping))
        if not surfaces:
            return list(domains)
        if self._is_broad_goal(goal) and len(domains) == 1 and len(surfaces) > 1:
            raw_domains = [
                {
                    "id": f"domain-{surface['id']}",
                    "outcome": f"{goal} ({surface['name']})",
                    "done_when": default_done,
                    "verification_specs": (),
                    "ownership": (str(surface["ownership"]),),
                    "checks": default_done,
                    "metadata": {
                        "repository_surface": surface["id"],
                        "repository_evidence": dict(surface.get("evidence", {})),
                    },
                }
                for surface in surfaces
            ]
            return self._materialize_domains(raw_domains, default_done=default_done)
        return [self._attach_surface_ownership(domain, surfaces) for domain in domains]

    @staticmethod
    def _is_broad_goal(goal: str) -> bool:
        lowered = goal.lower()
        has_repository_reference = any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in _REPOSITORY_WORDS)
        has_quantifier = any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in _BROAD_QUANTIFIERS)
        has_broad_action = any(re.search(rf"\b{re.escape(phrase)}\b", lowered) for phrase in _BROAD_ACTIONS)
        explicit_whole_product = re.search(r"\b(entire|whole|full)\s+(?:product|platform|system|stack)\b", lowered)
        return bool((has_repository_reference and (has_quantifier or has_broad_action)) or explicit_whole_product)

    @classmethod
    def _attach_surface_ownership(
        cls,
        domain: OutcomeDomain,
        surfaces: Sequence[Mapping[str, Any]],
    ) -> OutcomeDomain:
        matches = [surface for surface in surfaces if cls._surface_matches(domain.outcome, surface)]
        if not matches:
            return domain
        ownership = list(domain.ownership)
        for surface in matches:
            path = str(surface.get("ownership") or "")
            if path and path not in ownership:
                ownership.append(path)
        metadata = dict(domain.metadata)
        metadata["repository_surfaces"] = [str(surface.get("id")) for surface in matches]
        metadata["repository_evidence"] = [dict(surface.get("evidence", {})) for surface in matches]
        return replace(domain, ownership=tuple(ownership), metadata=metadata)

    @staticmethod
    def _surface_matches(outcome: str, surface: Mapping[str, Any]) -> bool:
        lowered = outcome.lower()
        surface_name = str(surface.get("name", "")).lower()
        path_parts = {part for part in re.findall(r"[a-z0-9]+", str(surface.get("path", "")).lower())}
        outcome_words = set(re.findall(r"[a-z0-9]+", lowered))
        if surface_name and surface_name in outcome_words:
            return True
        if outcome_words & path_parts:
            return True
        aliases = {
            "api": {"backend", "server", "service"},
            "dashboard": {"frontend", "web", "ui", "client"},
            "ui": {"frontend", "web", "client"},
            "web": {"frontend", "ui", "client"},
        }
        return bool(any(word in outcome_words and surface_name in names for word, names in aliases.items()))

    def _materialize_domains(self, raw_domains: Sequence[Mapping[str, Any]], *, default_done: tuple[str, ...]) -> list[OutcomeDomain]:
        used: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for raw in raw_domains:
            candidate = _slug(str(raw.get("id") or raw.get("outcome") or "outcome"))
            # Explicit ids should remain recognizable, but still need to obey
            # the opaque-id contract when a caller supplied spaces/punctuation.
            if str(raw.get("id") or "").strip():
                candidate = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(raw["id"]).strip()).strip("-") or candidate
            if not candidate[0].isalpha():
                candidate = f"domain-{candidate}"
            base = candidate[:120]
            candidate = base
            suffix = 2
            while candidate in used:
                candidate = f"{base[: max(1, 120 - len(str(suffix)) - 1)]}-{suffix}"
                suffix += 1
            used.add(candidate)
            ownership = raw.get("ownership", ())
            if isinstance(ownership, Mapping):
                ownership = ownership.get("paths", ())
            done = _as_tuple(raw.get("done_when")) or default_done
            dependencies = raw.get("dependencies", ())
            if isinstance(dependencies, Mapping):
                dependencies = (dependencies,)
            if isinstance(dependencies, str):
                dependencies = (dependencies,)
            dependency_refs: list[str] = []
            dependency_exports: dict[str, tuple[str, ...]] = {}
            for dependency in dependencies or ():
                if isinstance(dependency, Mapping):
                    reference = dependency.get("id") or dependency.get("task_id") or dependency.get("task_ref") or dependency.get("depends_on") or dependency.get("name")
                    exports = _as_tuple(dependency.get("exports"))
                else:
                    reference = dependency
                    exports = ()
                if reference is None:
                    raise ValueError(f"domain {candidate} has a dependency without a target")
                ref_text = str(reference).removeprefix("task://")
                dependency_refs.append(ref_text)
                if exports:
                    dependency_exports[ref_text] = exports
            normalized.append({
                "id": candidate,
                "outcome": _text(str(raw.get("outcome") or ""), f"domain {candidate} outcome"),
                "done_when": done,
                "verification_specs": verification_specs(raw.get("verification_specs", ())),
                "dependency_refs": dependency_refs,
                "dependency_exports": dependency_exports,
                "ownership": tuple(str(item) for item in ownership or ()),
                "checks": _as_tuple(raw.get("checks")) or done,
                "exports": _as_tuple(raw.get("exports")),
                "priority": int(raw.get("priority", 0)),
                "resource_envelope": dict(raw.get("resource_envelope", {}) or {}),
                "work_unit_resource_envelope": dict(raw.get("work_unit_resource_envelope", {}) or {}),
                "metadata": dict(raw.get("metadata", {}) or {}),
            })

        for item in normalized:
            resolved: list[str] = []
            resolved_exports: dict[str, tuple[str, ...]] = {}
            for reference in item["dependency_refs"]:
                target = self._find_reference(reference, normalized)
                if target is None:
                    raise ValueError(f"domain {item['id']} dependency {reference!r} does not name an outcome domain")
                if target == item["id"]:
                    raise ValueError(f"domain {item['id']} cannot depend on itself")
                if target not in resolved:
                    resolved.append(target)
                exports = item["dependency_exports"].get(reference, ())
                if exports:
                    resolved_exports[target] = exports
            item["dependencies"] = resolved
            item["dependency_exports"] = resolved_exports

        self._assert_acyclic(normalized)
        return [
            OutcomeDomain(
                id=item["id"],
                outcome=item["outcome"],
                done_when=item["done_when"],
                verification_specs=item["verification_specs"],
                dependencies=tuple(item["dependencies"]),
                dependency_exports=item["dependency_exports"],
                ownership=item["ownership"],
                checks=item["checks"],
                exports=item["exports"],
                priority=item["priority"],
                resource_envelope=item["resource_envelope"],
                work_unit_resource_envelope=item["work_unit_resource_envelope"],
                metadata=item["metadata"],
            )
            for item in normalized
        ]

    @staticmethod
    def _find_reference(reference: str, candidates: Sequence[_Fragment] | Sequence[Mapping[str, Any]]) -> str | None:
        ref = _clean_dependency(reference).lower()
        ref_slug = _slug(ref)
        scored: list[tuple[int, str]] = []
        for candidate in candidates:
            if isinstance(candidate, _Fragment):
                candidate_id = candidate.text
                candidate_text = candidate.text
            else:
                candidate_id = str(candidate["id"])
                candidate_text = str(candidate.get("outcome", candidate_id))
            lowered = candidate_text.lower()
            candidate_slug = _slug(candidate_text)
            score = 0
            candidate_id_match = ref == candidate_id.lower()
            if isinstance(candidate, Mapping):
                candidate_id_match = candidate_id_match or ref == str(candidate.get("id", "")).lower()
            if candidate_id_match:
                score = 100
            elif ref_slug == candidate_slug or ref_slug == _slug(candidate_id):
                score = 80
            else:
                ref_words = set(re.findall(r"[a-z0-9]+", ref)) - _STOP_WORDS
                candidate_words = set(re.findall(r"[a-z0-9]+", lowered)) - _STOP_WORDS
                overlap = len(ref_words & candidate_words)
                if overlap:
                    score = overlap * 10
            if score:
                scored.append((score, candidate_id))
        if not scored:
            return None
        return max(scored, key=lambda item: item[0])[1]

    @classmethod
    def _assert_acyclic(cls, domains: Sequence[Mapping[str, Any]]) -> None:
        edges = {str(item["id"]): tuple(str(dep) for dep in item.get("dependencies", ())) for item in domains}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("outcome-domain dependencies contain a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in edges.get(node, ()):
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in edges:
            visit(node)


class SemanticDecomposer(Protocol):
    """Pure semantic proposal boundary used by ``GoalCompiler``.

    Implementations receive the typed ``RunIntent`` only.  They may propose
    domain records, but they do not receive a Store, scheduler, host, or
    persistence callback.  The deterministic ``TaskDecomposer`` validator
    owns the graph invariants after this method returns.
    """

    def decompose(
        self,
        request: RunIntent,
        *,
        done_when: Sequence[str] | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> Any: ...


class GoalCompiler:
    """Run deterministic validation around an optional semantic decomposer."""

    version = "2.1"

    def __init__(
        self,
        decomposer: TaskDecomposer | None = None,
        *,
        semantic_decomposer: SemanticDecomposer | Any | None = None,
    ) -> None:
        # ``decomposer`` remains the compatibility name for the deterministic
        # validator.  A semantic provider is optional and intentionally
        # separate so it cannot replace validation or gain Store access.
        self.decomposer = decomposer or TaskDecomposer()
        self.semantic_decomposer = semantic_decomposer

    @staticmethod
    def _call_semantic_decomposer(
        decomposer: Any,
        run_intent: RunIntent,
    ) -> Any:
        method = getattr(decomposer, "decompose", None)
        if not callable(method):
            if callable(decomposer):
                method = decomposer
            else:
                raise TypeError("semantic_decomposer must be callable or expose decompose()")
        # Do not pass a Store-shaped positional/keyword argument.  The only
        # input crossing this boundary is the immutable RunIntent plus its
        # pack config and done_when values.
        try:
            return method(
                run_intent,
                done_when=run_intent.done_when,
                config=run_intent.pack.config,
            )
        except TypeError as exc:
            # Small third-party providers commonly accept just the typed
            # request.  Retry only for a signature mismatch; a provider's
            # internal TypeError must not be silently rewritten.
            message = str(exc)
            if not any(token in message for token in ("unexpected keyword", "positional argument", "keyword-only")):
                raise
            return method(run_intent)

    def compile(self, run_intent: RunIntent, workflow_pack: Any) -> Any:
        compile_domains = getattr(workflow_pack, "compile_domains", None)
        if callable(compile_domains):
            if self.semantic_decomposer is None:
                decomposition = self.decomposer.decompose(run_intent)
            else:
                proposed = self._call_semantic_decomposer(self.semantic_decomposer, run_intent)
                decomposition = self.decomposer.validate_decomposition(
                    proposed,
                    request=run_intent,
                    done_when=run_intent.done_when,
                )
            return compile_domains(run_intent, decomposition.domains, decomposition=decomposition)
        # Research-route and third-party compatibility Packs retain their own
        # input compiler until they opt into outcome-domain compilation.
        return workflow_pack.compile_goal(run_intent)

    compile_goal = compile


__all__ = [
    "Decomposition",
    "GoalCompiler",
    "OutcomeDomain",
    "RepositoryContextInspector",
    "SemanticDecomposer",
    "TaskDecomposer",
]
