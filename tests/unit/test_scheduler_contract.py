from __future__ import annotations

import random

from ._protocol import construct, invoke, require_symbol


def make_scheduler(vnext_module, tmp_path, fake_codex_host):
    store_module = vnext_module("store")
    scheduler_module = vnext_module("scheduler.global_scheduler")
    store = construct(require_symbol(store_module, "Store"), tmp_path / "runtime.db")
    scheduler = construct(
        require_symbol(scheduler_module, "GlobalScheduler"),
        store=store,
        host=fake_codex_host,
    )
    return store, scheduler


def test_cycle_detection_rejects_cyclic_task_graph(vnext_module, tmp_path, fake_codex_host):
    _, scheduler = make_scheduler(vnext_module, tmp_path, fake_codex_host)
    invoke(scheduler, "add_task", "A", priority=10)
    invoke(scheduler, "add_task", "B", priority=10)
    invoke(scheduler, "add_dependency", "A", "B")
    try:
        invoke(scheduler, "add_dependency", "B", "A")
    except (ValueError, RuntimeError):
        return
    raise AssertionError("cyclic dependency was accepted")


def test_dependency_conditions_and_continuous_ready_dispatch(vnext_module, tmp_path, fake_codex_host):
    _, scheduler = make_scheduler(vnext_module, tmp_path, fake_codex_host)
    invoke(scheduler, "add_task", "producer", priority=10)
    invoke(scheduler, "add_task", "consumer", priority=10)
    invoke(
        scheduler,
        "add_dependency",
        "producer",
        "consumer",
        condition={"type": "exports_available", "exports": ["ArtifactA"]},
    )
    first = invoke(scheduler, "step")
    assert [action["task_id"] for action in first] == ["producer"]
    assert invoke(scheduler, "state", "consumer") == "proposed"
    invoke(scheduler, "complete", "producer", exports=["OtherArtifact"])
    assert invoke(scheduler, "step") == []
    invoke(scheduler, "complete", "producer", exports=["ArtifactA"])
    second = invoke(scheduler, "step")
    assert [action["task_id"] for action in second] == ["consumer"]


def test_write_conflicts_do_not_share_active_leases(vnext_module, tmp_path, fake_codex_host):
    _, scheduler = make_scheduler(vnext_module, tmp_path, fake_codex_host)
    invoke(scheduler, "add_task", "writer-a", ownership=["src/shared.py"])
    invoke(scheduler, "add_task", "writer-b", ownership=["src/shared.py"])
    selected = invoke(scheduler, "step", capacity=2)
    assert len(selected) == 1
    assert invoke(scheduler, "active_lease_count", ownership="src/shared.py") == 1


def test_blocked_lane_does_not_block_unrelated_ready_lane(vnext_module, tmp_path, fake_codex_host):
    _, scheduler = make_scheduler(vnext_module, tmp_path, fake_codex_host)
    invoke(scheduler, "add_task", "blocked", lane_id="lane-a")
    invoke(scheduler, "add_task", "ready", lane_id="lane-b")
    invoke(scheduler, "block", "blocked", reason="permission")
    actions = invoke(scheduler, "step")
    assert [action["task_id"] for action in actions] == ["ready"]


def test_critical_path_priority_and_fairness_are_both_observable(vnext_module, tmp_path, fake_codex_host):
    _, scheduler = make_scheduler(vnext_module, tmp_path, fake_codex_host)
    invoke(scheduler, "add_task", "critical-root", priority=1)
    invoke(scheduler, "add_task", "critical-leaf", priority=1)
    invoke(scheduler, "add_dependency", "critical-root", "critical-leaf")
    invoke(scheduler, "add_task", "waiting", priority=1)
    invoke(scheduler, "age", "waiting", ticks=100)
    ranked = invoke(scheduler, "rank_ready")
    assert ranked[0]["task_id"] in {"critical-root", "waiting"}
    assert {item["task_id"] for item in ranked} == {"critical-root", "waiting"}


def test_lease_expiry_reconciles_before_retry(vnext_module, tmp_path, fake_codex_host):
    _, scheduler = make_scheduler(vnext_module, tmp_path, fake_codex_host)
    invoke(scheduler, "add_task", "lease-task")
    action = invoke(scheduler, "step")[0]
    invoke(scheduler, "expire_lease", action["lease_id"])
    reconciliation = invoke(scheduler, "reconcile_expired")
    assert reconciliation[0]["task_id"] == "lease-task"
    assert reconciliation[0]["receipt_checked"] is True


def test_crash_recovery_reconciles_host_before_reissuing_dispatch(vnext_module, tmp_path, fake_codex_host):
    _, scheduler = make_scheduler(vnext_module, tmp_path, fake_codex_host)
    invoke(scheduler, "add_task", "crashed-task")
    first = invoke(scheduler, "step")[0]
    recovered = invoke(scheduler, "recover", unfinished=[first])
    assert recovered[0]["task_id"] == "crashed-task"
    assert recovered[0]["receipt_checked"] is True
    assert invoke(scheduler, "attempt_count", "crashed-task") == 1


def test_random_dag_never_dispatches_unsatisfied_dependencies(vnext_module, tmp_path, fake_codex_host):
    _, scheduler = make_scheduler(vnext_module, tmp_path, fake_codex_host)
    rng = random.Random(57)
    task_ids = [f"T{index}" for index in range(12)]
    for task_id in task_ids:
        invoke(scheduler, "add_task", task_id)
    for index, child in enumerate(task_ids):
        for parent in task_ids[:index]:
            if rng.random() < 0.2:
                invoke(scheduler, "add_dependency", parent, child)
    for _ in range(len(task_ids) + 1):
        actions = invoke(scheduler, "step", capacity=3)
        completed = [action["task_id"] for action in actions]
        assert all(invoke(scheduler, "dependencies_satisfied", task_id) for task_id in completed)
        for task_id in completed:
            invoke(scheduler, "complete", task_id)
        if not actions:
            break
