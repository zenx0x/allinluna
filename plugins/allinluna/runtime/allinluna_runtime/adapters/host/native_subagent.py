"""Native recursive subagent host and explicit direct-lane fallback."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ...core.policy import contains, contains_all
from .base import (
    ACTION_BRIDGE_PROTOCOL,
    DIRECT_WORK_RESULT_PROTOCOL,
    DIRECT_ONLY,
    HOST_RECEIPT_PROTOCOL,
    NATIVE_PREFERRED,
    NATIVE_REQUIRED,
    NATIVE_SUBAGENT_CAPABILITY,
    WORK_HANDOFF_PROTOCOL,
    HostAction,
    HostAdapter,
    HostCapabilities,
    HostReceipt,
    HostUnavailableError,
    DirectWorkResult,
    LaneDirectExecutionPlan,
    LocalDispatchIntent,
    as_host_action,
    mapping_from,
    stable_digest,
)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        for key in ("paths", "scope", "ownership", "authority", "actions"):
            if key in value:
                return _values(value[key])
        return tuple(str(item) for item in value.values())
    return tuple(str(item) for item in value)


def _within(child: str, parent: str) -> bool:
    return contains(parent, child)


def _subset(children: Sequence[str], parents: Sequence[str]) -> bool:
    return contains_all(parents, children)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class LocalCapabilityUnavailable(HostUnavailableError):
    """The requested local mode requires a native capability the host lacks."""

    code = "HOST_CAPABILITY_BLOCKED"

    def __init__(self, intent: LocalDispatchIntent, reason: str) -> None:
        super().__init__(reason)
        self.intent = intent
        self.reason = reason


@dataclass(frozen=True, slots=True)
class WorkHandoff(Mapping[str, Any]):
    """Lane-local result whose completion is backed by independent evidence."""

    work_unit_id: str
    task_id: str
    attempt_id: str | None
    status: str
    summary: str
    execution_mode: str
    changed_paths: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    checks: tuple[Mapping[str, Any], ...] = ()
    evidence: Mapping[str, Any] | None = None
    blockers: tuple[Mapping[str, Any], ...] = ()
    plan_digest: str | None = None
    result_digest: str | None = None
    idempotency_key: str | None = None
    execution_source: str = "lane-direct-executor"
    handoff_id: str | None = None
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.status == "completed":
            if not isinstance(self.evidence, Mapping) or self.evidence.get("verified") is not True:
                raise ValueError("completed lane-direct work requires independently verified evidence")
            if not self.checks:
                raise ValueError("completed lane-direct work requires independent check receipts")

    def to_dict(self) -> dict[str, Any]:
        handoff_id = self.handoff_id or "work-handoff-" + stable_digest(
            {
                "work_unit_id": self.work_unit_id,
                "attempt_id": self.attempt_id,
                "status": self.status,
                "plan_digest": self.plan_digest,
                "result_digest": self.result_digest,
                "evidence": self.evidence,
            }
        )
        return {
            "kind": "handoff",
            "schema_version": "1.0",
            "protocol": WORK_HANDOFF_PROTOCOL,
            "handoff_kind": "work",
            "handoff_id": handoff_id,
            "task_id": self.task_id,
            "work_unit_id": self.work_unit_id,
            "attempt_id": self.attempt_id,
            "idempotency_key": self.idempotency_key,
            "plan_digest": self.plan_digest,
            "result_digest": self.result_digest,
            "status": self.status,
            "summary": self.summary,
            "execution_mode": self.execution_mode,
            "execution_source": self.execution_source,
            "subagent_created": False,
            "thread_id": None,
            "changed_paths": list(self.changed_paths),
            "artifacts": list(self.artifacts),
            "checks": [dict(item) for item in self.checks],
            "evidence": deepcopy(dict(self.evidence)) if isinstance(self.evidence, Mapping) else None,
            "blockers": [dict(item) for item in self.blockers],
            "created_at": self.created_at,
        }

    as_dict = to_dict

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class LaneDirectExecutor:
    """Execute a WorkUnit in its existing top-level Lane and verify it.

    The optional ``work_executor`` is the Lane's concrete implementation seam.
    Regardless of whether work was performed before or during this call, this
    class creates the WorkHandoff itself and asks an independent
    ``EvidenceCollector`` to observe checks, artifacts, and workspace state.
    """

    EXECUTION_SOURCE = "lane-direct-executor"

    def __init__(
        self,
        store: Any,
        *,
        evidence_collector: Any = None,
        artifact_store: Any = None,
        work_executor: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self.store = store
        if evidence_collector is None:
            from ...evidence import EvidenceCollector

            evidence_collector = EvidenceCollector(store, artifact_store=artifact_store)
        self.evidence_collector = evidence_collector
        self.artifacts = artifact_store or getattr(evidence_collector, "artifacts", None)
        self.work_executor = work_executor

    @property
    def has_embedded_executor(self) -> bool:
        """Whether the optional in-process callback fast path is available."""

        return self.work_executor is not None

    def _artifact_refs(
        self, plan: LaneDirectExecutionPlan, result: Mapping[str, Any]
    ) -> list[str]:
        refs = [str(item) for item in (result.get("artifacts") or ())]
        raw_outputs = result.get("raw_outputs", ())
        if isinstance(raw_outputs, (str, bytes, bytearray, Mapping)):
            raw_outputs = (raw_outputs,)
        for index, item in enumerate(raw_outputs or ()):
            if self.artifacts is None:
                continue
            content = item if isinstance(item, bytes) else json.dumps(
                {
                    "work_unit_id": plan.intent.work_unit_id,
                    "attempt_id": plan.intent.attempt_id,
                    "index": index,
                    "output": item,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
            record = self.artifacts.put(
                content,
                kind="tool-log",
                produced_by=self.EXECUTION_SOURCE,
                metadata={"lane_direct": True},
                link=("work_unit", plan.intent.work_unit_id, "raw-output"),
            )
            refs.append(str(record.ref))
        return list(dict.fromkeys(refs))

    def _blocked(
        self,
        plan: LaneDirectExecutionPlan,
        *,
        code: str,
        message: str,
        evidence: Mapping[str, Any] | None = None,
        artifacts: Sequence[str] = (),
        changed_paths: Sequence[str] = (),
        result: DirectWorkResult | None = None,
        execution_source: str | None = None,
    ) -> dict[str, Any]:
        return WorkHandoff(
            work_unit_id=plan.intent.work_unit_id,
            task_id=plan.intent.task_id,
            attempt_id=plan.intent.attempt_id,
            status="blocked",
            summary=message,
            execution_mode="lane_direct",
            changed_paths=tuple(map(str, changed_paths)),
            artifacts=tuple(map(str, artifacts)),
            checks=tuple(
                dict(item) for item in (evidence or {}).get("checks", ())
                if isinstance(item, Mapping)
            ),
            evidence=evidence,
            blockers=(
                {
                    "code": code,
                    "message": message,
                    "owner_scope": plan.intent.work_unit_id,
                    "recoverable": True,
                },
            ),
            plan_digest=plan.to_dict().get("plan_digest"),
            result_digest=result.to_dict().get("result_digest") if result is not None else None,
            idempotency_key=plan.intent.idempotency_key,
            execution_source=execution_source or self.EXECUTION_SOURCE,
        ).to_dict()

    def _build_handoff(
        self,
        plan: LaneDirectExecutionPlan,
        result: DirectWorkResult,
        *,
        execution_source: str,
    ) -> dict[str, Any]:
        """Turn a report into a handoff only after independent observation."""

        result_value = result.to_dict()
        changed_paths = tuple(result.changed_paths)
        if changed_paths and (
            not plan.intent.ownership or not _subset(changed_paths, plan.intent.ownership)
        ):
            return self._blocked(
                plan,
                code="lane.direct_ownership_violation",
                message="lane-direct changed_paths exceed WorkUnit ownership",
                result=result,
                execution_source=execution_source,
            )
        export_values = tuple(
            dict(item) for item in (result_value.get("exports") or ()) if isinstance(item, Mapping)
        )
        artifact_inputs = list(result_value.get("artifacts") or ())
        artifact_inputs.extend(
            str(item["artifact_ref"])
            for item in export_values
            if item.get("artifact_ref")
        )
        artifacts = self._artifact_refs(
            plan,
            {**result_value, "artifacts": artifact_inputs},
        )
        if str(result.status).lower() in {"failed", "blocked"} or result.blockers:
            return self._blocked(
                plan,
                code="lane.direct_execution_blocked",
                message=str(result.summary or "lane-direct execution did not complete"),
                artifacts=artifacts,
                changed_paths=changed_paths,
                result=result,
                execution_source=execution_source,
            )

        candidate = {
            "protocol": WORK_HANDOFF_PROTOCOL,
            "work_unit_id": plan.intent.work_unit_id,
            "status": "verifying",
            "changed_paths": list(changed_paths),
            "artifacts": artifacts,
        }
        try:
            evidence = self.evidence_collector.collect(
                self.store.get_task(plan.intent.task_id) or plan.intent.task_id,
                candidate,
                checks=plan.intent.checks,
                artifacts=artifacts,
                exports=export_values,
                workspace_scope={
                    "workspace": plan.intent.resource_envelope.get("workspace"),
                    "ownership": list(plan.intent.ownership),
                    "work_unit_id": plan.intent.work_unit_id,
                },
                profile=plan.intent.artifact_policy.get("evidence_profile"),
            )
        except Exception as exc:
            return self._blocked(
                plan,
                code="lane.direct_evidence_collection_failed",
                message=f"independent evidence collection failed: {type(exc).__name__}: {exc}",
                artifacts=artifacts,
                changed_paths=changed_paths,
                result=result,
                execution_source=execution_source,
            )
        verified_paths = tuple(str(item) for item in evidence.get("changed_paths", ()))
        workspace = evidence.get("workspace_evidence")
        if changed_paths and (
            not isinstance(workspace, Mapping)
            or workspace.get("adapter") == "projectless"
            or set(verified_paths) != set(changed_paths)
        ):
            evidence = dict(evidence)
            evidence["verified"] = False
            evidence["errors"] = sorted(
                set(evidence.get("errors", ())) | {"lane_direct_changed_paths_unverified"}
            )
        if evidence.get("verified") is not True:
            return self._blocked(
                plan,
                code="lane.direct_evidence_unverified",
                message="lane-direct work lacks independently verified completion evidence",
                evidence=evidence,
                artifacts=artifacts,
                changed_paths=changed_paths,
                result=result,
                execution_source=execution_source,
            )
        checks = tuple(
            dict(item) for item in evidence.get("checks", ()) if isinstance(item, Mapping)
        )
        return WorkHandoff(
            work_unit_id=plan.intent.work_unit_id,
            task_id=plan.intent.task_id,
            attempt_id=plan.intent.attempt_id,
            status="completed",
            summary=str(result.summary or f"completed {plan.intent.objective}"),
            execution_mode="lane_direct",
            changed_paths=verified_paths,
            artifacts=tuple(
                dict.fromkeys([*artifacts, *map(str, evidence.get("artifacts", ()))])
            ),
            checks=checks,
            evidence=evidence,
            blockers=(),
            plan_digest=plan.to_dict().get("plan_digest"),
            result_digest=result_value.get("result_digest"),
            idempotency_key=plan.intent.idempotency_key,
            execution_source=execution_source,
        ).to_dict()

    def ingest_result(
        self,
        plan: LaneDirectExecutionPlan | Mapping[str, Any],
        result: DirectWorkResult | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Independently verify an external result and build its WorkHandoff."""

        direct_plan = LaneDirectExecutionPlan.from_value(plan)
        direct_result = DirectWorkResult.from_value(result, plan=direct_plan)
        return self._build_handoff(
            direct_plan,
            direct_result,
            execution_source="lane-direct-external",
        )

    def execute(self, value: LaneDirectExecutionPlan | Mapping[str, Any]) -> dict[str, Any]:
        plan = LaneDirectExecutionPlan.from_value(value)
        if self.work_executor is None:
            raise RuntimeError("lane-direct external execution is required when no callback is bound")
        try:
            observed = self.work_executor(plan.to_dict())
            result = DirectWorkResult.from_value(
                {
                    **dict(observed or {}),
                    "protocol": DIRECT_WORK_RESULT_PROTOCOL,
                    "work_unit_id": plan.intent.work_unit_id,
                    "attempt_id": plan.intent.attempt_id,
                    "idempotency_key": plan.intent.idempotency_key,
                    "plan_digest": plan.to_dict()["plan_digest"],
                },
                plan=plan,
            )
        except Exception as exc:
            return self._blocked(
                plan,
                code="lane.direct_execution_failed",
                message=f"lane-direct execution failed: {type(exc).__name__}: {exc}",
            )
        return self._build_handoff(plan, result, execution_source=self.EXECUTION_SOURCE)


