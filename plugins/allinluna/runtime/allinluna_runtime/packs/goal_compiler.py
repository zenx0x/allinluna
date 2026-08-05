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
from dataclasses import dataclass, field
import re
from typing import Any

from ..domain import RunIntent


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


@dataclass(frozen=True)
class OutcomeDomain:
    """One global outcome domain, which becomes one top-level Task/Lane."""

    id: str
    outcome: str
    done_when: tuple[str, ...] = ()
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

    def __post_init__(self) -> None:
        if not self.domains:
            raise ValueError("goal decomposition must contain at least one outcome domain")
        if self.strategy not in {"atomic", "outcome-domain"}:
            raise ValueError(f"unknown decomposition strategy: {self.strategy}")

    def __len__(self) -> int:
        return len(self.domains)

    def __getitem__(self, index: int | slice) -> OutcomeDomain | tuple[OutcomeDomain, ...]:
        return self.domains[index]

    def __iter__(self) -> Iterator[OutcomeDomain]:
        return iter(self.domains)

    @property
    def parallel_domains(self) -> tuple[OutcomeDomain, ...]:
        return tuple(domain for domain in self.domains if not domain.dependencies)

    def to_dict(self) -> dict[str, Any]:
        edges = [
            {"from": dependency, "to": domain.id, "exports": list(domain.dependency_exports.get(dependency, ())) }
            for domain in self.domains
            for dependency in domain.dependencies
        ]
        return {
            "strategy": self.strategy,
            "source": self.source,
            "explicit": self.explicit,
            "domain_count": len(self.domains),
            "domains": [domain.to_dict() for domain in self.domains],
            "parallel_domain_ids": [domain.id for domain in self.parallel_domains],
            "edges": edges,
        }


@dataclass
class _Fragment:
    text: str
    dependency_refs: tuple[str, ...] = ()


class TaskDecomposer:
    """Deterministically decompose a goal into outcome domains.

    Conjunctions only split when both sides name recognizable outcome domains;
    this keeps ordinary atomic language such as ``build and verify the
    product`` as one Task.  Semicolons, list lines, ``then``, and explicit
    ``after``/``depends on`` clauses provide stronger boundaries.
    """

    version = "2.1"

    def decompose(
        self,
        request: str | Mapping[str, Any] | RunIntent,
        *,
        done_when: Sequence[str] | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> Decomposition:
        goal, defaults, request_config = self._request_parts(request)
        merged_config = dict(request_config)
        merged_config.update(dict(config or {}))
        default_done = tuple(str(item) for item in (done_when or defaults or (f"{goal} is evidenced",)))

        configured = self._configured_domains(merged_config)
        if configured is not None:
            domains = self._from_configured(configured, goal=goal, default_done=default_done)
            return Decomposition(tuple(domains), "atomic" if len(domains) == 1 else "outcome-domain", "configured", True)

        fragments = self._parse_goal(goal)
        domains = self._from_fragments(fragments, default_done=default_done)
        return Decomposition(tuple(domains), "atomic" if len(domains) == 1 else "outcome-domain", "natural-language", False)

    @staticmethod
    def _request_parts(request: str | Mapping[str, Any] | RunIntent) -> tuple[str, tuple[str, ...], Mapping[str, Any]]:
        if isinstance(request, RunIntent):
            return request.goal, tuple(request.done_when), request.pack.config
        if isinstance(request, str):
            return _text(request, "goal"), (), {}
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
        return goal, _as_tuple(raw_done), config

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
                "done_when": value.get("done_when", value.get("verification", default_done)),
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
                "dependencies": fragment.dependency_refs,
                "ownership": (),
                "checks": default_done,
                "metadata": {"source_index": index, "source_text": fragment.text},
            })
        return self._materialize_domains(raw_domains, default_done=default_done)

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


class GoalCompiler:
    """Run the shared goal/decomposition pass before invoking a Pack."""

    version = "2.1"

    def __init__(self, decomposer: TaskDecomposer | None = None) -> None:
        self.decomposer = decomposer or TaskDecomposer()

    def compile(self, run_intent: RunIntent, workflow_pack: Any) -> Any:
        compile_domains = getattr(workflow_pack, "compile_domains", None)
        if callable(compile_domains):
            decomposition = self.decomposer.decompose(run_intent)
            return compile_domains(run_intent, decomposition.domains, decomposition=decomposition)
        # Research-route and third-party compatibility Packs retain their own
        # input compiler until they opt into outcome-domain compilation.
        return workflow_pack.compile_goal(run_intent)

    compile_goal = compile


__all__ = ["Decomposition", "GoalCompiler", "OutcomeDomain", "TaskDecomposer"]
