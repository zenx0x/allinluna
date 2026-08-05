from __future__ import annotations

import pytest


COMMON_REASONING_LEVELS = ("low", "medium", "high", "xhigh", "max")


@pytest.mark.parametrize("reasoning", COMMON_REASONING_LEVELS + ("future-ultra",))
def test_resource_broker_accepts_configured_models_and_reasoning(vnext_module, reasoning):
    resource = vnext_module("resource")
    broker = resource.ResourceBroker({"model": "gpt-5.3-codex-spark", "reasoning": reasoning})

    receipt = broker.resolve(
        {"model": "custom-model", "reasoning": reasoning},
        actual_receipt={"model": "custom-model", "reasoning": reasoning},
    )

    assert receipt.requested == {"model": "custom-model", "reasoning": reasoning}
    assert receipt.resolved["model"] == "custom-model"
    assert receipt.resolved["reasoning"] == reasoning
    # A policy resolver has no host authority.  Supplying route-shaped data
    # without a source/timestamp must not turn it into an observed actual.
    assert receipt.actual is None
    assert receipt.actual_state == "unresolved"
    observed = broker.observe_receipt(
        {
            "requested": {"model": "custom-model", "reasoning": reasoning},
            "resolved": {"model": "custom-model", "reasoning": reasoning},
            "actual": {"model": "custom-model", "reasoning": reasoning},
            "actual_state": "resolved",
            "evidence_source": "host-runtime",
            "observed_at": "2026-08-05T12:00:00Z",
        }
    )
    assert observed.actual == {"model": "custom-model", "reasoning": reasoning}
    assert observed.actual_state == "resolved"


def test_resource_broker_rejects_only_empty_resource_identifiers(vnext_module):
    broker_type = vnext_module("resource").ResourceBroker

    with pytest.raises(ValueError, match="model must be a non-empty string"):
        broker_type({"model": " "})
    with pytest.raises(ValueError, match="reasoning must be a non-empty string"):
        broker_type({"reasoning": ""})


def test_codex_app_action_boundary_accepts_configured_resource_routes(vnext_module):
    create_action = vnext_module("adapters.host.codex_app").create_thread_action
    routes = (
        ("gpt-5.6-luna", "medium"),
        ("gpt-5.6-luna", "xhigh"),
        ("gpt-5.6-luna", "max"),
        ("gpt-5.3-codex-spark", "high"),
    )
    for index, (model, thinking) in enumerate(routes):
        action = create_action(
            kind="acceptance",
            entity_id=f"route-{index}",
            prompt="return host identity",
            target={"type": "project"},
            model=model,
            thinking=thinking,
            title=f"route {index}",
            record_with="host-receipt/v1",
        )
        assert action["model"] == model
        assert action["thinking"] == thinking


def test_allocations_honor_per_request_overrides_without_changing_external_policy(vnext_module):
    resource = vnext_module("resource")
    broker = resource.ResourceBroker(
        {
            "model": "default-model",
            "reasoning": "medium",
            "external_action_policy": "deny",
            "top_level_slots": 2,
            "total_subagent_slots": 2,
        }
    )

    top = broker.allocate_top_level_slots(
        [
            {
                "id": "task-custom",
                "resource_envelope": {
                    "model": "gpt-5.6-luna",
                    "reasoning": "max",
                    "external_action_policy": "allow",
                },
            },
            {"id": "task-default"},
        ]
    )
    lane = broker.allocate_lane_slots(
        "lane-a",
        [
            {
                "id": "work-custom",
                "resource_envelope": {
                    "model": "gpt-5.3-codex-spark",
                    "reasoning": "low",
                },
            }
        ],
    )

    assert (top[0].model, top[0].reasoning) == ("gpt-5.6-luna", "max")
    assert (top[1].model, top[1].reasoning) == ("default-model", "medium")
    assert (lane[0].model, lane[0].reasoning) == ("gpt-5.3-codex-spark", "low")
    assert all(item.external_action_policy == "deny" for item in [*top, *lane])
    assert top[0].receipt.requested["external_action_policy"] == "allow"
    assert top[0].receipt.resolved["external_action_policy"] == "deny"


def _live_discovery(capability_id):
    return {
        "id": capability_id,
        "status": "available",
        "source": "host-probe",
        "observed_at": "2026-08-05T00:00:00Z",
    }


def _granted_permission(*_args):
    return {"status": "granted", "source": "jit-permission"}


