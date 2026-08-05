"""Read-only importer for legacy development plans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..domain import RunIntent
from ..packs.delivery import DeliveryPack
from ..packs.base import TaskGraph
from .common import CompatibilityReport, digest
from .resources import LegacyResourceTranslator


def _load(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    path = Path(value)
    return json.loads(path.read_text(encoding="utf-8"))


def _repository(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    mode = str(raw.get("mode", "projectless"))
    if mode not in {"existing", "greenfield", "multi-repository", "projectless"}:
        mode = "projectless"
    roots = []
    for item in raw.get("roots", ()):
        if not isinstance(item, Mapping):
            continue
        dirty = str(item.get("dirty_state", "unknown"))
        roots.append({"path": str(item.get("path", "unknown")), "git": bool(item.get("git", False)), "branch": item.get("branch"), "head": item.get("head"), "dirty_state": dirty if dirty in {"clean", "dirty", "unknown"} else "unknown"})
    return {"mode": mode, "roots": roots, "protected_paths": tuple(str(item) for item in raw.get("protected_paths", ()))}


class LegacyPlanImportAPI:
    """Parse/validate/translate legacy plans without writing either state format."""

    def parse(self, value: str | Path | Mapping[str, Any]) -> Mapping[str, Any]:
        raw = _load(value)
        if not isinstance(raw, dict):
            raise ValueError("legacy plan must be a JSON object")
        return raw

    def validate(self, value: str | Path | Mapping[str, Any]) -> CompatibilityReport:
        raw = self.parse(value)
        losses: list[str] = []
        unknowns = [str(key) for key in raw if key not in {"schema_version", "plan_id", "title", "objective", "completion_standard", "stop_boundary", "repository", "authorizations", "resource_policy", "tasks", "assumptions", "unknowns", "execution_style", "mode"}]
        if not raw.get("objective"):
            losses.append("legacy plan has no objective; title will be used as goal")
        if not raw.get("completion_standard"):
            losses.append("legacy completion_standard missing; importer requires one derived done_when")
        return CompatibilityReport("legacy-plan", digest(raw), tuple(losses), tuple(unknowns), (), True)

    def translate(self, value: str | Path | Mapping[str, Any]) -> "LegacyPlanImportResult":
        raw = self.parse(value)
        validation = self.validate(raw)
        repository = _repository(raw.get("repository"))
        authorizations = raw.get("authorizations") or {}
        resource = LegacyResourceTranslator().translate(raw.get("resource_policy", {}))
        done_when = tuple(str(item) for item in raw.get("completion_standard", ()) if str(item).strip()) or ("the requested goal is evidenced",)
        tasks = []
        for item in raw.get("tasks", ()):
            if not isinstance(item, Mapping):
                continue
            tasks.append({
                "id": item.get("id"),
                "outcome": item.get("description") or item.get("title") or raw.get("objective") or raw.get("title"),
                "done_when": item.get("verification") or done_when,
                "dependencies": item.get("dependencies", ()),
                "ownership": item.get("ownership", {}).get("paths", ()) if isinstance(item.get("ownership"), Mapping) else item.get("ownership", ()),
                "checks": item.get("verification", done_when),
            })
        config = {"tasks": tasks} if tasks else {}
        intent = RunIntent(
            intent_id=str(raw.get("plan_id") or "legacy-plan"),
            goal=str(raw.get("objective") or raw.get("title") or "Imported legacy plan"),
            done_when=done_when,
            repository=repository,
            authorization_intent={
                "implementation_writes": bool(authorizations.get("implementation_writes", True)),
                "git_operations": bool(authorizations.get("git_operations", False)),
                "destructive_operations": bool(authorizations.get("destructive_operations", False)),
                "live_external_mutation": bool(authorizations.get("live_external_mutation", False)),
                "publication": bool(authorizations.get("publication", False)),
            },
            resource_envelope=resource.envelope,
            pack={"id": "delivery", "version": DeliveryPack.version, "config": config},
            constraints=tuple(str(item) for item in (raw.get("assumptions", ()) + raw.get("unknowns", ()) if isinstance(raw.get("assumptions", ()), list) and isinstance(raw.get("unknowns", ()), list) else ())),
        )
        graph = DeliveryPack().compile_goal(intent)
        report = CompatibilityReport(validation.source_kind, validation.source_digest, validation.losses + resource.report.losses, validation.unknowns, validation.warnings + resource.report.warnings, True, resource.report.model_evidence)
        return LegacyPlanImportResult(intent, graph, report)

    def import_read_only(self, value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
        """Return a stable migration envelope for callers that expect a mapping."""
        result = self.translate(value)
        payload = result.to_dict()
        payload.update({
            "source_format": "legacy-plan",
            "mode": "read-only",
            "write_back": False,
            "legacy_writeback": False,
            "vnext_objects_created": True,
            "imported_task_id": str(result.task_graph.tasks[0].id) if result.task_graph.tasks else None,
        })
        return payload

    import_plan = import_read_only


class LegacyPlanImportResult:
    def __init__(self, intent: RunIntent, task_graph: TaskGraph, report: CompatibilityReport) -> None:
        self.intent = intent
        self.task_graph = task_graph
        self.report = report

    def to_dict(self) -> dict[str, Any]:
        return {"run_intent": self.intent.to_dict(), "task_graph": self.task_graph.to_dict(), "report": self.report.to_dict()}

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


LegacyPlanImporter = LegacyPlanImportAPI

__all__ = ["LegacyPlanImportAPI", "LegacyPlanImportResult", "LegacyPlanImporter"]
