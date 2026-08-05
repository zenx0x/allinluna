"""Canonical bootstrap supplied to an independently running Task Lane.

The public top-level-thread prompt is an execution boundary, not a summary of a
Task outcome.  This object gives that thread enough stable identity to reopen
the same Store and load its Task, Contract, WorkGraph, and Context itself.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


LANE_BOOTSTRAP_PROTOCOL = "lane-bootstrap/v1"
LANE_RESPONSE_CONTRACT = "lane-handoff/v1"

DEFAULT_LOCAL_CAPABILITIES = (
    "read",
    "write",
    "execute-local",
    "delegate-recursive",
    "report",
)
DEFAULT_FORBIDDEN_GLOBAL_CAPABILITIES = (
    "create-top-level-task",
    "create-global-task",
    "modify-global-task",
    "global-scheduler",
    "global-coordinator",
)


class LaneBootstrapError(ValueError):
    """A lane bootstrap is malformed or does not match the persisted Store."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Mapping[str, Any]) -> str:
    """Hash TaskEnvelope material without the bootstrap's self-reference."""

    material = dict(value)
    extensions = material.get("extensions")
    if isinstance(extensions, Mapping):
        clean_extensions = dict(extensions)
        clean_extensions.pop("lane_bootstrap", None)
        clean_extensions.pop("task_envelope_digest", None)
        material["extensions"] = clean_extensions
    material.pop("task_envelope_digest", None)
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def _strings(value: Sequence[Any] | str | None, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    values = (value,) if isinstance(value, str) else value
    try:
        result = tuple(str(item).strip() for item in values if str(item).strip())
    except TypeError as exc:  # pragma: no cover - defensive protocol boundary
        raise LaneBootstrapError(f"{field} must be a sequence of capability names") from exc
    if len(set(result)) != len(result):
        raise LaneBootstrapError(f"{field} must not contain duplicates")
    return result


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LaneBootstrapError(f"{field} must be a non-empty string")
    return text


@dataclass(frozen=True, slots=True)
class LaneBootstrapEnvelope:
    """The complete ``lane-bootstrap/v1`` contract for a top-level Lane."""

    run_ref: str
    task_id: str
    task_ref: str
    lane_attempt_ref: str
    task_envelope_ref: str
    task_envelope_digest: str
    runtime_db: str
    contract_ref: str
    context_ref: str
    work_graph_ref: str
    workspace: str
    allowed_local_capabilities: tuple[str, ...] = DEFAULT_LOCAL_CAPABILITIES
    forbidden_global_capabilities: tuple[str, ...] = DEFAULT_FORBIDDEN_GLOBAL_CAPABILITIES
    response_contract: str = LANE_RESPONSE_CONTRACT
    protocol: str = LANE_BOOTSTRAP_PROTOCOL

    def __post_init__(self) -> None:
        for field in (
            "run_ref", "task_id", "task_ref", "lane_attempt_ref", "task_envelope_ref",
            "task_envelope_digest", "runtime_db", "contract_ref", "context_ref",
            "work_graph_ref", "workspace", "response_contract", "protocol",
        ):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        object.__setattr__(
            self, "allowed_local_capabilities",
            _strings(self.allowed_local_capabilities, field="allowed_local_capabilities"),
        )
        object.__setattr__(
            self, "forbidden_global_capabilities",
            _strings(self.forbidden_global_capabilities, field="forbidden_global_capabilities"),
        )
        if self.protocol != LANE_BOOTSTRAP_PROTOCOL:
            raise LaneBootstrapError(f"expected protocol {LANE_BOOTSTRAP_PROTOCOL}")
        if self.response_contract != LANE_RESPONSE_CONTRACT:
            raise LaneBootstrapError(f"expected response contract {LANE_RESPONSE_CONTRACT}")
        if not self.run_ref.startswith("run://"):
            raise LaneBootstrapError("run_ref must use run://")
        if self.task_ref != f"task://{self.task_id}":
            raise LaneBootstrapError("task_ref must identify task_id exactly")
        if not self.lane_attempt_ref.startswith("lane-attempt://"):
            raise LaneBootstrapError("lane_attempt_ref must use lane-attempt://")
        if not self.task_envelope_ref.startswith("task-envelope://"):
            raise LaneBootstrapError("task_envelope_ref must use task-envelope://")
        if len(self.task_envelope_digest) != 64 or any(char not in "0123456789abcdef" for char in self.task_envelope_digest):
            raise LaneBootstrapError("task_envelope_digest must be a lowercase SHA-256 digest")
        if not self.contract_ref.startswith("contract://task/"):
            raise LaneBootstrapError("contract_ref must use contract://task/")
        if not self.context_ref.startswith("context://"):
            raise LaneBootstrapError("context_ref must use context://")
        if not self.work_graph_ref.startswith("runtime-db://work-graph/"):
            raise LaneBootstrapError("work_graph_ref must use runtime-db://work-graph/")
        if set(self.allowed_local_capabilities).intersection(self.forbidden_global_capabilities):
            raise LaneBootstrapError("local and forbidden capabilities must not overlap")

    @property
    def run_id(self) -> str:
        return self.run_ref.removeprefix("run://")

    @property
    def runtime_path(self) -> Path:
        return Path(self.runtime_db)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "run_ref": self.run_ref,
            "task_id": self.task_id,
            "task_ref": self.task_ref,
            "lane_attempt_ref": self.lane_attempt_ref,
            "task_envelope_ref": self.task_envelope_ref,
            "task_envelope_digest": self.task_envelope_digest,
            "runtime_db": self.runtime_db,
            "contract_ref": self.contract_ref,
            "context_ref": self.context_ref,
            "work_graph_ref": self.work_graph_ref,
            "workspace": self.workspace,
            "allowed_local_capabilities": list(self.allowed_local_capabilities),
            "forbidden_global_capabilities": list(self.forbidden_global_capabilities),
            "response_contract": self.response_contract,
        }

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | "LaneBootstrapEnvelope") -> "LaneBootstrapEnvelope":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise LaneBootstrapError("lane bootstrap must be an object")
        return cls(
            protocol=value.get("protocol", LANE_BOOTSTRAP_PROTOCOL),
            run_ref=value.get("run_ref"),
            task_id=value.get("task_id") or str(value.get("task_ref") or "").removeprefix("task://"),
            task_ref=value.get("task_ref") or f"task://{value.get('task_id')}",
            lane_attempt_ref=value.get("lane_attempt_ref"),
            task_envelope_ref=value.get("task_envelope_ref"),
            task_envelope_digest=value.get("task_envelope_digest"),
            runtime_db=value.get("runtime_db"),
            contract_ref=value.get("contract_ref"),
            context_ref=value.get("context_ref"),
            work_graph_ref=value.get("work_graph_ref"),
            workspace=value.get("workspace"),
            allowed_local_capabilities=_strings(value.get("allowed_local_capabilities"), field="allowed_local_capabilities"),
            forbidden_global_capabilities=_strings(value.get("forbidden_global_capabilities"), field="forbidden_global_capabilities"),
            response_contract=value.get("response_contract", LANE_RESPONSE_CONTRACT),
        )

    @classmethod
    def for_task(
        cls,
        store: Any,
        task: Mapping[str, Any],
        task_envelope: Mapping[str, Any],
        *,
        workspace: str | None = None,
    ) -> "LaneBootstrapEnvelope":
        """Create the canonical child contract from already-persistable inputs."""

        task_id = str(task["id"])
        run_id = str(task["run_id"])
        resource = task.get("resource_envelope") if isinstance(task.get("resource_envelope"), Mapping) else {}
        run = store.get_run(run_id) or {}
        policy = run.get("policy") if isinstance(run.get("policy"), Mapping) else {}
        repository = policy.get("repository") if isinstance(policy.get("repository"), Mapping) else {}
        resolved_workspace = workspace or resource.get("workspace") or policy.get("workspace") or repository.get("workspace") or repository.get("root")
        if not resolved_workspace:
            resolved_workspace = str(Path.cwd())
        extension = task_envelope.get("extensions") if isinstance(task_envelope.get("extensions"), Mapping) else {}
        attempt_ref = str(task_envelope.get("lane_attempt_ref") or "")
        return cls(
            run_ref=f"run://{run_id}",
            task_id=task_id,
            task_ref=f"task://{task_id}",
            lane_attempt_ref=attempt_ref,
            task_envelope_ref=str(task_envelope["task_envelope_ref"]),
            task_envelope_digest=_digest(task_envelope),
            runtime_db=str(getattr(store, "path", "runtime.db")),
            contract_ref=str(task_envelope["contract_ref"]),
            context_ref=str(task_envelope["context_ref"]),
            work_graph_ref=str(extension.get("local_graph_ref") or f"runtime-db://work-graph/{task_id}"),
            workspace=str(resolved_workspace),
            allowed_local_capabilities=tuple(extension.get("allowed_local_capabilities") or DEFAULT_LOCAL_CAPABILITIES),
            forbidden_global_capabilities=tuple(extension.get("forbidden_global_capabilities") or DEFAULT_FORBIDDEN_GLOBAL_CAPABILITIES),
        )

    @classmethod
    def from_store(cls, store: Any, run_id: str, task_id: str) -> "LaneBootstrapEnvelope":
        """Reopen the exact bootstrap persisted with the top-level action."""

        task = store.get_task(str(task_id), run_id=str(run_id))
        if task is None:
            raise LaneBootstrapError(f"task {task_id!r} is unavailable in run {run_id!r}")
        row = store._fetchone(
            """SELECT action_json FROM dispatch_outbox
               WHERE run_id = ? AND target_type = 'task' AND target_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (str(run_id), str(task["id"])),
        )
        if row is None:
            raise LaneBootstrapError("no durable top-level dispatch action exists for this lane")
        try:
            action = json.loads(str(row.get("action_json") or "{}"))
        except (TypeError, json.JSONDecodeError) as exc:
            raise LaneBootstrapError("persisted top-level dispatch action is not valid JSON") from exc
        payload = action.get("payload") if isinstance(action, Mapping) else None
        bootstrap = payload.get("lane_bootstrap") if isinstance(payload, Mapping) else None
        envelope = payload.get("task_envelope") if isinstance(payload, Mapping) else None
        value = cls.from_value(bootstrap)
        if not isinstance(envelope, Mapping):
            raise LaneBootstrapError("persisted dispatch action has no task envelope")
        value.verify_task_envelope(envelope)
        value.validate_store(store)
        return value

    def verify_task_envelope(self, task_envelope: Mapping[str, Any]) -> None:
        if str(task_envelope.get("task_envelope_ref")) != self.task_envelope_ref:
            raise LaneBootstrapError("task_envelope_ref does not match the envelope")
        if _digest(task_envelope) != self.task_envelope_digest:
            raise LaneBootstrapError("task_envelope_digest does not match the envelope")

    def validate_store(self, store: Any) -> dict[str, Any]:
        """Load the complete parent-owned state before a Lane begins work."""

        run = store.get_run(self.run_id)
        if run is None:
            raise LaneBootstrapError(f"run {self.run_id!r} is unavailable in the runtime Store")
        task = store.get_task(self.task_id)
        if task is None or str(task.get("run_id")) != self.run_id:
            raise LaneBootstrapError("task is unavailable or belongs to another run")
        expected_contract = f"contract://task/{task['contract_id']}@{task['contract_version']}"
        if self.contract_ref != expected_contract:
            raise LaneBootstrapError("bootstrap contract_ref does not match the persisted task")
        contract = store.get_contract(str(task["contract_id"]), int(task["contract_version"]))
        if contract is None:
            raise LaneBootstrapError("persisted task contract is unavailable")
        expected_graph = f"runtime-db://work-graph/{task['id']}"
        if self.work_graph_ref != expected_graph:
            raise LaneBootstrapError("work_graph_ref does not match the persisted task")
        snapshot = store.lane_scheduler_snapshot(str(task["id"]))
        return {"run": run, "task": task, "contract": contract, "work_graph": snapshot}


def render_lane_bootstrap_prompt(*, outcome: str, bootstrap: LaneBootstrapEnvelope) -> str:
    """Render the public create-thread prompt without reducing it to outcome text."""

    payload = json.dumps(bootstrap.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
    return (
        "You are a Top-level All in Luna Task Lane, not a local subagent.\n\n"
        f"Objective:\n{outcome}\n\n"
        "This is the complete canonical LaneBootstrapEnvelope. Reopen this exact Runtime DB "
        "and load the Task, Contract, WorkGraph, and Context yourself before doing work.\n"
        "```json\n"
        f"{payload}\n"
        "```\n\n"
        "Start the Lane runtime with the same Store (for example: "
        "allinluna --db <runtime_db> lane start <run_id> <task_id>).\n"
        "Use LocalScheduler only for local WorkUnits. Only local WorkUnits may recursively use "
        "subagents. Do not create, replace, or mutate global Tasks or the GlobalScheduler. "
        "Return a lane-handoff/v1 response."
    )


__all__ = [
    "DEFAULT_FORBIDDEN_GLOBAL_CAPABILITIES",
    "DEFAULT_LOCAL_CAPABILITIES",
    "LANE_BOOTSTRAP_PROTOCOL",
    "LANE_RESPONSE_CONTRACT",
    "LaneBootstrapEnvelope",
    "LaneBootstrapError",
    "render_lane_bootstrap_prompt",
]