def test_capability_invocation_verifies_actual_against_this_request(vnext_module):
    adapter_type = vnext_module("adapters.capability.adapter").RegistryCapabilityAdapter

    def invoke(_capability_id, action):
        return {
            "protocol": "host-receipt/v1",
            "receipt_id": "receipt-custom-resource",
            "source": "codex-app",
            "actual_tool": "tool.search",
            "status": "completed",
            "actual": {
                "tool": "tool.search",
                "model": action["model"],
                "reasoning": action["reasoning"],
            },
        }

    adapter = adapter_type(
        [{"id": "tool.search", "kind": "tool"}],
        discovery_provider=_live_discovery,
        permission_provider=_granted_permission,
        invoker=invoke,
    )
    result = adapter.invoke(
        "tool.search",
        {"query": "configured"},
        model="gpt-5.3-codex-spark",
        reasoning="low",
    )

    evidence = result["runtime_evidence"]
    assert result["model_receipt"] == "real"
    assert evidence["requested"]["resource"] == {
        "model": "gpt-5.3-codex-spark",
        "reasoning": "low",
    }
    assert evidence["resolved"]["resource"] == evidence["requested"]["resource"]
    assert evidence["actual"]["model"] == "gpt-5.3-codex-spark"
    assert evidence["actual"]["reasoning"] == "low"


def test_capability_invocation_keeps_mismatched_actual_unresolved(vnext_module):
    adapter_type = vnext_module("adapters.capability.adapter").RegistryCapabilityAdapter
    adapter = adapter_type(
        [{"id": "tool.search", "kind": "tool"}],
        discovery_provider=_live_discovery,
        permission_provider=_granted_permission,
        invoker=lambda *_args: {
            "protocol": "host-receipt/v1",
            "receipt_id": "receipt-mismatch",
            "source": "codex-app",
            "actual_tool": "tool.search",
            "status": "completed",
            "actual": {"model": "different-model", "reasoning": "max"},
        },
    )

    result = adapter.invoke(
        "tool.search", {}, model="requested-model", reasoning="medium"
    )

    assert result["model_receipt"] == "unresolved"
    assert result["blocker"] == "real-model-receipt-missing-or-mismatched"
    assert result["runtime_evidence"]["requested"]["resource"] == {
        "model": "requested-model",
        "reasoning": "medium",
    }
    assert result["runtime_evidence"]["actual"] is None


def test_host_receipt_uses_action_request_as_verification_baseline(vnext_module):
    host = vnext_module("adapters.host.base")
    action = host.HostAction(
        action_id="action-1",
        kind="create-task",
        idempotency_key="intent-1",
        model="custom-model",
        reasoning="xhigh",
    )

    matching = host.HostReceipt.from_value(
        {
            "thread_id": "thread-1",
            "actual": {"model": "custom-model", "reasoning": "xhigh"},
            "resource_receipt": {
                "requested": {"model": "custom-model", "reasoning": "xhigh"},
                "resolved": {"model": "custom-model", "reasoning": "xhigh"},
                "actual": {"model": "custom-model", "reasoning": "xhigh"},
                "actual_state": "resolved",
                "evidence_source": "codex-host-runtime",
                "observed_at": "2026-08-05T12:00:00Z",
            },
        },
        action=action,
    )
    mismatched = host.HostReceipt.from_value(
        {
            "thread_id": "thread-2",
            "actual": {"model": "custom-model", "reasoning": "high"},
            "model_receipt": "real",
        },
        action=action,
    )

    assert matching.model_receipt == "real"
    assert matching.resource_receipt["actual_state"] == "resolved"
    assert matching.resource_receipt["evidence_source"] == "codex-host-runtime"
    assert mismatched.model_receipt == "unresolved"


@pytest.mark.parametrize(
    "field,value",
    [
        ("requested", {"model": "other-model", "reasoning": "xhigh"}),
        ("resolved", {"model": "custom-model", "reasoning": "high"}),
        ("actual", {"model": "custom-model", "reasoning": "high"}),
    ],
)
def test_host_receipt_requires_requested_resolved_actual_triple_match(vnext_module, field, value):
    host = vnext_module("adapters.host.base")
    action = host.HostAction(
        action_id="action-triple",
        kind="create-task",
        idempotency_key="intent-triple",
        model="custom-model",
        reasoning="xhigh",
    )
    resource_receipt = {
        "requested": {"model": "custom-model", "reasoning": "xhigh"},
        "resolved": {"model": "custom-model", "reasoning": "xhigh"},
        "actual": {"model": "custom-model", "reasoning": "xhigh"},
        "actual_state": "resolved",
        "evidence_source": "codex-host-runtime",
        "observed_at": "2026-08-05T12:00:00Z",
    }
    resource_receipt[field] = value
    receipt = host.HostReceipt.from_value(
        {"thread_id": "thread-triple", "resource_receipt": resource_receipt},
        action=action,
    )
    assert receipt.model_receipt == "unresolved"
    assert receipt.resource_receipt["actual"] is None
    assert receipt.resource_receipt["evidence_source"] is None