@dataclass(frozen=True, slots=True)
class NativeSubagentFallbackContract(Mapping[str, Any]):
    """A truthful direct-execution boundary when native subagents are absent."""

    work_unit_id: str
    parent_work_unit_id: str
    reason: str = "native-subagent-unavailable"
    scope: tuple[str, ...] = ()
    authority: tuple[str, ...] = ()
    ownership: tuple[str, ...] = ()
    idempotency_key: str | None = None
    action_id: str | None = None
    host_id: str | None = None
    source: str | None = None
    subagent_created: bool = False
    thread_id: str | None = None
    receipt_id: str | None = None
    status: str = "direct-execution"

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": HOST_RECEIPT_PROTOCOL,
            "kind": "lane-direct-fallback",
            "contract": "native-subagent-fallback/v1",
            "work_unit_id": self.work_unit_id,
            "parent_work_unit_id": self.parent_work_unit_id,
            "reason": self.reason,
            "scope": list(self.scope),
            "authority": list(self.authority),
            "ownership": list(self.ownership),
            "idempotency_key": self.idempotency_key,
            "action_id": self.action_id,
            "host_id": self.host_id,
            "source": self.source,
            "subagent_created": False,
            "thread_id": None,
            "receipt_id": self.receipt_id,
            "status": self.status,
            "model_receipt": "unresolved",
            "actual": False,
        }

    as_dict = to_dict

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


