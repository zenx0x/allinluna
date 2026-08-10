"""Host adapter contracts and receipt primitives for the vNext runtime.

The host boundary deliberately has two different records:

* :class:`HostAction` is an executable intent.  It may be handed to an
  external Coordinator/Action Bridge when Python cannot call host tools.
* :class:`HostReceipt` is an observation returned by a host.  It is the only
  record that can carry a real thread identity.

The module is dependency-free so the store, scheduler, and test fakes can use
the contract without importing Codex App-specific code.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ...core.protocol import ACTION_BRIDGE_PROTOCOL, DISPATCH_INTENT_PROTOCOL, HOST_RECEIPT_PROTOCOL
from ...core.protocol import DIRECT_WORK_RESULT_PROTOCOL, LANE_DIRECT_WORK_PROTOCOL
from ...resource_observation import ResourceObservation

# Host adapters may leave a route unspecified and let the host choose its
# current default.  Concrete model names belong in host/deployment policy,
# never in Core adapter defaults.
DEFAULT_MODEL: str | None = None
DEFAULT_REASONING: str | None = None
TOP_LEVEL_TASK_EXECUTION_CLASS = "top_level_task"
LOCAL_SUBAGENT_EXECUTION_CLASS = "local_subagent"
DIRECT_EXECUTION_CLASS = "direct"
TOP_LEVEL_CREATE_THREAD_TOOL = "codex_app__create_thread"
LOCAL_DISPATCH_INTENT_PROTOCOL = "local-dispatch-intent/v1"
WORK_HANDOFF_PROTOCOL = "work-handoff/v1"
NATIVE_SUBAGENT_CAPABILITY = "native_subagent"
NATIVE_PREFERRED = "native_preferred"
NATIVE_REQUIRED = "native_required"
DIRECT_ONLY = "direct_only"
LOCAL_EXECUTION_MODES = frozenset({NATIVE_PREFERRED, NATIVE_REQUIRED, DIRECT_ONLY})


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _copy(value: Any) -> Any:
    return deepcopy(value)


def _public_create_target(value: Any) -> Any:
    """Normalize an internal project target to the Desktop public schema.

    Project resolution retains path and branch identity for trust checks, but
    the public ``create_thread`` contract accepts only a typed worktree plus
    an optional ``startingState``.  Keeping this conversion at the exact
    HostAction boundary lets internal planners continue to inspect the full
    resolved identity without leaking unsupported fields into the host call.
    """

    if not isinstance(value, Mapping) or value.get("type") != "project":
        return value
    environment = value.get("environment")
    if not isinstance(environment, Mapping) or environment.get("type") != "worktree":
        return value
    target_environment: dict[str, Any] = {"type": "worktree"}
    starting_state = environment.get("startingState") or environment.get("starting_state")
    if isinstance(starting_state, Mapping):
        if starting_state.get("type") == "branch" and _text(starting_state.get("branchName")):
            target_environment["startingState"] = {
                "type": "branch",
                "branchName": _text(starting_state.get("branchName")),
            }
    else:
        branch = _text(environment.get("branch"))
        if branch:
            target_environment["startingState"] = {"type": "branch", "branchName": branch}
    return {
        "type": "project",
        "projectId": value.get("projectId"),
        "environment": target_environment,
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=lambda item: str(item))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_digest(value: Any, *, length: int = 20) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def action_contract_material(
    *,
    kind: str,
    tool: str | None,
    arguments: Mapping[str, Any],
    task_id: str | None,
    dispatch_id: str | None,
    execution_class: str,
    task_envelope_ref: str | None,
) -> dict[str, Any]:
    """Return the immutable material that defines a host execution opcode."""

    return {
        "kind": kind,
        "tool": tool,
        "arguments": _copy(dict(arguments)),
        "task_id": task_id,
        "dispatch_id": dispatch_id,
        "execution_class": execution_class,
        "task_envelope_ref": task_envelope_ref,
    }


def action_contract_digest(**material: Any) -> str:
    """Return the full deterministic SHA-256 for an exact host action contract."""

    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def mapping_from(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        candidate = method()
        if isinstance(candidate, Mapping):
            return dict(candidate)
    method = getattr(value, "as_dict", None)
    if callable(method):
        candidate = method()
        if isinstance(candidate, Mapping):
            return dict(candidate)
    try:
        return dict(vars(value))
    except TypeError:
        raise TypeError(f"host value must be mapping-like, got {type(value).__name__}")


def first_text(value: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        candidate = value.get(name)
        if candidate is None and "_" in name:
            first, *rest = name.split("_")
            candidate = value.get(first + "".join(part[:1].upper() + part[1:] for part in rest))
        result = _text(candidate)
        if result:
            return result
    return None


def _resource_values(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    model = _text(value.get("model"))
    reasoning = _text(value.get("reasoning", value.get("thinking")))
    return {"model": model, "reasoning": reasoning} if model and reasoning else None


def _valid_observed_at(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


class HostAdapterError(RuntimeError):
    """Base error for fail-closed host adapter boundary failures."""


class HostUnavailableError(HostAdapterError):
    code = "host_unavailable"


class HostReceiptError(HostAdapterError):
    code = "host_receipt_invalid"


class HostActionError(HostAdapterError):
    code = "host_action_invalid"


class _MappingRecord(Mapping[str, Any]):
    def _raw_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self._raw_dict())

    as_dict = to_dict

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __getattr__(self, name: str) -> Any:
        data = self.to_dict()
        if name in data:
            return data[name]
        raise AttributeError(name)


@dataclass(frozen=True, slots=True)
class HostCapabilities(_MappingRecord):
    """Live host capability discovery, never inferred from a requested action."""

    host_id: str
    host_kind: str = "unknown"
    available: bool = True
    tools: tuple[str, ...] = ()
    native_subagent: bool | None = None
    receipt_provenance: str | None = None
    source: str | None = None
    is_real_codex_app: bool | None = None
    logical_capabilities: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _text(self.host_id):
            raise ValueError("host_id is required")

    def has_tool(self, tool: str) -> bool:
        return tool in self.tools

    def logical_capability(self, name: str) -> dict[str, Any]:
        """Return one normalized host-owned logical capability binding.

        Physical tool names come only from discovery/configuration.  Core may
        ask for ``native_subagent`` but never supplies a vendor opcode.
        """

        value = self.logical_capabilities.get(str(name), {})
        if isinstance(value, bool):
            value = {"available": value}
        raw = dict(value) if isinstance(value, Mapping) else {}
        physical = raw.get("physical_tools", raw.get("physicalTools", ()))
        if isinstance(physical, str):
            physical = (physical,)
        if isinstance(physical, Mapping):
            physical = tuple(
                str(tool)
                for tool, descriptor in physical.items()
                if not isinstance(descriptor, Mapping) or descriptor.get("available", True)
            )
        tools = tuple(dict.fromkeys(str(item) for item in (physical or ()) if _text(item)))
        preferred = _text(raw.get("preferred_tool", raw.get("preferredTool")))
        if preferred and preferred not in tools:
            tools = (preferred, *tools)
        available = bool(raw.get("available", bool(tools))) and self.available
        return {
            "available": available,
            "physical_tools": list(tools),
            "preferred_tool": preferred or (tools[0] if tools else None),
            "receipt_contract": _text(
                raw.get("receipt_contract", raw.get("receiptContract"))
            )
            or HOST_RECEIPT_PROTOCOL,
        }

    def _raw_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "host_kind": self.host_kind,
            "available": self.available,
            "tools": list(self.tools),
            "native_subagent": self.native_subagent,
            "receipt_provenance": self.receipt_provenance,
            "source": self.source,
            "is_real_codex_app": self.is_real_codex_app,
            "logical_capabilities": _copy(dict(self.logical_capabilities)),
            "evidence": _copy(self.evidence),
        }

    @classmethod
    def from_value(cls, value: Any, *, default_host_id: str = "unknown-host") -> "HostCapabilities":
        if isinstance(value, cls):
            return value
        raw = mapping_from(value)
        nested = raw.get("capabilities")
        capability_raw = dict(nested) if isinstance(nested, Mapping) else {}
        tools = raw.get("tools", raw.get("thread_tools", capability_raw.get("tools", capability_raw.get("thread_tools", ()))))
        if not tools and capability_raw:
            method_tools = {
                "create_top_level_task": "codex_app__create_thread",
                "wait_tasks": "codex_app__wait_threads",
                "list_tasks": "codex_app__list_threads",
                "read_task": "codex_app__read_thread",
                "send_message": "codex_app__send_message_to_thread",
                "cancel_task": "codex_app__cancel_thread",
            }
            tools = [tool for method, tool in method_tools.items() if capability_raw.get(method) is True]
        if isinstance(tools, Mapping):
            tools = [name for name, item in tools.items() if not isinstance(item, Mapping) or item.get("available", True)]
        if isinstance(tools, str):
            tools = [tools]
        logical = raw.get(
            "logical_capabilities",
            raw.get("logicalCapabilities", capability_raw.get("logical_capabilities", {})),
        )
        logical = dict(logical) if isinstance(logical, Mapping) else {}
        legacy_native = raw.get(
            "native_subagent",
            raw.get("nativeSubagent", capability_raw.get("native_subagent")),
        )
        if NATIVE_SUBAGENT_CAPABILITY not in logical and legacy_native is not None:
            native_tools = raw.get(
                "native_subagent_tools",
                raw.get("nativeSubagentTools", capability_raw.get("native_subagent_tools", ())),
            )
            native_preferred = first_text(
                raw, "native_subagent_tool", "nativeSubagentTool"
            ) or first_text(capability_raw, "native_subagent_tool", "nativeSubagentTool")
            logical[NATIVE_SUBAGENT_CAPABILITY] = {
                "available": bool(legacy_native),
                "physical_tools": native_tools,
                "preferred_tool": native_preferred,
                "receipt_contract": HOST_RECEIPT_PROTOCOL,
            }
        return cls(
            host_id=first_text(raw, "host_id", "hostId") or default_host_id,
            host_kind=first_text(raw, "host_kind", "kind") or "unknown",
            available=bool(raw.get("available", True)),
            tools=tuple(sorted({str(item) for item in (tools or ()) if str(item).strip()})),
            native_subagent=legacy_native,
            receipt_provenance=first_text(raw, "receipt_provenance", "receiptProvenance") or first_text(capability_raw, "receipt_provenance", "receiptProvenance"),
            source=first_text(raw, "source") or first_text(capability_raw, "source"),
            is_real_codex_app=raw.get("is_real_codex_app", raw.get("isRealCodexApp", capability_raw.get("is_real_codex_app"))),
            logical_capabilities=logical,
            evidence=_copy(raw),
        )


@dataclass(frozen=True, slots=True)
class LocalDispatchIntent(_MappingRecord):
    """Logical Lane-local work request emitted before host resolution."""

    work_unit_id: str
    task_id: str
    objective: str
    idempotency_key: str
    attempt_id: str | None = None
    run_ref: str | None = None
    parent_work_unit_id: str | None = None
    logical_capability: str = NATIVE_SUBAGENT_CAPABILITY
    execution_mode: str = NATIVE_PREFERRED
    context_ref: str | None = None
    scope: tuple[str, ...] = ()
    authority: tuple[str, ...] = ()
    ownership: tuple[str, ...] = ()
    resource_envelope: Mapping[str, Any] = field(default_factory=dict)
    checks: tuple[Any, ...] = ()
    artifact_policy: Mapping[str, Any] = field(default_factory=dict)
    return_contract: str = WORK_HANDOFF_PROTOCOL

    def __post_init__(self) -> None:
        for name in ("work_unit_id", "task_id", "objective", "idempotency_key"):
            if not _text(getattr(self, name)):
                raise ValueError(f"local dispatch intent requires {name}")
        if self.execution_mode not in LOCAL_EXECUTION_MODES:
            raise ValueError(f"unknown local execution_mode: {self.execution_mode!r}")
        if self.return_contract != WORK_HANDOFF_PROTOCOL:
            raise ValueError(f"local work must return {WORK_HANDOFF_PROTOCOL}")
        object.__setattr__(self, "scope", tuple(map(str, self.scope)))
        object.__setattr__(self, "authority", tuple(map(str, self.authority)))
        object.__setattr__(self, "ownership", tuple(map(str, self.ownership)))
        object.__setattr__(self, "checks", tuple(_copy(item) for item in self.checks))

    def with_attempt(self, attempt_id: str) -> "LocalDispatchIntent":
        return LocalDispatchIntent(
            work_unit_id=self.work_unit_id,
            task_id=self.task_id,
            objective=self.objective,
            idempotency_key=self.idempotency_key,
            attempt_id=str(attempt_id),
            run_ref=self.run_ref,
            parent_work_unit_id=self.parent_work_unit_id,
            logical_capability=self.logical_capability,
            execution_mode=self.execution_mode,
            context_ref=self.context_ref,
            scope=self.scope,
            authority=self.authority,
            ownership=self.ownership,
            resource_envelope=self.resource_envelope,
            checks=self.checks,
            artifact_policy=self.artifact_policy,
            return_contract=self.return_contract,
        )

    def _raw_dict(self) -> dict[str, Any]:
        return {
            "protocol": LOCAL_DISPATCH_INTENT_PROTOCOL,
            "execution_class": "local_work",
            "run_ref": self.run_ref,
            "task_id": self.task_id,
            "task_ref": f"task://{self.task_id}",
            "work_unit_id": self.work_unit_id,
            "parent_work_unit_id": self.parent_work_unit_id,
            "attempt_id": self.attempt_id,
            "objective": self.objective,
            "logical_capability": self.logical_capability,
            "execution_mode": self.execution_mode,
            "context_ref": self.context_ref,
            "scope": list(self.scope),
            "authority": list(self.authority),
            "ownership": list(self.ownership),
            "resource_envelope": _copy(dict(self.resource_envelope)),
            "checks": [_copy(item) for item in self.checks],
            "artifact_policy": _copy(dict(self.artifact_policy)),
            "return_contract": self.return_contract,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_value(cls, value: Any) -> "LocalDispatchIntent":
        if isinstance(value, cls):
            return value
        raw = mapping_from(value)
        return cls(
            work_unit_id=str(raw.get("work_unit_id") or raw.get("workUnitId") or ""),
            task_id=str(
                raw.get("task_id")
                or raw.get("taskId")
                or str(raw.get("task_ref") or "").removeprefix("task://")
            ),
            objective=str(raw.get("objective") or ""),
            idempotency_key=str(
                raw.get("idempotency_key") or raw.get("idempotencyKey") or ""
            ),
            attempt_id=first_text(raw, "attempt_id", "attemptId"),
            run_ref=first_text(raw, "run_ref", "runRef"),
            parent_work_unit_id=first_text(
                raw, "parent_work_unit_id", "parentWorkUnitId"
            ),
            logical_capability=first_text(
                raw, "logical_capability", "logicalCapability"
            )
            or NATIVE_SUBAGENT_CAPABILITY,
            execution_mode=first_text(raw, "execution_mode", "executionMode")
            or NATIVE_PREFERRED,
            context_ref=first_text(raw, "context_ref", "contextRef"),
            scope=tuple(map(str, raw.get("scope") or ())),
            authority=tuple(map(str, raw.get("authority") or ())),
            ownership=tuple(map(str, raw.get("ownership") or ())),
            resource_envelope=_copy(raw.get("resource_envelope") or {}),
            checks=tuple(_copy(item) for item in (raw.get("checks") or ())),
            artifact_policy=_copy(raw.get("artifact_policy") or {}),
            return_contract=str(raw.get("return_contract") or WORK_HANDOFF_PROTOCOL),
        )


@dataclass(frozen=True, slots=True)
class LaneDirectExecutionPlan(_MappingRecord):
    """Executable Lane-owned alternative to a resolved local HostAction."""

    intent: LocalDispatchIntent
    reason: str = "native-subagent-unavailable"

    def _raw_dict(self) -> dict[str, Any]:
        value = self.intent.to_dict()
        plan = {
            "protocol": LANE_DIRECT_WORK_PROTOCOL,
            "execution_class": "lane_direct",
            "run_ref": value["run_ref"],
            "task_ref": value["task_ref"],
            "task_id": value["task_id"],
            "work_unit_id": value["work_unit_id"],
            "parent_work_unit_id": value["parent_work_unit_id"],
            "attempt_id": value["attempt_id"],
            "objective": value["objective"],
            "context_ref": value["context_ref"],
            "scope": value["scope"],
            "ownership": value["ownership"],
            "authority": value["authority"],
            "resource_envelope": value["resource_envelope"],
            "checks": value["checks"],
            "artifact_policy": value["artifact_policy"],
            "return_contract": value["return_contract"],
            "idempotency_key": value["idempotency_key"],
            "logical_capability": value["logical_capability"],
            "execution_mode": value["execution_mode"],
            "reason": self.reason,
        }
        plan["plan_digest"] = "sha256:" + stable_digest(plan, length=64)
        return plan

    @classmethod
    def from_value(cls, value: Any) -> "LaneDirectExecutionPlan":
        if isinstance(value, cls):
            return value
        raw = mapping_from(value)
        if raw.get("protocol") not in {None, LANE_DIRECT_WORK_PROTOCOL}:
            raise ValueError("lane-direct plan must use lane-direct-work/v1")
        plan = cls(
            LocalDispatchIntent.from_value(raw),
            reason=str(raw.get("reason") or "lane-direct-requested"),
        )
        supplied = raw.get("plan_digest")
        if supplied is not None and str(supplied) != str(plan.to_dict()["plan_digest"]):
            raise ValueError("lane-direct plan digest does not match its immutable plan")
        return plan


@dataclass(frozen=True, slots=True)
class DirectWorkResult(_MappingRecord):
    """An external report of direct work; it never proves completion itself."""

    work_unit_id: str
    attempt_id: str
    idempotency_key: str
    plan_digest: str
    status: str = "reported"
    summary: str = ""
    changed_paths: tuple[str, ...] = ()
    raw_outputs: tuple[Any, ...] = ()
    artifacts: tuple[str, ...] = ()
    exports: tuple[Mapping[str, Any], ...] = ()
    blockers: tuple[Mapping[str, Any], ...] = ()
    result_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("work_unit_id", "attempt_id", "idempotency_key", "plan_digest"):
            if not _text(getattr(self, name)):
                raise ValueError(f"direct work result requires {name}")
        object.__setattr__(self, "changed_paths", tuple(map(str, self.changed_paths)))
        object.__setattr__(self, "artifacts", tuple(map(str, self.artifacts)))
        object.__setattr__(self, "raw_outputs", tuple(_copy(item) for item in self.raw_outputs))
        normalized_exports: list[Mapping[str, Any]] = []
        for item in self.exports:
            if not isinstance(item, Mapping):
                raise TypeError("direct work result exports must be mappings")
            normalized_exports.append(_copy(dict(item)))
        object.__setattr__(self, "exports", tuple(normalized_exports))
        object.__setattr__(self, "blockers", tuple(_copy(item) for item in self.blockers))

    def _material(self) -> dict[str, Any]:
        return {
            "protocol": DIRECT_WORK_RESULT_PROTOCOL,
            "work_unit_id": self.work_unit_id,
            "attempt_id": self.attempt_id,
            "idempotency_key": self.idempotency_key,
            "plan_digest": self.plan_digest,
            "status": self.status,
            "summary": self.summary,
            "changed_paths": list(self.changed_paths),
            "raw_outputs": [_copy(item) for item in self.raw_outputs],
            "artifacts": list(self.artifacts),
            "exports": [_copy(dict(item)) for item in self.exports],
            "blockers": [dict(item) for item in self.blockers],
        }

    def _raw_dict(self) -> dict[str, Any]:
        value = self._material()
        value["result_digest"] = self.result_digest or "sha256:" + stable_digest(value, length=64)
        return value

    @property
    def computed_result_digest(self) -> str:
        return "sha256:" + stable_digest(self._material(), length=64)

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        plan: "LaneDirectExecutionPlan | Mapping[str, Any] | None" = None,
    ) -> "DirectWorkResult":
        if isinstance(value, cls):
            result = value
        else:
            raw = mapping_from(value)
            if raw.get("protocol") not in {None, DIRECT_WORK_RESULT_PROTOCOL}:
                raise ValueError("direct result must use direct-work-result/v1")
            result = cls(
                work_unit_id=str(raw.get("work_unit_id") or raw.get("workUnitId") or ""),
                attempt_id=str(raw.get("attempt_id") or raw.get("attemptId") or ""),
                idempotency_key=str(raw.get("idempotency_key") or raw.get("idempotencyKey") or ""),
                plan_digest=str(raw.get("plan_digest") or raw.get("planDigest") or ""),
                status=str(raw.get("status") or "reported"),
                summary=str(raw.get("summary") or ""),
                changed_paths=tuple(raw.get("changed_paths") or raw.get("changedPaths") or ()),
                raw_outputs=tuple(raw.get("raw_outputs") or raw.get("rawOutputs") or ()),
                artifacts=tuple(str(item) for item in (raw.get("artifacts") or ())),
                exports=tuple(_copy(item) for item in (raw.get("exports") or ())),
                blockers=tuple(item for item in (raw.get("blockers") or ()) if isinstance(item, Mapping)),
                result_digest=str(raw.get("result_digest") or raw.get("resultDigest") or "") or None,
            )
        if plan is not None:
            expected = LaneDirectExecutionPlan.from_value(plan).to_dict()
            if result.plan_digest != expected["plan_digest"]:
                raise ValueError("direct work result plan digest does not match the persisted plan")
            intent = LocalDispatchIntent.from_value(expected)
            for name, actual, expected_value in (
                ("work_unit_id", result.work_unit_id, intent.work_unit_id),
                ("attempt_id", result.attempt_id, intent.attempt_id),
                ("idempotency_key", result.idempotency_key, intent.idempotency_key),
            ):
                if str(actual) != str(expected_value):
                    raise ValueError(f"direct work result {name} does not match the persisted plan")
        if result.result_digest is not None:
            expected_digest = "sha256:" + stable_digest(result._material(), length=64)
            if result.result_digest != expected_digest:
                raise ValueError("direct work result digest does not match its immutable result")
        return result


@dataclass(frozen=True, slots=True)
class HostAction(_MappingRecord):
    """An action/intention sent to a host or Action Bridge.

    ``tool`` is an execution opcode, not a hint.  In particular, a
    ``top_level_task`` must remain a ``codex_app__create_thread`` action until
    it is either receipted or explicitly blocked.  It may never be translated
    into a local subagent or direct/current-thread execution.
    """

    action_id: str
    kind: str
    idempotency_key: str
    tool: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    dispatch_id: str | None = None
    host_id: str | None = None
    expected_receipt: str = HOST_RECEIPT_PROTOCOL
    payload: Mapping[str, Any] = field(default_factory=dict)
    identity: Mapping[str, Any] | None = None
    model: str | None = None
    reasoning: str | None = None
    execution_class: str | None = None
    logical_capability: str | None = None
    tool_policy: Mapping[str, Any] = field(default_factory=dict)
    host_capability_required: str | None = None
    task_envelope_ref: str | None = None
    action_contract_hash: str | None = None

    def __post_init__(self) -> None:
        if not _text(self.action_id) or not _text(self.kind) or not _text(self.idempotency_key):
            raise ValueError("host action requires action_id, kind, and idempotency_key")
        execution_class = _text(self.execution_class)
        if execution_class is None:
            execution_class = (
                TOP_LEVEL_TASK_EXECUTION_CLASS
                if self.kind == "create-top-level-task"
                else LOCAL_SUBAGENT_EXECUTION_CLASS
                if self.kind == "spawn-subagent"
                else DIRECT_EXECUTION_CLASS
            )
            object.__setattr__(self, "execution_class", execution_class)
        if execution_class not in {
            TOP_LEVEL_TASK_EXECUTION_CLASS,
            LOCAL_SUBAGENT_EXECUTION_CLASS,
            DIRECT_EXECUTION_CLASS,
        }:
            raise ValueError(f"unknown execution_class: {execution_class!r}")

        tool = _text(self.tool)
        if execution_class == TOP_LEVEL_TASK_EXECUTION_CLASS and tool is None:
            tool = TOP_LEVEL_CREATE_THREAD_TOOL
            object.__setattr__(self, "tool", tool)
        policy = dict(self.tool_policy) if isinstance(self.tool_policy, Mapping) else {}
        exact_tool = _text(policy.get("exact_tool", policy.get("exactTool"))) or tool
        substitutions = policy.get("substitutions", ())
        if isinstance(substitutions, str):
            substitutions = (substitutions,)
        if not isinstance(substitutions, Sequence):
            raise ValueError("tool_policy.substitutions must be a sequence")
        normalized_policy = {
            "exact_tool": exact_tool,
            "substitutions": [str(item) for item in substitutions if str(item).strip()],
            "on_unavailable": _text(policy.get("on_unavailable", policy.get("onUnavailable")))
            or ("block" if execution_class == TOP_LEVEL_TASK_EXECUTION_CLASS else "adapter-policy"),
        }
        if execution_class == TOP_LEVEL_TASK_EXECUTION_CLASS:
            if self.kind != "create-top-level-task":
                raise ValueError("top_level_task actions must use kind=create-top-level-task")
            if tool != TOP_LEVEL_CREATE_THREAD_TOOL:
                raise ValueError("top_level_task actions must use codex_app__create_thread exactly")
            if normalized_policy["exact_tool"] != tool or normalized_policy["substitutions"]:
                raise ValueError("top_level_task actions forbid tool substitutions")
            if normalized_policy["on_unavailable"] != "block":
                raise ValueError("top_level_task actions must block when the exact tool is unavailable")
            required = _text(self.host_capability_required) or tool
            if required != tool:
                raise ValueError("top_level_task required capability must equal its exact tool")
            object.__setattr__(self, "host_capability_required", required)
            arguments = dict(self.arguments) if isinstance(self.arguments, Mapping) else {}
            if "target" in arguments:
                arguments["target"] = _public_create_target(arguments["target"])
                object.__setattr__(self, "arguments", arguments)
            missing = [
                name for name in ("target", "prompt", "model", "title")
                if name not in arguments
            ]
            if missing:
                raise ValueError(
                    "top_level_task actions require host arguments: "
                    + ", ".join(missing)
                )
            if not isinstance(arguments.get("model"), str) or not arguments["model"].strip():
                raise ValueError("top_level_task model must be a non-empty host model identifier")
        elif execution_class == LOCAL_SUBAGENT_EXECUTION_CLASS:
            logical = _text(self.logical_capability) or (
                NATIVE_SUBAGENT_CAPABILITY if self.kind == "spawn-subagent" else None
            )
            if not logical:
                raise ValueError("local_subagent actions require a logical_capability")
            object.__setattr__(self, "logical_capability", logical)
            if tool is None:
                raise ValueError("local_subagent actions require an adapter-resolved physical tool")
            normalized_policy.update(
                {
                    "exact_after_resolution": True,
                    "on_unavailable": "block",
                }
            )
            if normalized_policy["exact_tool"] != tool or normalized_policy["substitutions"]:
                raise ValueError("resolved local_subagent actions forbid tool substitutions")
            required = _text(self.host_capability_required) or tool
            if required != tool:
                raise ValueError("resolved local_subagent capability must equal its physical tool")
            object.__setattr__(self, "host_capability_required", required)
        object.__setattr__(self, "tool_policy", normalized_policy)

        task_envelope_ref = _text(self.task_envelope_ref)
        if task_envelope_ref is None and isinstance(self.payload, Mapping):
            task_envelope_ref = _text(self.payload.get("task_envelope_ref"))
            if task_envelope_ref is None and isinstance(self.payload.get("task_envelope"), Mapping):
                task_envelope_ref = _text(self.payload["task_envelope"].get("task_envelope_ref"))
            if task_envelope_ref is not None:
                object.__setattr__(self, "task_envelope_ref", task_envelope_ref)
        material = action_contract_material(
            kind=self.kind,
            tool=tool,
            arguments=self.arguments,
            task_id=self.task_id,
            dispatch_id=self.dispatch_id,
            execution_class=execution_class,
            task_envelope_ref=task_envelope_ref,
        )
        digest = action_contract_digest(**material)
        supplied = _text(self.action_contract_hash)
        if supplied is not None and supplied != digest:
            raise ValueError("action_contract_hash does not match immutable action contract material")
        object.__setattr__(self, "action_contract_hash", digest)

    def _raw_dict(self) -> dict[str, Any]:
        arguments = _copy(dict(self.arguments))
        result: dict[str, Any] = {
            "protocol": ACTION_BRIDGE_PROTOCOL,
            "action_id": self.action_id,
            "kind": self.kind,
            "tool": self.tool,
            "arguments": arguments,
            "idempotency_key": self.idempotency_key,
            "expected_receipt": self.expected_receipt,
            "task_id": self.task_id,
            "dispatch_id": self.dispatch_id,
            "host_id": self.host_id,
            "identity": _copy(self.identity),
            "model": self.model,
            "reasoning": self.reasoning,
            "execution_class": self.execution_class,
            "logical_capability": self.logical_capability,
            "tool_policy": _copy(dict(self.tool_policy)),
            "host_capability_required": self.host_capability_required,
            "task_envelope_ref": self.task_envelope_ref,
            "action_contract_hash": self.action_contract_hash,
            "payload": _copy(dict(self.payload)),
        }
        return result

    @classmethod
    def from_value(cls, value: Any, *, kind: str = "host-action") -> "HostAction":
        if isinstance(value, cls):
            return value
        raw = mapping_from(value)
        arguments = raw.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = {
                key: _copy(raw[key])
                for key in ("target", "prompt", "model", "thinking", "title", "threadId", "hostId")
                if key in raw
            }
        action_id = first_text(raw, "action_id", "actionId", "dispatch_id", "dispatchId")
        if not action_id:
            action_id = "action-" + stable_digest({"kind": raw.get("kind", kind), "arguments": arguments})
        idem = first_text(raw, "idempotency_key", "idempotencyKey")
        if not idem:
            idem = "intent:" + stable_digest({"action_id": action_id, "arguments": arguments})
        raw_dispatch = raw.get("dispatch_id", raw.get("dispatchId", ...))
        dispatch_id = _text(raw_dispatch) if raw_dispatch is not ... else action_id
        return cls(
            action_id=action_id,
            kind=first_text(raw, "kind") or kind,
            idempotency_key=idem,
            tool=first_text(raw, "tool"),
            arguments=_copy(dict(arguments)),
            task_id=first_text(raw, "task_id", "taskId"),
            dispatch_id=dispatch_id,
            host_id=first_text(raw, "host_id", "hostId"),
            expected_receipt=first_text(raw, "expected_receipt", "expectedReceipt") or HOST_RECEIPT_PROTOCOL,
            payload=_copy(raw.get("payload", raw)),
            identity=_copy(raw.get("identity")) if isinstance(raw.get("identity"), Mapping) else None,
            model=first_text(raw, "model") or first_text(arguments, "model"),
            reasoning=first_text(raw, "reasoning", "thinking")
            or first_text(arguments, "reasoning", "thinking"),
            execution_class=first_text(raw, "execution_class", "executionClass"),
            logical_capability=first_text(raw, "logical_capability", "logicalCapability"),
            tool_policy=_copy(raw.get("tool_policy", raw.get("toolPolicy", {}))),
            host_capability_required=first_text(raw, "host_capability_required", "hostCapabilityRequired"),
            task_envelope_ref=first_text(raw, "task_envelope_ref", "taskEnvelopeRef"),
            action_contract_hash=first_text(raw, "action_contract_hash", "actionContractHash"),
        )


@dataclass(frozen=True, slots=True)
class HostReceipt(_MappingRecord):
    """A host observation; only a real ``thread_id`` is an active identity."""

    receipt_id: str
    status: str
    source: str | None = None
    host_id: str | None = None
    action_id: str | None = None
    action_kind: str | None = None
    idempotency_key: str | None = None
    dispatch_id: str | None = None
    task_id: str | None = None
    thread_id: str | None = None
    client_thread_id: str | None = None
    actual: bool = False
    actual_tool: str | None = None
    actual_capability: str | None = None
    action_contract_hash: str | None = None
    model: str | None = None
    reasoning: str | None = None
    duplicate_of: str | None = None
    fallback: str | None = None
    model_receipt: str = "unresolved"
    resource_receipt: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    runtime_evidence: Mapping[str, Any] = field(default_factory=dict)
    action: Mapping[str, Any] | None = None
    worktree: str | None = None
    branch: str | None = None
    base_commit: str | None = None

    def __post_init__(self) -> None:
        # Requested action data and observed host identity are different trust
        # domains.  In particular, do not derive actual_tool,
        # actual_capability, or action_contract_hash from ``self.action`` here.
        # A directly-called trusted HostAdapter may sign those fields before
        # constructing this record; external ingest must provide them.
        return None

    @property
    def is_active_identity(self) -> bool:
        return self.actual and bool(self.thread_id)

    def _raw_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "protocol": HOST_RECEIPT_PROTOCOL,
            "receipt_id": self.receipt_id,
            "status": self.status,
            "source": self.source,
            "host_id": self.host_id,
            "action_id": self.action_id,
            "action_kind": self.action_kind,
            "idempotency_key": self.idempotency_key,
            "dispatch_id": self.dispatch_id,
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "client_thread_id": self.client_thread_id,
            "actual": self.actual,
            "actual_tool": self.actual_tool,
            "actual_capability": self.actual_capability,
            "action_contract_hash": self.action_contract_hash,
            "model": self.model,
            "reasoning": self.reasoning,
            "duplicate_of": self.duplicate_of,
            "fallback": self.fallback,
            "model_receipt": self.model_receipt,
            "resource_receipt": _copy(dict(self.resource_receipt)),
            "worktree": self.worktree,
            "branch": self.branch,
            "base_commit": self.base_commit,
            "payload": _copy(dict(self.payload)),
            "runtime_evidence": _copy(dict(self.runtime_evidence)),
            "action": _copy(dict(self.action)) if self.action is not None else None,
        }
        return result

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        action: HostAction | Mapping[str, Any] | None = None,
        default_source: str | None = None,
        default_host_id: str | None = None,
    ) -> "HostReceipt":
        if isinstance(value, cls):
            return value
        raw = mapping_from(value)
        action_obj = HostAction.from_value(action) if action is not None else None
        thread_id = first_text(raw, "thread_id", "threadId")
        client_id = first_text(raw, "client_thread_id", "clientThreadId")
        status = first_text(raw, "status") or ("active" if thread_id else "pending" if client_id else "unresolved")
        actual = bool(raw.get("actual", bool(thread_id))) and bool(thread_id)
        receipt_id = first_text(raw, "receipt_id", "receiptId")
        if not receipt_id:
            receipt_id = "receipt-" + stable_digest({"raw": raw, "action": action_obj.to_dict() if action_obj else None})
        runtime = raw.get("runtime_evidence", raw.get("runtimeEvidence", {}))
        if not isinstance(runtime, Mapping):
            runtime = {}
        actual_payload = raw.get("actual")
        if not isinstance(actual_payload, Mapping):
            actual_payload = {}
        resource_raw = raw.get("resource_receipt", raw.get("resourceReceipt", {}))
        if not isinstance(resource_raw, Mapping):
            resource_raw = {}
        resource_actual = _resource_values(resource_raw.get("actual"))
        source = first_text(raw, "source") or default_source
        action_id = first_text(raw, "action_id", "actionId") or (action_obj.action_id if action_obj else None)
        idem = first_text(raw, "idempotency_key", "idempotencyKey") or (action_obj.idempotency_key if action_obj else None)
        dispatch = first_text(raw, "dispatch_id", "dispatchId") or (action_obj.dispatch_id if action_obj else None)
        actual_model = resource_actual["model"] if resource_actual else None
        actual_reasoning = resource_actual["reasoning"] if resource_actual else None
        runtime_requested = (
            runtime.get("requested") if isinstance(runtime.get("requested"), Mapping) else {}
        )
        runtime_requested_resource = (
            runtime_requested.get("resource")
            if isinstance(runtime_requested.get("resource"), Mapping)
            else runtime_requested
        )
        runtime_resolved = (
            runtime.get("resolved") if isinstance(runtime.get("resolved"), Mapping) else {}
        )
        resolved_resource = (
            runtime_resolved.get("resource")
            if isinstance(runtime_resolved.get("resource"), Mapping)
            else runtime_resolved
        )
        action_resource_receipt = (
            action_obj.payload.get("resource_receipt", {})
            if action_obj and isinstance(action_obj.payload.get("resource_receipt"), Mapping)
            else {}
        )
        baseline_requested_metadata = (
            dict(action_resource_receipt.get("requested"))
            if isinstance(action_resource_receipt.get("requested"), Mapping)
            else dict(runtime_requested_resource) if isinstance(runtime_requested_resource, Mapping) else {}
        )
        baseline_requested = _resource_values(baseline_requested_metadata)
        baseline_requested = baseline_requested or _resource_values(
            {"model": action_obj.model, "reasoning": action_obj.reasoning}
            if action_obj else None
        )
        if baseline_requested:
            baseline_requested_metadata.update(baseline_requested)
        baseline_resolved_metadata = (
            dict(action_resource_receipt.get("resolved"))
            if isinstance(action_resource_receipt.get("resolved"), Mapping)
            else dict(resolved_resource) if isinstance(resolved_resource, Mapping) else {}
        )
        baseline_resolved = _resource_values(baseline_resolved_metadata)
        baseline_resolved = baseline_resolved or _resource_values(
            {"model": action_obj.model, "reasoning": action_obj.reasoning}
            if action_obj else None
        )
        if baseline_resolved:
            baseline_resolved_metadata.update(baseline_resolved)
        reported_requested = _resource_values(resource_raw.get("requested"))
        reported_resolved = _resource_values(resource_raw.get("resolved"))
        diagnostics = resource_raw.get("diagnostics")
        evidence_source = (
            first_text(resource_raw, "evidence_source", "evidenceSource")
            or first_text(resource_actual or {}, "evidence_source", "evidenceSource", "source")
            or first_text(raw, "resource_evidence_source", "resourceEvidenceSource")
        )
        observed_at = (
            first_text(resource_raw, "observed_at", "observedAt")
            or first_text(resource_actual or {}, "observed_at", "observedAt")
            or first_text(raw, "resource_observed_at", "resourceObservedAt")
        )
        reported_actual_tool = (
            first_text(raw, "actual_tool", "actualTool")
            or first_text(actual_payload, "actual_tool", "actualTool")
            or first_text(runtime, "actual_tool", "actualTool")
        )
        reported_actual_capability = (
            first_text(raw, "actual_capability", "actualCapability")
            or first_text(actual_payload, "actual_capability", "actualCapability")
            or first_text(runtime, "actual_capability", "actualCapability")
        )
        reported_action_contract_hash = (
            first_text(raw, "action_contract_hash", "actionContractHash")
            or first_text(actual_payload, "action_contract_hash", "actionContractHash")
            or first_text(runtime, "action_contract_hash", "actionContractHash")
        )
        effective_resolved = reported_resolved or baseline_resolved
        verified_model_receipt = bool(
            actual
            and first_text(resource_raw, "actual_state", "actualState") == "resolved"
            and baseline_requested
            and reported_requested == baseline_requested
            and reported_resolved
            and actual_model == effective_resolved["model"]
            and actual_reasoning == effective_resolved["reasoning"]
            and evidence_source
            and _valid_observed_at(observed_at)
        )
        reported_model_receipt = first_text(raw, "model_receipt", "modelReceipt")
        if verified_model_receipt:
            model_receipt = "real"
        elif reported_model_receipt in (None, "real"):
            model_receipt = "unresolved"
        else:
            model_receipt = reported_model_receipt
        canonical_resource_receipt = ResourceObservation(
            requested=baseline_requested_metadata or {"model": None, "reasoning": None},
            resolved=(baseline_resolved_metadata | (effective_resolved or {})) or {"model": None, "reasoning": None},
            actual={"model": actual_model, "reasoning": actual_reasoning} if verified_model_receipt else None,
            actual_state="resolved" if verified_model_receipt else "unresolved",
            evidence_source=evidence_source if verified_model_receipt else None,
            observed_at=observed_at if verified_model_receipt else None,
            diagnostics=diagnostics if isinstance(diagnostics, Mapping) else None,
        ).to_dict()
        return cls(
            receipt_id=receipt_id,
            status=status,
            source=source,
            host_id=first_text(raw, "host_id", "hostId") or default_host_id,
            action_id=action_id,
            action_kind=first_text(raw, "action_kind", "kind") or (action_obj.kind if action_obj else None),
            idempotency_key=idem,
            dispatch_id=dispatch,
            task_id=first_text(raw, "task_id", "taskId") or (action_obj.task_id if action_obj else None),
            thread_id=thread_id,
            client_thread_id=client_id if not thread_id else client_id,
            actual=actual,
            actual_tool=reported_actual_tool,
            actual_capability=reported_actual_capability,
            action_contract_hash=reported_action_contract_hash,
            model=actual_model if verified_model_receipt else None,
            reasoning=actual_reasoning if verified_model_receipt else None,
            duplicate_of=first_text(raw, "duplicate_of", "duplicateOf"),
            fallback=first_text(raw, "fallback"),
            model_receipt=model_receipt,
            resource_receipt=canonical_resource_receipt,
            payload=_copy(raw),
            runtime_evidence=_copy(dict(runtime)),
            action=action_obj.to_dict() if action_obj else None,
            worktree=first_text(raw, "worktree", "worktreePath"),
            branch=first_text(raw, "branch"),
            base_commit=first_text(raw, "base_commit", "baseCommit"),
        )


@dataclass(frozen=True, slots=True)
class DispatchIntent(_MappingRecord):
    """Persistable intent emitted before invoking a host."""

    action: HostAction
    emitted_at: str
    dispatcher_epoch: int | None = None
    dispatcher_owner_identity: Mapping[str, Any] | None = None

    def _raw_dict(self) -> dict[str, Any]:
        action = self.action.to_dict()
        return {
            "protocol": DISPATCH_INTENT_PROTOCOL,
            "kind": "dispatch-intent",
            "status": "emitted",
            "dispatch_id": self.action.dispatch_id or self.action.action_id,
            "emitted_at": self.emitted_at,
            "action_id": self.action.action_id,
            "task_id": self.action.task_id,
            "idempotency_key": self.action.idempotency_key,
            "target": _copy(self.action.arguments.get("target")),
            "model": self.action.model,
            "reasoning": self.action.reasoning,
            "idempotency_material": _copy(self.action.payload.get("idempotency_material")),
            "identity": _copy(self.action.identity),
            "dispatcher_epoch": self.dispatcher_epoch,
            "dispatcher_owner_identity": _copy(self.dispatcher_owner_identity),
            "original_action": action,
            "expected_receipt": HOST_RECEIPT_PROTOCOL,
        }


def stable_dispatch_key(
    *,
    task_id: str,
    dispatch_identifier: str,
    repository_identity: Any = None,
    worktree_identity: Any = None,
) -> tuple[str, dict[str, Any]]:
    material = {
        "task_id": task_id,
        "dispatch_id": dispatch_identifier,
        "repository_identity": _copy(repository_identity),
        "worktree_identity": _copy(worktree_identity),
    }
    safe_task = re.sub(r"[^A-Za-z0-9._-]+", "-", str(task_id)).strip("-") or "task"
    safe_dispatch = re.sub(r"[^A-Za-z0-9._-]+", "-", str(dispatch_identifier)).strip("-") or "dispatch"
    return f"intent:{safe_task}:{safe_dispatch}:{stable_digest(material)}", material


def as_host_action(value: Any, *, kind: str = "host-action") -> HostAction:
    return HostAction.from_value(value, kind=kind)


def dispatch_intent(
    action: HostAction | Mapping[str, Any],
    *,
    emitted_at: str,
    lease: Mapping[str, Any] | None = None,
) -> DispatchIntent:
    lease = lease or {}
    return DispatchIntent(
        action=as_host_action(action),
        emitted_at=emitted_at,
        dispatcher_epoch=lease.get("epoch"),
        dispatcher_owner_identity=_copy(lease.get("owner_identity")),
    )


@runtime_checkable
class HostAdapter(Protocol):
    """Stable host protocol consumed by Core."""

    def discover(self) -> HostCapabilities: ...

    def create_top_level_task(self, action: HostAction | Mapping[str, Any]) -> HostReceipt: ...

    def wait_tasks(self, targets: Sequence[Any], cursor: str | None = None) -> HostReceipt: ...

    def read_task(self, target: Any, cursor: str | None = None) -> HostReceipt: ...

    def send_message(self, target: Any, envelope: Any) -> HostReceipt: ...

    def cancel_task(self, target: Any) -> HostReceipt: ...


HostAdapterAPI = HostAdapter
HostReceiptAPI = HostReceipt


__all__ = [
    "ACTION_BRIDGE_PROTOCOL",
    "DIRECT_EXECUTION_CLASS",
    "DISPATCH_INTENT_PROTOCOL",
    "DIRECT_WORK_RESULT_PROTOCOL",
    "DispatchIntent",
    "HOST_RECEIPT_PROTOCOL",
    "DEFAULT_MODEL",
    "DEFAULT_REASONING",
    "DIRECT_ONLY",
    "LANE_DIRECT_WORK_PROTOCOL",
    "LaneDirectExecutionPlan",
    "DirectWorkResult",
    "LOCAL_DISPATCH_INTENT_PROTOCOL",
    "LOCAL_EXECUTION_MODES",
    "LocalDispatchIntent",
    "NATIVE_PREFERRED",
    "NATIVE_REQUIRED",
    "NATIVE_SUBAGENT_CAPABILITY",
    "HostAction",
    "HostActionError",
    "HostAdapter",
    "HostAdapterAPI",
    "HostAdapterError",
    "HostCapabilities",
    "HostReceipt",
    "HostReceiptAPI",
    "HostReceiptError",
    "HostUnavailableError",
    "LOCAL_SUBAGENT_EXECUTION_CLASS",
    "TOP_LEVEL_CREATE_THREAD_TOOL",
    "TOP_LEVEL_TASK_EXECUTION_CLASS",
    "WORK_HANDOFF_PROTOCOL",
    "action_contract_digest",
    "action_contract_material",
    "as_host_action",
    "canonical_json",
    "dispatch_intent",
    "mapping_from",
    "stable_digest",
    "stable_dispatch_key",
]