def test_host_receipt_rejects_invalid_observation_timestamp(vnext_module):
    host = vnext_module("adapters.host.base")
    action = host.HostAction(
        action_id="action-time", kind="create-task", idempotency_key="intent-time",
        model="custom-model", reasoning="medium",
    )
    receipt = host.HostReceipt.from_value(
        {
            "thread_id": "thread-time",
            "resource_receipt": {
                "requested": {"model": "custom-model", "reasoning": "medium"},
                "resolved": {"model": "custom-model", "reasoning": "medium"},
                "actual": {"model": "custom-model", "reasoning": "medium"},
                "actual_state": "resolved",
                "evidence_source": "codex-host-runtime",
                "observed_at": "not-a-timestamp",
            },
        },
        action=action,
    )
    assert receipt.model_receipt == "unresolved"


def test_action_bridge_does_not_accept_receipt_as_its_own_resource_baseline(vnext_module, tmp_path):
    bridge_type = vnext_module("engine.action_bridge").ActionBridge
    store_type = vnext_module("store").Store
    raw = {
        "receipt_id": "self-attested",
        "dispatch_key": "unknown-intent",
        "thread_id": "thread-self-attested",
        "resource_receipt": {
            "requested": {"model": "self-model", "reasoning": "max"},
            "resolved": {"model": "self-model", "reasoning": "max"},
            "actual": {"model": "self-model", "reasoning": "max"},
            "actual_state": "resolved",
            "evidence_source": "untrusted-receipt",
            "observed_at": "2026-08-05T12:00:00Z",
        },
    }
    with store_type(tmp_path / "self-attested.db") as store:
        result = bridge_type(store).ingest_receipt(raw)
    assert result["resource_receipt"]["actual_state"] == "unresolved"


def test_action_bridge_rehydrates_trusted_resource_baseline_from_outbox(vnext_module, tmp_path):
    bridge_type = vnext_module("engine.action_bridge").ActionBridge
    coordinator_type = vnext_module("engine.coordinator").CoordinatorEngine
    broker_type = vnext_module("resource").ResourceBroker
    scheduler_type = vnext_module("scheduler.global_scheduler").GlobalScheduler
    store_type = vnext_module("store").Store
    with store_type(tmp_path / "persisted-action.db") as store:
        store.create_run(
            "run-persisted-action", "verify persisted action", {"model": "route-model", "reasoning": "low"}, "contract://root@1"
        )
        store.create_task(
            {
                "id": "persisted-action-task", "run_id": "run-persisted-action",
                "outcome": "verify receipt", "state": "ready",
                "resource_envelope": {"model": "route-model", "reasoning": "low"},
            }
        )
        action = scheduler_type(store).step("run-persisted-action")[0]
        values = {"model": action.model, "reasoning": action.reasoning}
        result = bridge_type(store).ingest_receipt(
            {
                "receipt_id": "persisted-action-receipt",
                "dispatch_key": action.idempotency_key,
                "thread_id": "persisted-action-thread",
                "status": "active",
                "actual": True,
                "actual_tool": action.tool,
                "actual_capability": action.host_capability_required,
                "action_contract_hash": action.action_contract_hash,
                "resource_receipt": {
                    "requested": dict(values), "resolved": dict(values), "actual": dict(values),
                    "actual_state": "resolved", "evidence_source": "codex-host-runtime",
                    "observed_at": "2026-08-05T12:00:00Z",
                },
            }
        )
        status = coordinator_type(
            store, resource_broker=broker_type({"model": "new-default", "reasoning": "max"})
        ).status("run-persisted-action")
    assert result["resource_receipt"]["actual_state"] == "resolved"
    task_status = status["tasks"][0]
    assert (task_status["requested_model"], task_status["requested_reasoning"]) == ("route-model", "low")
    assert (task_status["resolved_model"], task_status["resolved_reasoning"]) == ("route-model", "low")
    assert (task_status["actual_model"], task_status["actual_reasoning"]) == ("route-model", "low")


def test_store_promotes_late_same_status_evidence_and_keeps_it_immutable(vnext_module, tmp_path):
    store_type = vnext_module("store").Store
    base = {
        "receipt_id": "late-evidence",
        "dispatch_key": "late-evidence-intent",
        "host_adapter": "codex-app",
        "thread_id": "thread-late-evidence",
        "status": "active",
        "resource_receipt": {
            "requested": {"model": "gpt-5.6-luna", "reasoning": "medium"},
            "resolved": {"model": "gpt-5.6-luna", "reasoning": "medium"},
            "actual": None,
            "actual_state": "unresolved",
            "evidence_source": None,
            "observed_at": None,
        },
    }
    resolved = {
        **base,
        "resource_receipt": {
            **base["resource_receipt"],
            "actual": {"model": "gpt-5.6-luna", "reasoning": "medium"},
            "actual_state": "resolved",
            "evidence_source": "codex-host-runtime",
            "observed_at": "2026-08-05T12:00:00Z",
        },
    }
    conflict = {
        **resolved,
        "status": "completed",
        "resource_receipt": {
            **resolved["resource_receipt"],
            "requested": {"model": "other-model", "reasoning": "max"},
            "resolved": {"model": "other-model", "reasoning": "max"},
            "actual": {"model": "other-model", "reasoning": "max"},
        },
    }
    with store_type(tmp_path / "late-evidence.db") as store:
        assert store.ingest_receipt(base)["resource_receipt"]["actual_state"] == "unresolved"
        assert store.ingest_receipt(resolved)["resource_receipt"]["actual_state"] == "resolved"
        store.ingest_receipt(conflict)
        persisted = store.get_host_receipt("late-evidence")
    assert persisted["actual_model"] == "gpt-5.6-luna"
    assert persisted["actual_reasoning"] == "medium"


