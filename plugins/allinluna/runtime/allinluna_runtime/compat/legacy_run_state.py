"""Read-only importer for legacy run-state snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..domain import RunIntent
from .common import CompatibilityReport, digest
from .legacy_plan import LegacyPlanImportAPI


def _load(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return json.loads(Path(value).read_text(encoding="utf-8"))


class LegacyRunStateImportAPI:
    """Import a snapshot for inspection/recovery planning; never update it."""

    STATUS_MAP = {"planned": "created", "running": "active", "paused": "paused", "blocked": "blocked", "completed": "completed", "failed": "aborted"}

    def parse(self, value: str | Path | Mapping[str, Any]) -> Mapping[str, Any]:
        raw = _load(value)
        if not isinstance(raw, dict):
            raise ValueError("legacy run state must be a JSON object")
        return raw

    def validate(self, value: str | Path | Mapping[str, Any]) -> CompatibilityReport:
        raw = self.parse(value)
        unknowns = [str(key) for key in raw if key not in {"schema_version", "run_id", "plan_id", "goal", "completion_standard", "status", "tasks", "repository", "resource_policy", "profile", "authorizations"}]
        losses = ["legacy run state is a projection, not an authoritative vNext source"]
        return CompatibilityReport("legacy-run-state", digest(raw), tuple(losses), tuple(unknowns), (), True)

    def translate(self, value: str | Path | Mapping[str, Any]) -> "LegacyRunStateImportResult":
        raw = self.parse(value)
        report = self.validate(raw)
        plan = {
            "plan_id": raw.get("plan_id") or raw.get("run_id") or "legacy-run",
            "objective": raw.get("goal") or "Imported legacy run state",
            "title": raw.get("goal") or "Imported legacy run state",
            "completion_standard": raw.get("completion_standard") or ("the imported completion evidence is reviewed",),
            "repository": raw.get("repository") or {"mode": "projectless", "roots": (), "protected_paths": ()},
            "authorizations": raw.get("authorizations") or {},
            "resource_policy": raw.get("resource_policy") or {"profile": raw.get("profile", "balanced")},
            "tasks": raw.get("tasks") or (),
        }
        translated = LegacyPlanImportAPI().translate(plan)
        mapped = self.STATUS_MAP.get(str(raw.get("status", "planned")), "blocked")
        merged = CompatibilityReport(report.source_kind, report.source_digest, report.losses + translated.report.losses, report.unknowns + translated.report.unknowns, report.warnings + translated.report.warnings, True, translated.report.model_evidence)
        return LegacyRunStateImportResult(raw.get("run_id") or "legacy-run", mapped, translated.intent, translated.task_graph, merged)

    import_state = translate

    import_run_state = translate


class LegacyRunStateImportResult:
    def __init__(self, run_id: str, mapped_status: str, intent: RunIntent, task_graph: Any, report: CompatibilityReport) -> None:
        self.run_id = str(run_id)
        self.mapped_status = mapped_status
        self.intent = intent
        self.task_graph = task_graph
        self.report = report

    def to_dict(self) -> dict[str, Any]:
        return {"legacy_run_id": self.run_id, "mapped_status": self.mapped_status, "run_intent": self.intent.to_dict(), "task_graph": self.task_graph.to_dict(), "report": self.report.to_dict()}

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


LegacyRunStateImporter = LegacyRunStateImportAPI

__all__ = ["LegacyRunStateImportAPI", "LegacyRunStateImportResult", "LegacyRunStateImporter"]
