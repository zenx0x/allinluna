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


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _copy(value: Any) -> Any:
    return deepcopy(value)


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
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _text(self.host_id):
            raise ValueError("host_id is required")

    def has_tool(self, tool: str) -> bool:
        return tool in self.tools

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
        return cls(
            host_id=first_text(raw, "host_id", "hostId") or default_host_id,
            host_kind=first_text(raw, "host_kind", "kind") or "unknown",
            available=bool(raw.get("available", True)),
            tools=tuple(sorted({str(item) for item in (tools or ()) if str(item).strip()})),
            native_subagent=raw.get("native_subagent", raw.get("nativeSubagent", capability_raw.get("native_subagent"))),
            receipt_provenance=first_text(raw, "receipt_provenance", "receiptProvenance") or first_text(capability_raw, "receipt_provenance", "receiptProvenance"),
            source=first_text(raw, "source") or first_text(capability_raw, "source"),
            is_real_codex_app=raw.get("is_real_codex_app", raw.get("isRealCodexApp", capability_raw.get("is_real_codex_app"))),
            evidence=_copy(raw),
        )


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
            arguments = self.arguments if isinstance(self.arguments, Mapping) else {}
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
    "DispatchIntent",
    "HOST_RECEIPT_PROTOCOL",
    "DEFAULT_MODEL",
    "DEFAULT_REASONING",
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
    "action_contract_digest",
    "action_contract_material",
    "as_host_action",
    "canonical_json",
    "dispatch_intent",
    "mapping_from",
    "stable_digest",
    "stable_dispatch_key",
]