def test_store_persists_queryable_actual_resource_receipt(vnext_module, tmp_path):
    host = vnext_module("adapters.host.base")
    store_type = vnext_module("store").Store
    action = host.HostAction(
        action_id="action-store-resource",
        kind="create-task",
        idempotency_key="intent-store-resource",
        model="gpt-5.6-luna",
        reasoning="medium",
    )
    receipt = host.HostReceipt.from_value(
        {
            "thread_id": "thread-store-resource",
            "source": "codex-app",
            "actual": {"model": "gpt-5.6-luna", "reasoning": "medium"},
            "resource_receipt": {
                "requested": {"model": "gpt-5.6-luna", "reasoning": "medium"},
                "resolved": {"model": "gpt-5.6-luna", "reasoning": "medium"},
                "actual": {"model": "gpt-5.6-luna", "reasoning": "medium"},
                "actual_state": "resolved",
                "evidence_source": "codex-host-runtime",
                "observed_at": "2026-08-05T12:00:00Z",
            },
        },
        action=action,
    )
    with store_type(tmp_path / "receipt.db") as store:
        ingested = store.ingest_receipt(receipt.to_dict())
        persisted = store.get_host_receipt(receipt.receipt_id)
    assert ingested["resource_receipt"]["actual_state"] == "resolved"
    assert persisted["actual_model"] == "gpt-5.6-luna"
    assert persisted["actual_reasoning"] == "medium"
    assert persisted["requested_model"] == "gpt-5.6-luna"
    assert persisted["resolved_model"] == "gpt-5.6-luna"
    assert persisted["resource_receipt"]["requested"] == {"model": "gpt-5.6-luna", "reasoning": "medium"}
    assert persisted["resource_receipt"]["resolved"] == {"model": "gpt-5.6-luna", "reasoning": "medium"}
    assert persisted["resource_receipt"]["evidence_source"] == "codex-host-runtime"


def test_unresolved_actual_does_not_block_host_result_or_root_completion(vnext_module, tmp_path):
    host = vnext_module("adapters.host.base")
    bridge_type = vnext_module("engine.action_bridge").ActionBridge
    coordinator_type = vnext_module("engine.coordinator").CoordinatorEngine
    store_type = vnext_module("store").Store
    action = host.HostAction(
        action_id="action-no-telemetry",
        kind="create-task",
        idempotency_key="intent-no-telemetry",
        task_id="task-no-telemetry",
        model="requested-model",
        reasoning="medium",
    )
    with store_type(tmp_path / "no-telemetry.db") as store:
        store.create_run("run-no-telemetry", "complete without resource diagnostics")
        store.create_task(
            {
                "id": "task-no-telemetry",
                "run_id": "run-no-telemetry",
                "outcome": "deliver result",
                "state": "ready",
            }
        )
        ingested = bridge_type(store).ingest_receipt(
            {
                "receipt_id": "receipt-no-telemetry",
                "thread_id": "thread-no-telemetry",
                "status": "completed",
            },
            action=action,
        )
        assert ingested["resource_receipt"] == {
            "requested": {"model": "requested-model", "reasoning": "medium"},
            "resolved": {"model": "requested-model", "reasoning": "medium"},
            "actual": None,
            "actual_state": "unresolved",
            "evidence_source": None,
            "observed_at": None,
        }
        store.update_task_status("task-no-telemetry", "dispatching")
        store.update_task_status("task-no-telemetry", "active")
        store.update_task_status("task-no-telemetry", "verifying")
        store.update_task_status("task-no-telemetry", "completed")
        coordinator_type(store).reconcile("run-no-telemetry")
        receipt = store.get_host_receipt("receipt-no-telemetry")
        run = store.get_run("run-no-telemetry")
    assert receipt["resource_receipt"]["actual"] is None
    assert receipt["resource_receipt"]["actual_state"] == "unresolved"
    assert run["status"] == "completed"
