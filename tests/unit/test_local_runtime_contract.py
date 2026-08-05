from __future__ import annotations

from ._protocol import construct, invoke, require_symbol


def make_lane(vnext_module, tmp_path, fake_subagent_host):
    store_module = vnext_module("store")
    local_module = vnext_module("scheduler.local_scheduler")
    workgraph_module = vnext_module("domain")
    store = construct(require_symbol(store_module, "Store"), tmp_path / "runtime.db")
    graph = construct(require_symbol(workgraph_module, "WorkGraph"), task_id="lane-task")
    lane = construct(
        require_symbol(local_module, "LocalScheduler"),
        store=store,
        work_graph=graph,
        host=fake_subagent_host,
    )
    return graph, lane


def test_workgraph_dynamic_expansion_and_local_ready_dispatch(vnext_module, tmp_path, fake_subagent_host):
    graph, lane = make_lane(vnext_module, tmp_path, fake_subagent_host)
    invoke(graph, "add", "parent", objective="decompose")
    invoke(graph, "expand", "parent", [
        {"id": "child-a", "objective": "schema"},
        {"id": "child-b", "objective": "tests"},
    ])
    ready = invoke(lane, "ready_work_units")
    assert {item["id"] for item in ready} == {"child-a", "child-b"}
    actions = invoke(lane, "step", capacity=2)
    assert {item["work_unit_id"] for item in actions} == {"child-a", "child-b"}


def test_recursive_scope_authority_and_ownership_only_narrow(vnext_module, tmp_path, fake_subagent_host):
    graph, _ = make_lane(vnext_module, tmp_path, fake_subagent_host)
    invoke(graph, "add", "root", scope=["plugins/allinluna/runtime/**"], authority=["local-read"],
           ownership=["plugins/allinluna/runtime/allinluna_runtime/store.py"])
    invoke(graph, "add_child", "root", "child", scope=["plugins/allinluna/runtime/allinluna_runtime/**"],
           authority=["local-read"], ownership=["plugins/allinluna/runtime/allinluna_runtime/store.py"])
    invoke(graph, "add_child", "child", "grandchild",
           scope=["plugins/allinluna/runtime/allinluna_runtime/store.py"], authority=["local-read"],
           ownership=["plugins/allinluna/runtime/allinluna_runtime/store.py"])
    assert invoke(graph, "validate_monotonic_narrowing") is True
    try:
        invoke(graph, "add_child", "child", "invalid", scope=["README.md"], authority=["push"],
               ownership=["README.md"])
    except (ValueError, PermissionError):
        return
    raise AssertionError("child scope/authority/ownership expansion was accepted")


def test_child_correction_targets_same_work_unit(vnext_module, tmp_path, fake_subagent_host):
    graph, lane = make_lane(vnext_module, tmp_path, fake_subagent_host)
    invoke(graph, "add", "child", objective="initial")
    correction = invoke(lane, "correct", "child", expected_contract_revision=3, issue="wrong output")
    assert correction["target"] == "child"
    assert correction["new_work_unit_id"] == "child"
    assert correction["replacement"] is False


def test_promotion_request_and_lane_synthesis_are_explicit(vnext_module, tmp_path, fake_subagent_host):
    graph, lane = make_lane(vnext_module, tmp_path, fake_subagent_host)
    invoke(graph, "add", "discover", objective="independent deliverable")
    request = invoke(
        lane,
        "request_promotion",
        "discover",
        reason="needs cross-lane ownership",
        requested_ownership=["docs/architecture/vnext/**"],
    )
    assert request["type"] == "PromotionRequest"
    assert request["from_work_unit"] == "discover"
    synthesis = invoke(lane, "synthesize", done_when=["all local work complete"])
    assert synthesis["status"] in {"verifying", "completed"}
    assert synthesis["promotion_requests"] == [request]