class NativeSubagentHost(HostAdapter):
    """Host boundary for nested work units with monotone policy narrowing."""

    host_kind = "native-subagent"

    def __init__(
        self,
        host: Any = None,
        *,
        capabilities: Any = None,
        host_id: str = "native-subagent-host",
        scope: Any = (),
        authority: Any = (),
        ownership: Any = (),
        native_available: bool | None = None,
        native_tool: str | None = None,
        tool_invokers: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None,
    ) -> None:
        self.host = host
        self.host_id = host_id
        self.parent_scope = _values(scope)
        self.parent_authority = _values(authority)
        self.parent_ownership = _values(ownership)
        self._capabilities = HostCapabilities.from_value(capabilities, default_host_id=host_id) if capabilities is not None else None
        self._native_override = native_available
        self._native_tool = _text(native_tool)
        self._tool_invokers = dict(tool_invokers or {})
        self._actions: list[HostAction] = []
        self._receipts: dict[str, HostReceipt | NativeSubagentFallbackContract] = {}

    @property
    def actions(self) -> tuple[HostAction, ...]:
        return tuple(self._actions)

    def discover(self) -> HostCapabilities:
        method = getattr(self.host, "discover", None)
        raw = method() if callable(method) else None
        if raw is not None:
            self._capabilities = HostCapabilities.from_value(raw, default_host_id=self.host_id)
        current = self._capabilities
        binding = current.logical_capability(NATIVE_SUBAGENT_CAPABILITY) if current else {}
        has_binding = bool(
            current
            and NATIVE_SUBAGENT_CAPABILITY in current.logical_capabilities
        )
        native = (
            self._native_override
            if self._native_override is not None
            else bool(binding.get("available"))
            if has_binding
            else callable(getattr(self.host, "spawn", None))
        )
        preferred = self._native_tool or (
            _text(binding.get("preferred_tool")) if binding else None
        ) or _text(getattr(self.host, "native_subagent_tool", None))
        if native and preferred is None and callable(getattr(self.host, "spawn", None)):
            preferred = "spawn"
        physical = list(binding.get("physical_tools", ())) if binding else []
        if preferred and preferred not in physical:
            physical.insert(0, preferred)
        base_tools = tuple(current.tools) if current else ()
        logical = dict(current.logical_capabilities) if current else {}
        logical[NATIVE_SUBAGENT_CAPABILITY] = {
            "available": bool(native and preferred),
            "physical_tools": physical,
            "preferred_tool": preferred,
            "receipt_contract": HOST_RECEIPT_PROTOCOL,
        }
        self._capabilities = HostCapabilities(
            host_id=current.host_id if current else self.host_id,
            host_kind=current.host_kind if current else self.host_kind,
            available=current.available if current else bool(self.host is not None or native),
            tools=tuple(dict.fromkeys((*base_tools, *physical))),
            native_subagent=bool(native and preferred),
            receipt_provenance=current.receipt_provenance if current else None,
            source=current.source if current else "native-subagent-adapter",
            is_real_codex_app=current.is_real_codex_app if current else None,
            logical_capabilities=logical,
            evidence=current.evidence if current else {},
        )
        return self._capabilities

    def resolve_local(
        self, value: LocalDispatchIntent | Mapping[str, Any]
    ) -> HostAction | LaneDirectExecutionPlan:
        """Resolve a logical local request to one exact action or direct plan."""

        intent = LocalDispatchIntent.from_value(value)
        if intent.execution_mode == DIRECT_ONLY:
            return LaneDirectExecutionPlan(intent, reason="direct-only-policy")
        binding = self.discover().logical_capability(intent.logical_capability)
        tool = _text(binding.get("preferred_tool"))
        if binding.get("available") is True and tool:
            return HostAction(
                action_id="action-" + stable_digest(
                    {"host": self.host_id, "key": intent.idempotency_key, "tool": tool}
                ),
                kind="spawn-subagent",
                idempotency_key=intent.idempotency_key,
                tool=tool,
                arguments={"envelope": intent.to_dict()},
                task_id=intent.task_id,
                dispatch_id="dispatch-" + stable_digest(
                    {"work_unit": intent.work_unit_id, "key": intent.idempotency_key}
                ),
                host_id=self.host_id,
                model=_text(intent.resource_envelope.get("model")),
                reasoning=_text(
                    intent.resource_envelope.get("reasoning", intent.resource_envelope.get("thinking"))
                ),
                execution_class="local_subagent",
                logical_capability=intent.logical_capability,
                tool_policy={
                    "exact_tool": tool,
                    "substitutions": [],
                    "on_unavailable": "block",
                },
                host_capability_required=tool,
                payload={
                    "work_unit_id": intent.work_unit_id,
                    "attempt_id": intent.attempt_id,
                    "work_unit_envelope": intent.to_dict(),
                    "local_dispatch_intent": intent.to_dict(),
                    "resource_receipt": intent.resource_envelope.get("receipt"),
                },
            )
        if intent.execution_mode == NATIVE_REQUIRED:
            raise LocalCapabilityUnavailable(
                intent,
                f"host {self.host_id!r} does not advertise an adapter-resolved physical "
                f"tool for logical capability {intent.logical_capability!r}",
            )
        if intent.execution_mode != NATIVE_PREFERRED:
            raise ValueError(f"unsupported local execution mode: {intent.execution_mode!r}")
        return LaneDirectExecutionPlan(intent, reason="native-subagent-unavailable")

    def invoke_resolved(self, value: HostAction | Mapping[str, Any]) -> HostReceipt:
        """Invoke an already-resolved exact local action through this adapter."""

        action = as_host_action(value)
        if action.execution_class != "local_subagent":
            raise ValueError("NativeSubagentHost only invokes local_subagent HostActions")
        binding = self.discover().logical_capability(
            action.logical_capability or NATIVE_SUBAGENT_CAPABILITY
        )
        if action.tool not in binding.get("physical_tools", ()):
            raise LocalCapabilityUnavailable(
                LocalDispatchIntent.from_value(action.payload.get("local_dispatch_intent", {})),
                f"resolved physical tool {action.tool!r} is no longer advertised",
            )
        arguments = dict(action.arguments)
        invoker = self._tool_invokers.get(str(action.tool))
        if invoker is not None:
            result = invoker(arguments)
        else:
            invoke_tool = getattr(self.host, "invoke_tool", None)
            invoke = getattr(self.host, "invoke", None)
            method = getattr(self.host, str(action.tool), None)
            if callable(invoke_tool):
                result = invoke_tool(action.tool, arguments)
            elif callable(invoke):
                result = invoke(action.tool, arguments)
            elif callable(method):
                result = method(arguments.get("envelope", arguments))
            elif action.tool == "spawn" and callable(getattr(self.host, "spawn", None)):
                result = self.host.spawn(arguments.get("envelope", arguments))
            else:
                raise LocalCapabilityUnavailable(
                    LocalDispatchIntent.from_value(action.payload.get("local_dispatch_intent", {})),
                    f"host cannot invoke resolved physical tool {action.tool!r}",
                )
        raw = mapping_from(result or {})
        raw.update(
            {
                "actual_tool": action.tool,
                "actual_capability": action.logical_capability,
                "action_contract_hash": action.action_contract_hash,
                "local_resource_receipt": {
                    "requested_capability": action.logical_capability,
                    "resolved_tool": action.tool,
                    "actual_tool": action.tool,
                    "actual_capability": action.logical_capability,
                    "host_id": raw.get("host_id") or self.host_id,
                    "worker_id": raw.get("worker_id"),
                    "thread_id": raw.get("thread_id") or raw.get("threadId"),
                },
            }
        )
        return HostReceipt.from_value(
            raw, action=action, default_source="native-subagent", default_host_id=self.host_id
        )

    def _policy(self, envelope: Mapping[str, Any], name: str) -> tuple[str, ...]:
        aliases = {
            "scope": ("scope", "paths", "parent_scope"),
            "authority": ("authority", "parent_authority"),
            "ownership": ("ownership", "owned_paths", "parent_ownership"),
        }
        for key in aliases[name]:
            if key in envelope:
                value = _values(envelope[key])
                if key.startswith("parent_") and name in {"scope", "ownership", "authority"} and name in envelope:
                    continue
                return value
        return ()

    def _parent_policy(self, envelope: Mapping[str, Any], name: str) -> tuple[str, ...]:
        aliases = {"scope": ("parent_scope",), "authority": ("parent_authority",), "ownership": ("parent_ownership",)}
        for key in aliases[name]:
            if key in envelope:
                return _values(envelope[key])
        return {"scope": self.parent_scope, "authority": self.parent_authority, "ownership": self.parent_ownership}[name]

    def _validate_narrowing(self, envelope: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        child_scope = self._policy(envelope, "scope")
        child_authority = self._policy(envelope, "authority")
        child_ownership = self._policy(envelope, "ownership")
        parent_scope = self._parent_policy(envelope, "scope")
        parent_authority = self._parent_policy(envelope, "authority")
        parent_ownership = self._parent_policy(envelope, "ownership")
        if not parent_scope or not _subset(child_scope, parent_scope):
            raise ValueError("child scope must be a non-empty subset of parent scope")
        if not parent_authority or not set(child_authority).issubset(parent_authority):
            raise ValueError("child authority must be a subset of parent authority")
        if not parent_ownership or not _subset(child_ownership, parent_ownership):
            raise ValueError("child ownership must be a non-empty subset of parent ownership")
        return child_scope, child_authority, child_ownership

    def _fallback(self, envelope: Mapping[str, Any], *, reason: str) -> NativeSubagentFallbackContract:
        work_unit = _text(envelope.get("work_unit_id", envelope.get("workUnitId"))) or "unknown-work-unit"
        parent = _text(envelope.get("parent_work_unit_id", envelope.get("parentWorkUnitId"))) or "unknown-parent"
        key = _text(envelope.get("idempotency_key", envelope.get("idempotencyKey")))
        action_id = "action-" + stable_digest({"work_unit": work_unit, "key": key, "reason": reason})
        capabilities = self._capabilities or self.discover()
        return NativeSubagentFallbackContract(work_unit_id=work_unit, parent_work_unit_id=parent, reason=reason, scope=self._policy(envelope, "scope"), authority=self._policy(envelope, "authority"), ownership=self._policy(envelope, "ownership"), idempotency_key=key, action_id=action_id, host_id=capabilities.host_id, source=capabilities.source or "native-subagent", receipt_id="receipt-" + stable_digest({"action": action_id, "reason": reason}))

    def direct_fallback_receipt(
        self, value: LaneDirectExecutionPlan | Mapping[str, Any]
    ) -> NativeSubagentFallbackContract:
        """Receipt the routing decision without claiming WorkUnit completion."""

        plan = LaneDirectExecutionPlan.from_value(value)
        payload = plan.to_dict()
        payload["parent_work_unit_id"] = (
            plan.intent.parent_work_unit_id or plan.intent.task_id
        )
        return self._fallback(payload, reason=plan.reason)

    def _invoke(self, method_name: str, *args: Any) -> Any:
        method = getattr(self.host, method_name, None)
        if callable(method):
            return method(*args)
        invoke = getattr(self.host, "invoke", None)
        if callable(invoke):
            return invoke(method_name, *args)
        return None

    def spawn(self, envelope: Any) -> HostReceipt | NativeSubagentFallbackContract:
        raw = mapping_from(envelope)
        child_scope, child_authority, child_ownership = self._validate_narrowing(raw)
        key = _text(raw.get("idempotency_key", raw.get("idempotencyKey")))
        if not key:
            raise ValueError("subagent envelope requires idempotency_key")
        if key in self._receipts:
            return self._receipts[key]
        work_unit = _text(raw.get("work_unit_id", raw.get("workUnitId"))) or "unknown-work-unit"
        parent = _text(raw.get("parent_work_unit_id", raw.get("parentWorkUnitId"))) or "unknown-parent"
        intent = LocalDispatchIntent(
            work_unit_id=work_unit,
            task_id=str(raw.get("task_id") or parent),
            objective=str(raw.get("objective") or work_unit),
            idempotency_key=key,
            attempt_id=_text(raw.get("attempt_id")),
            run_ref=_text(raw.get("run_ref")),
            parent_work_unit_id=parent,
            logical_capability=str(
                raw.get("logical_capability") or NATIVE_SUBAGENT_CAPABILITY
            ),
            execution_mode=str(raw.get("execution_mode") or NATIVE_PREFERRED),
            context_ref=_text(raw.get("context_ref") or raw.get("base_context_ref")),
            scope=child_scope,
            authority=child_authority,
            ownership=child_ownership,
            resource_envelope=raw.get("resource_envelope") or {},
            checks=tuple(raw.get("checks") or ()),
            artifact_policy=raw.get("artifact_policy") or {},
        )
        resolution = self.resolve_local(intent)
        if isinstance(resolution, LaneDirectExecutionPlan):
            receipt = self._fallback(raw, reason="native-subagent-unavailable")
            self._receipts[key] = receipt
            return receipt
        action = resolution
        self._actions.append(action)
        try:
            receipt = self.invoke_resolved(action)
        except LocalCapabilityUnavailable:
            receipt = HostReceipt(
                receipt_id="receipt-" + stable_digest(action.to_dict()),
                status="pending",
                source="native-subagent",
                host_id=self.host_id,
                action_id=action.action_id,
                action_kind=action.kind,
                idempotency_key=key,
                dispatch_id=action.dispatch_id,
                task_id=parent,
                actual=False,
                fallback="action-bridge-required",
                model_receipt="unresolved",
                action=action.to_dict(),
                payload={"protocol": ACTION_BRIDGE_PROTOCOL, "action": action.to_dict()},
            )
        self._receipts[key] = receipt
        return receipt

    def wait(self, work_unit_ids: Sequence[str], cursor: str | None = None) -> HostReceipt:
        result = self._invoke("wait", list(work_unit_ids), cursor)
        if result is None:
            action = HostAction(action_id="action-" + stable_digest({"kind": "wait", "ids": list(work_unit_ids), "cursor": cursor}), kind="wait-subagents", idempotency_key="intent:wait-subagents:" + stable_digest({"ids": list(work_unit_ids), "cursor": cursor}), tool="native_subagent.wait", arguments={"work_unit_ids": list(work_unit_ids), "cursor": cursor}, host_id=self.host_id)
            return HostReceipt(receipt_id="receipt-" + stable_digest(action.to_dict()), status="pending", source="native-subagent", host_id=self.host_id, action_id=action.action_id, action_kind=action.kind, idempotency_key=action.idempotency_key, actual=False, fallback="action-bridge-required", action=action.to_dict(), payload={"protocol": ACTION_BRIDGE_PROTOCOL, "action": action.to_dict()})
        return HostReceipt.from_value(result, default_source="native-subagent", default_host_id=self.host_id)

    def read(self, work_unit_id: str, cursor: str | None = None) -> HostReceipt:
        result = self._invoke("read", work_unit_id, cursor)
        if result is None:
            action = HostAction(action_id="action-" + stable_digest({"kind": "read", "id": work_unit_id, "cursor": cursor}), kind="read-subagent", idempotency_key="intent:read-subagent:" + stable_digest({"id": work_unit_id, "cursor": cursor}), tool="native_subagent.read", arguments={"work_unit_id": work_unit_id, "cursor": cursor}, host_id=self.host_id)
            return HostReceipt(receipt_id="receipt-" + stable_digest(action.to_dict()), status="pending", source="native-subagent", host_id=self.host_id, action_id=action.action_id, action_kind=action.kind, idempotency_key=action.idempotency_key, actual=False, fallback="action-bridge-required", action=action.to_dict(), payload={"protocol": ACTION_BRIDGE_PROTOCOL, "action": action.to_dict()})
        return HostReceipt.from_value(result, default_source="native-subagent", default_host_id=self.host_id)

    def correct(
        self,
        correction: Any,
        task_id: str,
        thread_id: str | None = None,
        host_id: str | None = None,
    ) -> HostReceipt:
        envelope = mapping_from(correction)
        correction_id = _text(envelope.get("correction_id", envelope.get("correctionId"))) or "correction-" + stable_digest(envelope)
        target_thread = _text(thread_id) or _text(envelope.get("thread_id", envelope.get("threadId")))
        if not target_thread:
            return HostReceipt(receipt_id="receipt-" + stable_digest({"correction": envelope, "task": task_id}), status="unresolved", source="native-subagent", host_id=self.host_id, task_id=task_id, fallback="real-thread-id-required", payload={"correction": envelope})
        result = self._invoke("correct", envelope, task_id, target_thread)
        if result is None:
            target = {"task_id": task_id, "thread_id": target_thread}
            if host_id:
                target["host_id"] = host_id
            result = self._invoke("send_message", target, envelope)
        if result is None:
            action = HostAction(action_id="action-" + stable_digest({"correction": envelope, "task": task_id, "thread": target_thread}), kind="correction", idempotency_key="intent:correction:" + stable_digest({"correction": correction_id, "task": task_id}), tool="native_subagent.correction", arguments={"task_id": task_id, "thread_id": target_thread, "envelope": envelope}, host_id=self.host_id)
            return HostReceipt(receipt_id="receipt-" + stable_digest(action.to_dict()), status="pending", source="native-subagent", host_id=self.host_id, action_id=action.action_id, action_kind=action.kind, idempotency_key=action.idempotency_key, task_id=task_id, thread_id=target_thread, actual=False, fallback="action-bridge-required", action=action.to_dict(), payload={"protocol": ACTION_BRIDGE_PROTOCOL, "action": action.to_dict()})
        return HostReceipt.from_value(result, default_source="native-subagent", default_host_id=self.host_id)

    def receipt(self, payload: Any) -> HostReceipt | NativeSubagentFallbackContract:
        if isinstance(payload, NativeSubagentFallbackContract):
            return payload
        return HostReceipt.from_value(payload, default_source="native-subagent", default_host_id=self.host_id)

    def request_promotion(self, request: Any) -> dict[str, Any]:
        value = mapping_from(request)
        return {"protocol": "promotion-request/v1", "kind": "promotion-request", "status": "requested", "source": "native-subagent", "request": deepcopy(value)}

    # HostAdapter compatibility: recursive callers should use spawn/wait/read;
    # these methods keep the shared protocol type-checkable without pretending
    # that a work unit is a top-level task.
    def create_top_level_task(self, action: Any) -> HostReceipt:
        return HostReceipt(receipt_id="receipt-" + stable_digest(mapping_from(action)), status="unresolved", source="native-subagent", host_id=self.host_id, fallback="top-level-task-not-supported-by-native-host", payload=mapping_from(action))

    def wait_tasks(self, targets: Sequence[Any], cursor: str | None = None) -> HostReceipt:
        return self.wait([_text(mapping_from(item).get("work_unit_id", mapping_from(item).get("workUnitId"))) or str(item) for item in targets], cursor)

    def read_task(self, target: Any, cursor: str | None = None) -> HostReceipt:
        raw = mapping_from(target)
        return self.read(_text(raw.get("work_unit_id", raw.get("workUnitId"))) or str(target), cursor)

    def send_message(self, target: Any, envelope: Any) -> HostReceipt:
        raw = mapping_from(target)
        return self.correct(
            envelope,
            _text(raw.get("task_id", raw.get("taskId"))) or "unknown-task",
            _text(raw.get("thread_id", raw.get("threadId"))),
            _text(raw.get("host_id", raw.get("hostId"))),
        )

    def cancel_task(self, target: Any) -> HostReceipt:
        raw = mapping_from(target)
        return HostReceipt(receipt_id="receipt-" + stable_digest(raw), status="unresolved", source="native-subagent", host_id=self.host_id, fallback="cancel-not-supported-by-native-host", payload=raw)


NativeSubagentHostAdapter = NativeSubagentHost
NativeSubagentFallback = NativeSubagentFallbackContract


__all__ = [
    "LaneDirectExecutor",
    "LocalCapabilityUnavailable",
    "NativeSubagentFallback",
    "NativeSubagentFallbackContract",
    "NativeSubagentHost",
    "NativeSubagentHostAdapter",
    "WorkHandoff",
]
