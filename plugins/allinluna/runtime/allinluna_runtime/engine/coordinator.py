"""CoordinatorEngine: RunIntent/TaskGraph orchestration over Core APIs."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..resource import ResourceBroker
from ..scheduler.global_scheduler import GlobalScheduler
from .action_bridge import ActionBridge


def _raw(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        return dict(method())
    return dict(vars(value))


def _contract_parts(value: Any) -> tuple[str, int]:
    text = str(value or "")
    if text.startswith("contract://"):
        text = text.removeprefix("contract://")
    if text.startswith("task/"):
        text = text.removeprefix("task/")
    if "@" in text:
        identifier, version = text.rsplit("@", 1)
        return identifier, int(version)
    return text, 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_mapping(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


@dataclass(frozen=True, slots=True)
class CoordinatorTick:
    run_id: str
    actions: tuple[Mapping[str, Any], ...]
    receipts: tuple[Mapping[str, Any], ...] = ()
    status: Mapping[str, Any] | None = None


class CoordinatorEngine:
    """Drives the global scheduler and Action Bridge without a second state file."""

    API_VERSION = 1

    def __init__(self, store: Any, *, host: Any = None, resource_broker: ResourceBroker | None = None, bridge: ActionBridge | None = None, scheduler: GlobalScheduler | None = None, adapter: str = "codex-app") -> None:
        self.store = store
        self.resource_broker = resource_broker or ResourceBroker()
        self.bridge = bridge or ActionBridge(store, host, adapter=adapter, resource_broker=self.resource_broker)
        self.scheduler = scheduler or GlobalScheduler(store, resource_broker=self.resource_broker, adapter=adapter)
        self.lanes: dict[str, Any] = {}

    def start(self, intent: Any, task_graph: Any = None, *, run_id: str | None = None) -> dict[str, Any]:
        value = _raw(intent)
        supplied_graph = task_graph if task_graph is not None else value.get("task_graph")
        if supplied_graph is not None:
            graph = _raw(supplied_graph)
        else:
            graph = {}
        run_id = run_id or str(graph.get("run_id") or value.get("run_id") or "run-" + uuid.uuid4().hex[:16])
        contracts = list(graph.get("contracts") or ())
        root_contract = str(value.get("root_contract_id") or (contracts[0].get("ref") if contracts else None) or f"contract://run/{run_id}@1")
        tasks = graph.get("tasks", graph if isinstance(graph, list) else ())
        if isinstance(tasks, Mapping):
            tasks = [dict(item, id=task_id) if isinstance(item, Mapping) else {"id": task_id, "outcome": str(item)} for task_id, item in tasks.items()]
        with self.store.transaction():
            policy = dict(value.get("resource_envelope", value.get("policy", {})) or {})
            repository = value.get("repository") if isinstance(value.get("repository"), Mapping) else {}
            if repository.get("mode"):
                policy.setdefault("repository_mode", str(repository["mode"]))
            if value.get("evidence_profile"):
                policy["evidence_profile"] = str(value["evidence_profile"])
            pack_value = value.get("pack")
            if isinstance(pack_value, Mapping):
                policy["workflow_pack"] = str(pack_value.get("id") or "delivery")
            elif pack_value:
                policy["workflow_pack"] = str(pack_value)
            else:
                policy["workflow_pack"] = str(graph.get("metadata", {}).get("pack") or "delivery")
            self.store.create_run(run_id, str(value.get("goal") or "All in Luna run"), policy, root_contract)
            contract_map: dict[tuple[str, int], str] = {}
            for contract in contracts:
                contract = _raw(contract)
                local_contract_id, version = _contract_parts(contract.get("contract_ref") or contract.get("contract_id") or contract.get("ref") or contract.get("id"))
                physical_contract_id = f"{run_id}:contract:{local_contract_id}"
                contract_map[(local_contract_id, version)] = physical_contract_id
                self.store.put_contract({**contract, "id": physical_contract_id, "version": version})

            task_map: dict[str, str] = {}
            pending_dependencies: list[tuple[str, Sequence[Any]]] = []
            for index, task in enumerate(tasks or ()):
                task = _raw(task)
                local_task_id = str(task.get("local_id") or task.get("id") or task.get("task_id") or f"task-{index + 1}")
                physical_task_id = f"{run_id}:task:{local_task_id}"
                task_map[local_task_id] = physical_task_id
                local_contract_id, contract_version = _contract_parts(task.get("contract_ref") or task.get("contract_id") or f"contract-{local_task_id}")
                physical_contract_id = contract_map.get((local_contract_id, contract_version), f"{run_id}:contract:{local_contract_id}")
                dependencies = task.get("dependencies", ()) or ()
                pending_dependencies.append((physical_task_id, dependencies))
                self.store.create_task(
                    {
                        **task,
                        "id": local_task_id,
                        "uid": physical_task_id,
                        "local_id": local_task_id,
                        "run_id": run_id,
                        "outcome": str(task.get("outcome") or task.get("objective") or value.get("goal") or local_task_id),
                        "contract_id": physical_contract_id,
                        "contract_version": contract_version,
                        "state": task.get("state", "proposed"),
                        "dependencies": (),
                    },
                    run_id=run_id,
                )

            if pending_dependencies:
                for task_id, dependencies in pending_dependencies:
                    for dependency in dependencies:
                        dep_id = str(dependency.get("task_id") or dependency.get("depends_on_task_id") or dependency.get("task_ref")) if isinstance(dependency, Mapping) else str(dependency)
                        dep_id = dep_id.removeprefix("task://")
                        dep_id = task_map.get(dep_id, dep_id)
                        if self.store.get_task(dep_id) is None:
                            raise KeyError(f"dependency task {dep_id!r} does not exist")
                        condition = dependency.get("condition", {}) if isinstance(dependency, Mapping) else {}
                        if isinstance(condition, str):
                            condition = {"type": condition, "exports": list(dependency.get("exports", ())) if isinstance(dependency, Mapping) else []}
                        self.store._execute("INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id, condition_json) VALUES (?, ?, ?)", (task_id, dep_id, json.dumps(dict(condition), sort_keys=True)))

            for local_task_id, work_graph in dict(graph.get("work_graphs") or {}).items():
                task_uid = task_map[str(local_task_id)]
                units = list(_raw(work_graph).get("work_units") or ())
                unit_map = {str(unit.get("id") or unit.get("work_unit_id")): f"{task_uid}:work:{unit.get('id') or unit.get('work_unit_id')}" for unit in units}
                pending = list(units)
                while pending:
                    progressed = False
                    for unit in list(pending):
                        local_unit_id = str(unit.get("id") or unit.get("work_unit_id"))
                        parent_local = unit.get("parent_id") or unit.get("parent_work_unit_id")
                        if parent_local and str(parent_local) in {str(item.get("id") or item.get("work_unit_id")) for item in pending}:
                            continue
                        dependencies = []
                        for dependency in unit.get("dependencies", ()) or ():
                            dep_local = str(dependency.get("work_unit_id") or dependency.get("depends_on_work_unit_id")) if isinstance(dependency, Mapping) else str(dependency)
                            dependencies.append({"work_unit_id": unit_map.get(dep_local, dep_local), "condition": dependency.get("condition", {}) if isinstance(dependency, Mapping) else {}})
                        self.store.create_work_unit({
                            **unit,
                            "id": local_unit_id,
                            "uid": unit_map[local_unit_id],
                            "local_id": local_unit_id,
                            "task_id": task_uid,
                            "parent_id": unit_map.get(str(parent_local)) if parent_local else None,
                            "dependencies": dependencies,
                        })
                        pending.remove(unit)
                        progressed = True
                    if not progressed:
                        raise ValueError(f"work graph for {local_task_id!r} contains an unresolved parent cycle")

            if not tasks:
                local_task_id = "task-root"
                self.store.create_task({"id": local_task_id, "uid": f"{run_id}:task:{local_task_id}", "local_id": local_task_id, "run_id": run_id, "outcome": str(value.get("goal") or "complete requested goal"), "contract_id": f"{run_id}:contract:contract-{local_task_id}", "done_when": value.get("done_when", ["root goal satisfied"]), "state": "proposed"}, run_id=run_id)
            self.store._append_signal_in_transaction(run_id, "run", run_id, "WORK_GRAPH_CHANGED", {"operation": "run-compiled", "tasks": len(tasks or ()), "work_graphs": len(graph.get("work_graphs") or {})})
        self.resource_broker.bind(self.store, run_id)
        self.resource_broker.recover()
        return self.status(run_id)

    def tick(self, run_id: str, *, dispatch: bool = True) -> CoordinatorTick:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        persisted_policy = dict(run.get("policy") or {})
        if persisted_policy and self.resource_broker.requested != persisted_policy:
            self.resource_broker = ResourceBroker(persisted_policy, store=self.store, run_id=run_id)
            self.scheduler.resource_broker = self.resource_broker
            self.bridge.resource_broker = self.resource_broker
        else:
            self.resource_broker.bind(self.store, run_id)
        self.resource_broker.recover()
        if run["status"] in {"paused", "cancelled", "aborted", "completed"}:
            return CoordinatorTick(run_id, (), (), self.status(run_id))
        self.process_promotion_requests(run_id)
        actions = self.scheduler.step(run_id) if dispatch else self.scheduler.preview(run_id)
        receipts: list[Mapping[str, Any]] = []
        if dispatch:
            for action in actions:
                result = self.bridge.dispatch(action)
                if isinstance(result, Mapping):
                    # Keep receipt plus resource requested/resolved/actual
                    # evidence together; callers must not mistake the action
                    # envelope for host evidence.
                    receipts.append(dict(result))
        self._complete_run_if_ready(run_id)
        return CoordinatorTick(run_id, tuple(action.to_dict() for action in actions), tuple(receipts), self.status(run_id))

    def ingest_receipt(self, receipt: Any) -> dict[str, Any]:
        result = self.bridge.ingest_receipt(receipt)
        return result

    def ingest_handoff(self, task_id: str, handoff: Mapping[str, Any]) -> dict[str, Any]:
        result = self.scheduler.accept_handoff(task_id, handoff)
        self.process_promotion_requests(str(result["run_id"]))
        self._complete_run_if_ready(str(result["run_id"]))
        return result

    def process_promotion_requests(self, run_id: str) -> list[dict[str, Any]]:
        """Validate and materialize durable cross-lane promotion requests.

        The request id is the stable lineage key, so replay after a crash is
        idempotent and never creates a second physical Task.
        """

        if self.store.get_run(run_id) is None:
            raise KeyError(run_id)
        rows = self.store._fetchall(
            "SELECT * FROM promotion_requests WHERE run_id = ? AND state = 'requested' ORDER BY created_at, id",
            (run_id,),
        )
        results: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_mapping(row.get("payload_json"))
            if bool(payload.get("user_decision_required")):
                prior = self.store._fetchone(
                    "SELECT seq FROM signals WHERE run_id = ? AND type = 'DECISION_REQUIRED' AND json_extract(payload_json, '$.request_id') = ? LIMIT 1",
                    (run_id, row["id"]),
                )
                if prior is None:
                    self.store.append_signal(
                        run_id, "DECISION_REQUIRED", {"request_id": row["id"], "reason": payload.get("reason")},
                        scope_type="task", scope_id=str(row["source_task_id"]),
                    )
                results.append({"request_id": row["id"], "state": "requested", "decision_required": True})
                continue
            rejection = self._promotion_rejection(row, payload)
            if rejection:
                rejected_payload = {**payload, "resolution": {"state": "rejected", "reason": rejection}}
                with self.store.transaction():
                    self.store._execute(
                        "UPDATE promotion_requests SET state = 'rejected', payload_json = ?, resolved_at = ? WHERE id = ? AND state = 'requested'",
                        (json.dumps(rejected_payload, sort_keys=True), _now(), row["id"]),
                    )
                    self.store._append_signal_in_transaction(
                        run_id, "task", str(row["source_task_id"]), "WORK_GRAPH_CHANGED",
                        {"operation": "promotion-rejected", "request_id": row["id"], "reason": rejection},
                    )
                results.append({"request_id": row["id"], "state": "rejected", "reason": rejection})
                continue
            results.append(self._accept_promotion(row, payload))
        return results

    def _promotion_rejection(self, row: Mapping[str, Any], payload: Mapping[str, Any]) -> str | None:
        source_task = self.store.get_task(str(row["source_task_id"]))
        source_unit = self.store.get_work_unit(str(row.get("source_work_unit_id") or "")) if row.get("source_work_unit_id") else None
        if source_task is None or str(source_task["run_id"]) != str(row["run_id"]):
            return "source task is outside the promotion run"
        if source_unit is not None and str(source_unit["task_id"]) != str(source_task["id"]):
            return "source work unit is outside the source lane"
        if row.get("source_work_unit_id") and source_unit is None:
            return "source work unit does not exist"
        if not str(payload.get("proposed_outcome") or "").strip():
            return "proposed_outcome is required"
        if not str(payload.get("reason") or "").strip():
            return "promotion reason is required"
        for dependency in payload.get("dependencies", ()) or ():
            dependency_id = self._resolve_task_id(str(row["run_id"]), dependency)
            dependency_task = self.store.get_task(dependency_id)
            if dependency_task is None or str(dependency_task["run_id"]) != str(row["run_id"]):
                return f"dependency {dependency!r} is outside the promotion run"
        return None

    def _resolve_task_id(self, run_id: str, dependency: Any) -> str:
        raw = dependency.get("task_id") or dependency.get("depends_on_task_id") if isinstance(dependency, Mapping) else dependency
        candidate = str(raw).removeprefix("task://")
        task = self.store.get_task(candidate, run_id=run_id)
        return str(task["id"]) if task is not None else candidate

    def _accept_promotion(self, row: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(row["id"])
        run_id = str(row["run_id"])
        task_id = f"{run_id}:task:promoted:{request_id}"
        contract_id = f"{run_id}:contract:promoted:{request_id}"
        source_task_id = str(row["source_task_id"])
        context_refs = tuple(str(item) for item in (payload.get("context_seed_refs") or (f"context://task/{source_task_id}",)))
        dependency_ids = [source_task_id]
        for dependency in payload.get("dependencies", ()) or ():
            resolved = self._resolve_task_id(run_id, dependency)
            if resolved not in dependency_ids:
                dependency_ids.append(resolved)
        ownership = [str(item) for item in payload.get("requested_ownership", ()) or ()]
        outcome = str(payload["proposed_outcome"])
        lineage = {
            "kind": "promotion",
            "request_ref": f"promotion-request://{request_id}",
            "source_task_ref": f"task://{source_task_id}",
            "source_work_unit_ref": f"work-unit://{row['source_work_unit_id']}" if row.get("source_work_unit_id") else None,
            "context_seed_refs": list(context_refs),
        }
        with self.store.transaction():
            fresh = self.store._fetchone(
                "SELECT state, promoted_task_id FROM promotion_requests WHERE id = ?",
                (request_id,),
            )
            if fresh is None:
                raise KeyError(request_id)
            if str(fresh["state"]) != "requested":
                return {
                    "request_id": request_id,
                    "state": str(fresh["state"]),
                    "promoted_task_id": fresh.get("promoted_task_id"),
                    "replayed": True,
                }
            existing = self.store.get_task(task_id)
            if existing is None:
                self.store.put_contract({
                    "id": contract_id,
                    "version": 1,
                    "task_id": task_id,
                    "outcome": outcome,
                    "imports": [{"name": f"context_seed_{index + 1}", "kind": "context", "ref": ref} for index, ref in enumerate(context_refs)],
                    "exports": [],
                    "done_when": [outcome],
                    "ownership": {"paths": ownership},
                    "permissions": {"actions": list(map(str, payload.get("requested_authority", ()) or ()))},
                    "context_policy": {"lineage": lineage, "seed_refs": list(context_refs)},
                })
                self.store.create_task({
                    "id": request_id,
                    "uid": task_id,
                    "local_id": f"promoted:{request_id}",
                    "run_id": run_id,
                    "outcome": outcome,
                    "contract_id": contract_id,
                    "contract_version": 1,
                    "state": "proposed",
                    "ownership": {"paths": ownership},
                    "dependencies": [{"task_id": item, "condition": {"type": "completed"}} for item in dependency_ids],
                    "resource_envelope": payload.get("resource_envelope") or {},
                })
                self.store._execute("UPDATE task_ownership SET source = 'promotion' WHERE task_id = ?", (task_id,))
            accepted_payload = {**payload, "resolution": {"state": "accepted", "promoted_task_ref": f"task://{task_id}", "contract_ref": f"contract://task/{contract_id}@1", "lineage": lineage}}
            self.store._execute(
                "UPDATE promotion_requests SET state = 'accepted', promoted_task_id = ?, payload_json = ?, resolved_at = ? WHERE id = ?",
                (task_id, json.dumps(accepted_payload, sort_keys=True), _now(), request_id),
            )
            self.store._append_signal_in_transaction(
                run_id, "task", task_id, "TASK_CREATED",
                {"source": "promotion", "request_id": request_id, "source_task_id": source_task_id, "context_seed_refs": list(context_refs)},
            )
            self.store._append_signal_in_transaction(
                run_id, "run", run_id, "WORK_GRAPH_CHANGED",
                {"operation": "promotion-accepted", "request_id": request_id, "promoted_task_id": task_id},
            )
        return {"request_id": request_id, "state": "accepted", "promoted_task_id": task_id, "contract_id": contract_id, "dependencies": dependency_ids, "context_seed_refs": list(context_refs)}

    def reconcile(self, run_id: str, receipts: Sequence[Any] = ()) -> dict[str, Any]:
        result = self.scheduler.reconcile(run_id, receipts)
        self._complete_run_if_ready(run_id)
        return result

    def pause(self, run_id: str) -> dict[str, Any]:
        return self.store.update_run_status(run_id, "paused", signal_type="RUN_STARTED", payload={"operation": "pause"})

    def resume(self, run_id: str) -> dict[str, Any]:
        return self.store.update_run_status(run_id, "active", signal_type="RUN_STARTED", payload={"operation": "resume"})

    def cancel(self, run_id: str, task_id: str | None = None) -> dict[str, Any]:
        if task_id:
            task = self.store.get_task(task_id)
            if task and task["state"] in {"proposed", "ready", "blocked"}:
                self.store.update_task_status(task_id, "cancelled", payload={"operation": "cancel"})
            return self.store.get_task(task_id) or {}
        return self.store.update_run_status(run_id, "cancelled", signal_type="RUN_COMPLETED", payload={"operation": "cancel"})

    def retry(self, run_id: str, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None or str(task["run_id"]) != run_id:
            raise KeyError(task_id)
        self.store._execute("UPDATE task_attempts SET state = 'failed', ended_at = COALESCE(ended_at, ?) WHERE task_id = ? AND state IN ('dispatched','acknowledged','active')", (_now(), task_id))
        if task["state"] not in {"completed", "cancelled", "superseded"}:
            self.store._execute("UPDATE tasks SET state = 'ready', updated_at = ? WHERE id = ?", (_now(), task_id))
            self.store.append_signal(run_id, "WORK_GRAPH_CHANGED", {"task_id": task_id, "operation": "retry"}, scope_type="task", scope_id=task_id)
        self.resource_broker.release(task_id)
        return self.store.get_task(task_id) or {}

    def set_policy(self, run_id: str, policy: Mapping[str, Any]) -> dict[str, Any]:
        self.resource_broker = ResourceBroker(policy, store=self.store, run_id=run_id)
        self.scheduler.resource_broker = self.resource_broker
        self.bridge.resource_broker = self.resource_broker
        with self.store.transaction():
            self.store._execute("UPDATE runs SET policy_json = ?, revision = revision + 1, updated_at = ? WHERE id = ?", (json.dumps(dict(policy), sort_keys=True), _now(), run_id))
            self.store._append_signal_in_transaction(run_id, "run", run_id, "CONTRACT_CHANGED", {"operation": "set-policy"})
        return self.status(run_id)

    def status(self, run_id: str) -> dict[str, Any]:
        projection = dict(self.store.export_status(run_id))
        for item in projection.get("tasks", []):
            task_id = str(item.get("task_ref") or "").removeprefix("task://")
            task = self.store.get_task(task_id) or {}
            task_resource = task.get("resource_envelope")
            task_resource = task_resource if isinstance(task_resource, Mapping) else {}
            item["requested_model"] = task_resource.get("model") or self.resource_broker.model
            item["requested_reasoning"] = task_resource.get("reasoning") or self.resource_broker.reasoning
            item["resolved_model"] = item["requested_model"]
            item["resolved_reasoning"] = item["requested_reasoning"]
            attempt_ref = item.get("lane_attempt_ref")
            if attempt_ref:
                attempt_id = str(attempt_ref).removeprefix("lane-attempt://")
                attempt = self.store.get_attempt(attempt_id)
                receipt_id = attempt.get("receipt_id") if attempt else None
                if receipt_id:
                    persisted = self.store.get_host_receipt(str(receipt_id))
                    resource_receipt = (persisted or {}).get("resource_receipt", {})
                    requested = resource_receipt.get("requested", {}) if isinstance(resource_receipt, Mapping) else {}
                    resolved = resource_receipt.get("resolved", {}) if isinstance(resource_receipt, Mapping) else {}
                    actual = resource_receipt.get("actual", {}) if isinstance(resource_receipt, Mapping) else {}
                    if isinstance(requested, Mapping):
                        item["requested_model"] = requested.get("model") or item["requested_model"]
                        item["requested_reasoning"] = requested.get("reasoning") or item["requested_reasoning"]
                    if isinstance(resolved, Mapping):
                        item["resolved_model"] = resolved.get("model") or item["resolved_model"]
                        item["resolved_reasoning"] = resolved.get("reasoning") or item["resolved_reasoning"]
                    if (
                        isinstance(actual, Mapping)
                        and resource_receipt.get("actual_state") == "resolved"
                        and actual.get("model") == item["resolved_model"]
                        and actual.get("reasoning") == item["resolved_reasoning"]
                    ):
                        item["actual_model"] = actual["model"]
                        item["actual_reasoning"] = actual["reasoning"]
                        item["actual_model_state"] = "resolved"
                        item["resource_evidence_source"] = resource_receipt.get("evidence_source")
                        item["resource_observed_at"] = resource_receipt.get("observed_at")
        projection.setdefault("extensions", {})["resource_policy_receipt"] = self.resource_broker.resolve().to_dict()
        metrics = self.store.runtime_metrics(run_id)
        projection["extensions"]["metrics"] = metrics
        projection["extensions"]["progress_pulse"] = {
            "kind": "ProgressPulse", "run_ref": f"run://{run_id}",
            "completed": metrics["completed_tasks"], "total": metrics["tasks"],
            "blocked": metrics["blocked_tasks"], "outbox_backlog": metrics["outbox_backlog"],
        }
        return projection

    def graph(self, run_id: str) -> dict[str, Any]:
        return self.scheduler.graph(run_id)

    def _complete_run_if_ready(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if run is None or run["status"] in {"completed", "cancelled", "aborted"}:
            return
        tasks = self.store._fetchall("SELECT state, required FROM tasks WHERE run_id = ?", (run_id,))
        if tasks and all(str(task["state"]) in {"completed", "cancelled", "superseded"} for task in tasks) and all(str(task["state"]) == "completed" or not bool(task["required"]) for task in tasks):
            self.store.update_run_status(run_id, "completed", signal_type="RUN_COMPLETED", payload={"source": "coordinator"})


CoordinatorEngineAPI = CoordinatorEngine

__all__ = ["CoordinatorEngine", "CoordinatorEngineAPI", "CoordinatorTick"]
