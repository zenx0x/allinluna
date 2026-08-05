from __future__ import annotations

from allinluna_runtime.engine.action_bridge import ActionBridge
from allinluna_runtime.resource import ResourceBroker
from allinluna_runtime.resource_policy import ResourcePolicyResolver
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store
from scripts.host_conformance import evaluate


EXACT_CREATE = "codex_app__create_thread"


def _host_routes(*, version: str = "1", tools: tuple[str, ...] = (EXACT_CREATE,)) -> dict:
    return {
        "host_id": "host-resource-policy",
        "host_version": version,
        "plugin_version": "plugin-1",
        "tools": list(tools),
        "capability_routes": {
            "planning.semantic": {"model": "semantic-route", "reasoning": "high"},
            "lane.synthesis": {"model": "lane-route", "reasoning": "medium"},
            "work.implementation": {"model": "implementation-route", "reasoning": "high"},
            "verify.independent": {"model": "verifier-route", "reasoning": "xhigh"},
        },
    }


def test_resolver_uses_capability_classes_and_exposes_p1f_operation_seam() -> None:
    resolver = ResourcePolicyResolver({"route_assurance": "observe_if_exposed"})
    resolver.set_host_capabilities(_host_routes())

    planning = resolver.resolve(operation="planning")
    implementation = resolver.resolve(operation="spawn-subagent")
    verification = resolver.resolve(operation="verify")

    assert planning.capability_class == "planning.semantic"
    assert planning.resolved["model"] == "semantic-route"
    assert implementation.capability_class == "work.implementation"
    assert implementation.resolved["reasoning"] == "high"
    assert verification.capability_class == "verify.independent"
    assert verification.resolved["model"] == "verifier-route"
    # The operation string is the explicit P1-F semantic/compiler handoff;
    # no vendor/model branch appears in the Core classification table.
    assert resolver.resolve(operation="work.mechanical").capability_class == "work.mechanical"


def test_requested_resolved_actual_stay_separate_and_hard_lock_rejects_reroute() -> None:
    resolver = ResourcePolicyResolver(
        {"model": "requested-route", "reasoning": "medium", "route_assurance": "hard_lock"}
    )
    observation = {
        "requested": {"model": "requested-route", "reasoning": "medium"},
        "resolved": {"model": "rerouted-route", "reasoning": "medium", "route_assurance": "hard_lock"},
        "actual": {"model": "rerouted-route", "reasoning": "medium"},
        "actual_state": "resolved",
        "evidence_source": "host-runtime",
        "observed_at": "2026-08-05T00:00:00Z",
    }

    assurance = resolver.assess(observation)

    assert assurance.state == "blocked"
    assert assurance.blocking is True
    assert "requested/resolved" in str(assurance.reason)


def test_capability_cache_reuses_unchanged_profile_and_invalidates_changed_profile(tmp_path) -> None:
    with Store(tmp_path / "runtime.db") as store:
        store.create_run("run-resource-policy", "resource policy", {}, "contract://root@1")
        broker = ResourceBroker(store=store, run_id="run-resource-policy")

        first = broker.set_host_capabilities(_host_routes())
        second = broker.set_host_capabilities(_host_routes())
        changed = broker.set_host_capabilities(_host_routes(version="2", tools=(EXACT_CREATE, "codex_app__read_thread")))

        assert first["cache"] == "miss"
        assert second["cache"] == "hit"
        assert changed["cache"] == "miss"
        stale = store._fetchall(
            "SELECT invalidation_reason FROM host_capability_cache WHERE host_id = ? AND invalidated_at IS NOT NULL",
            ("host-resource-policy",),
        )
        assert stale == [{"invalidation_reason": "capability-profile-changed"}]


class _StrictTelemetryHost:
    def discover(self):
        return {"available": True, **_host_routes()}

    def create_top_level_task(self, _action):
        # This is operationally valid, but deliberately exposes no resource
        # telemetry so only strict assurance blocks it.
        return {
            "receipt_id": "receipt-strict-policy",
            "thread_id": "thread-strict-policy",
            "status": "active",
            "actual": True,
            "actual_tool": EXACT_CREATE,
            "actual_capability": EXACT_CREATE,
        }

    def cancel_task(self, _target):
        return {"receipt_id": "receipt-cancel", "status": "cancelled"}


def test_receipt_required_assurance_blocks_only_after_operational_receipt(tmp_path) -> None:
    with Store(tmp_path / "runtime.db") as store:
        store.create_run(
            "run-strict-policy",
            "strict resource telemetry",
            {"route_assurance": "receipt_required"},
            "contract://root@1",
        )
        store.create_task(
            {
                "id": "strict-policy-task",
                "run_id": "run-strict-policy",
                "outcome": "require resource receipt",
                "state": "ready",
            }
        )
        broker = ResourceBroker(
            {"route_assurance": "receipt_required"},
            store=store,
            run_id="run-strict-policy",
        )
        broker.set_host_capabilities(_host_routes())
        action = GlobalScheduler(store, resource_broker=broker).step("run-strict-policy")[0]
        result = ActionBridge(store, _StrictTelemetryHost(), resource_broker=broker).dispatch(action)

        assert result["status"] == "ROUTE_ASSURANCE_BLOCKED"
        assert result["ingestion"]["resource_receipt"]["actual_state"] == "unresolved"
        assert result["route_assurance"]["state"] == "blocked"
        assert store.get_task("strict-policy-task")["state"] == "blocked"
        assert store.count_receipts("receipt-strict-policy") == 1


def test_operation_specific_conformance_accepts_projectless_trace_and_rejects_wrong_tool() -> None:
    identity = {"thread_id": "projectless-thread", "host_id": "projectless-host"}
    tools = {
        "create": "codex_app__create_thread",
        "read": "codex_app__read_thread",
        "wait": "codex_app__wait_threads",
        "cancel": "codex_app__cancel_thread",
    }
    trace = {
        "protocol": "allinluna.host_conformance",
        "schema_version": "2.0",
        "verification_mode": "real",
        "checked_at": "2026-08-05T00:00:00Z",
        "identity": identity,
        "operations": [
            {
                "op": operation,
                "thread_id": identity["thread_id"],
                "requested_tool": tool,
                "resolved_tool": tool,
                "actual_tool": tool,
                "requested_capability": tool,
                "resolved_capability": tool,
                "actual_capability": tool,
                "identity": identity,
                "idempotency": "wait" if operation == "wait" else "reuse" if operation == "read" else "no-op",
            }
            for operation, tool in tools.items()
        ],
    }

    assert evaluate(trace, mode="real")["status"] == "PASS"
    trace["operations"][2]["actual_tool"] = tools["create"]
    assert evaluate(trace, mode="real")["status"] == "FAIL"
