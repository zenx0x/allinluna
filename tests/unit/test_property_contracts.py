from __future__ import annotations

import hashlib
import json
import random

from ._protocol import construct, invoke, require_symbol


def test_randomized_dag_invariants(vnext_module, tmp_path, fake_codex_host):
    store_module = vnext_module("store")
    graph_module = vnext_module("domain")
    scheduler_module = vnext_module("scheduler.global_scheduler")
    store = construct(require_symbol(store_module, "Store"), tmp_path / "runtime.db")
    graph = construct(require_symbol(graph_module, "TaskGraph"), run_id="run-property")
    scheduler = construct(require_symbol(scheduler_module, "GlobalScheduler"), store=store, host=fake_codex_host)
    rng = random.Random(7057)
    tasks = [f"T{index}" for index in range(25)]
    for task_id in tasks:
        invoke(graph, "add_task", task_id)
    for index, child in enumerate(tasks):
        for parent in tasks[:index]:
            if rng.random() < 0.12:
                invoke(graph, "add_dependency", parent, child)
    invoke(scheduler, "load_graph", graph)
    for _ in range(len(tasks) + 2):
        for action in invoke(scheduler, "step", capacity=5):
            assert invoke(graph, "dependencies_satisfied", action["task_id"])
            invoke(scheduler, "complete", action["task_id"])


def test_property_conflict_and_receipt_replay_invariants(vnext_module, tmp_path, fake_codex_host):
    store_module = vnext_module("store")
    scheduler_module = vnext_module("scheduler.global_scheduler")
    store = construct(require_symbol(store_module, "Store"), tmp_path / "runtime.db")
    scheduler = construct(require_symbol(scheduler_module, "GlobalScheduler"), store=store, host=fake_codex_host)
    invoke(scheduler, "add_task", "writer-1", ownership=["a.py"])
    invoke(scheduler, "add_task", "writer-2", ownership=["a.py"])
    actions = invoke(scheduler, "step", capacity=2)
    assert len(actions) == 1
    receipt = fake_codex_host.create_top_level_task(actions[0])
    first = invoke(scheduler, "ingest_receipt", receipt)
    second = invoke(scheduler, "ingest_receipt", receipt)
    assert first == second
    assert invoke(scheduler, "attempt_count", "writer-1") + invoke(scheduler, "attempt_count", "writer-2") == 1


def test_snapshot_reconstruction_digest_is_stable(vnext_module, tmp_path):
    context_module = vnext_module("context")
    kernel = construct(require_symbol(context_module, "ContextKernel"), tmp_path / "context.db")
    body = {"contract": {"id": "c1", "revision": 1}, "imports": ["artifact://a"], "delta": {"x": 1}}
    first = invoke(kernel, "reconstruct", body)
    second = invoke(kernel, "reconstruct", json.loads(json.dumps(body)))
    assert first["source_digest"] == second["source_digest"]
    assert first["source_digest"] == hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
