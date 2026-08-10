from __future__ import annotations

import pytest
from allinluna_runtime.adapters.host.base import (
    HostAction,
    LaneDirectExecutionPlan,
    LocalDispatchIntent,
)
from allinluna_runtime.adapters.host.native_subagent import (
    LocalCapabilityUnavailable,
    NativeSubagentFallbackContract,
    NativeSubagentHost,
)
from allinluna_runtime.scheduler.local_scheduler import LocalScheduler
from allinluna_runtime.store import Store


def _intent(*, mode: str = "native_preferred") -> LocalDispatchIntent:
    return LocalDispatchIntent(
        run_ref="run://run-local",
        task_id="task-local",
        work_unit_id="work-local",
        attempt_id="work-attempt-local",
        objective="complete local work",
        logical_capability="native_subagent",
        execution_mode=mode,
        context_ref="context://task/task-local",
        scope=("tests/**",),
        authority=("read", "write", "report"),
        ownership=("tests/**",),
        idempotency_key="intent:work-local",
    )


def _store(tmp_path) -> Store:
    store = Store(tmp_path / "runtime.db")
    store.create_run("run-local", "local", {}, "contract://root@1")
    store.put_contract({"id": "contract-local", "version": 1, "outcome": "local", "done_when": []})
    store.create_task(
        {
            "id": "task-local",
            "run_id": "run-local",
            "outcome": "local",
            "contract_id": "contract-local",
            "state": "ready",
        }
    )
    store.create_work_unit(
        {
            "id": "work-local",
            "task_id": "task-local",
            "objective": "complete local work",
            "state": "ready",
            "ownership": ["tests/**"],
            "return_contract": "work-handoff/v1",
        }
    )
    return store


def test_local_scheduler_emits_logical_intent_before_any_host_action(tmp_path):
    with _store(tmp_path) as store:
        local = LocalScheduler(store, "task-local").step()[0]

        assert isinstance(local.intent, LocalDispatchIntent)
        assert local.intent.protocol == "local-dispatch-intent/v1"
        assert local.intent.logical_capability == "native_subagent"
        assert local.intent.execution_mode == "native_preferred"
        assert "tool" not in local.intent.to_dict()
        assert store._fetchone("SELECT * FROM dispatch_outbox") is None


class _MappedHost:
    def __init__(self) -> None:
        self.invoked: list[tuple[str, dict]] = []

    def discover(self):
        return {
            "host_id": "mapped-host",
            "available": True,
            "logical_capabilities": {
                "native_subagent": {
                    "available": True,
                    "physical_tools": ["host.child.create"],
                    "preferred_tool": "host.child.create",
                    "receipt_contract": "host-receipt/v1",
                }
            },
        }

    def invoke(self, tool, arguments):
        self.invoked.append((tool, dict(arguments)))
        return {
            "receipt_id": "receipt-mapped",
            "thread_id": "worker-mapped",
            "status": "active",
            "actual": True,
        }


def test_host_discovery_resolves_logical_capability_to_exact_physical_action():
    raw_host = _MappedHost()
    adapter = NativeSubagentHost(raw_host, host_id="mapped-host")

    resolution = adapter.resolve_local(_intent())

    assert isinstance(resolution, HostAction)
    assert resolution.tool == "host.child.create"
    assert resolution.logical_capability == "native_subagent"
    assert resolution.tool_policy == {
        "exact_tool": "host.child.create",
        "substitutions": [],
        "on_unavailable": "block",
        "exact_after_resolution": True,
    }
    receipt = adapter.invoke_resolved(resolution)
    assert raw_host.invoked[0][0] == "host.child.create"
    assert receipt.actual_tool == "host.child.create"
    assert receipt.actual_capability == "native_subagent"
    assert receipt.action_contract_hash == resolution.action_contract_hash
    assert receipt.payload["local_resource_receipt"]["thread_id"] == "worker-mapped"


@pytest.mark.parametrize("mode", ["native_preferred", "direct_only"])
def test_native_preferred_and_direct_only_resolve_to_lane_direct(mode):
    adapter = NativeSubagentHost(None, native_available=False)

    resolution = adapter.resolve_local(_intent(mode=mode))

    assert isinstance(resolution, LaneDirectExecutionPlan)
    assert resolution.execution_class == "lane_direct"
    assert resolution.return_contract == "work-handoff/v1"


def test_direct_only_never_invokes_an_available_native_host():
    raw_host = _MappedHost()
    adapter = NativeSubagentHost(raw_host)

    resolution = adapter.resolve_local(_intent(mode="direct_only"))

    assert isinstance(resolution, LaneDirectExecutionPlan)
    assert raw_host.invoked == []


def test_native_required_blocks_truthfully_without_physical_capability():
    adapter = NativeSubagentHost(None, native_available=False)

    with pytest.raises(LocalCapabilityUnavailable, match="does not advertise"):
        adapter.resolve_local(_intent(mode="native_required"))


def test_legacy_fallback_receipt_is_not_a_work_handoff():
    receipt = NativeSubagentFallbackContract(
        work_unit_id="work-local", parent_work_unit_id="task-local"
    ).to_dict()

    assert receipt["status"] == "direct-execution"
    assert receipt["protocol"] == "host-receipt/v1"
    assert receipt.get("evidence") is None
    assert receipt.get("return_contract") != "work-handoff/v1"
