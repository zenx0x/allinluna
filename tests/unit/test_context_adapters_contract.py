from __future__ import annotations

from ._protocol import assert_no_raw_logs, construct, invoke, require_symbol


def test_context_views_causal_isolation_and_base_delta(vnext_module, tmp_path):
    context_module = vnext_module("context")
    kernel = construct(require_symbol(context_module, "ContextKernel"), tmp_path / "context.db")
    base = invoke(
        kernel,
        "build",
        scope="lane",
        scope_id="lane-a",
        contract={"id": "contract-a", "revision": 1},
        causal_refs=["artifact://upstream"],
        excluded=["lane-b", "raw-tool-logs"],
    )
    child = invoke(kernel, "derive", base, delta={"files": ["store.py"]}, scope="work_unit")
    assert child["base_snapshot_ref"] == base["id"]
    assert child["context_delta"] == {"files": ["store.py"]}
    assert "lane-b" not in child.get("imports", [])
    assert_no_raw_logs(invoke(kernel, "view", child, kind="LaneSnapshot"))


def test_context_compaction_invalidation_reconstruction_and_artifact_trace(vnext_module, tmp_path):
    context_module = vnext_module("context")
    kernel = construct(require_symbol(context_module, "ContextKernel"), tmp_path / "context.db")
    snapshot = invoke(kernel, "build", scope="task", scope_id="T1", contract={"revision": 1})
    compacted = invoke(kernel, "compact", snapshot, preserve=["contract", "exports", "blockers"])
    assert compacted["base_snapshot_ref"] is None
    invalidated = invoke(kernel, "invalidate", compacted, reason="upstream_contract_changed")
    assert invalidated["validity"] == "invalid"
    rebuilt = invoke(kernel, "reconstruct", invalidated, current_commit="abc123")
    assert rebuilt["validity"] == "current"
    trace = invoke(kernel, "trace_artifact", rebuilt, "artifact://check-log/1")
    assert trace["artifact_ref"] == "artifact://check-log/1"
    assert trace["snapshot_id"] == rebuilt["id"]


def test_context_views_use_distinct_allowlists_recursive_sanitization_and_artifact_visibility(vnext_module, tmp_path):
    context_module = vnext_module("context")
    kernel = construct(require_symbol(context_module, "ContextKernel"), tmp_path / "typed-context.db")
    local = kernel.artifacts.put(b"local", visibility="local")
    lane = kernel.artifacts.put(b"lane", visibility="lane")
    coordinator = kernel.artifacts.put(b"coordinator", visibility="coordinator")
    user = kernel.artifacts.put(b"user", visibility="user")
    snapshot = invoke(
        kernel,
        "build",
        scope="work_unit",
        scope_id="WU-typed",
        content={
            "objective": "typed projection",
            "known_facts": [{"fact": "safe", "details": {"stdout": "secret", "hidden_reasoning": "secret"}}],
            "files": ["context.py"],
            "authority": ["edit"],
            "untyped_private_field": "must not cross a typed boundary",
            "artifact_refs": [local.ref, lane.ref, coordinator.ref, user.ref, "artifact://missing"],
            "nested": {"tool_logs": ["secret"], "transcript": "secret"},
        },
    )

    conversation = invoke(kernel, "view", snapshot, kind="ConversationSnapshot").to_dict()
    coordinator_view = invoke(kernel, "view", snapshot, kind="CoordinatorSnapshot").to_dict()
    lane_view = invoke(kernel, "view", snapshot, kind="LaneSnapshot").to_dict()
    work_unit = invoke(kernel, "view", snapshot, kind="WorkUnitSlice").to_dict()

    assert "files" not in conversation
    assert "files" not in coordinator_view
    assert lane_view["files"] == ["context.py"]
    assert "authority" not in lane_view
    assert work_unit["authority"] == ["edit"]
    assert conversation["artifact_refs"] == [user.ref]
    assert coordinator_view["artifact_refs"] == [coordinator.ref, user.ref]
    assert lane_view["artifact_refs"] == [lane.ref, coordinator.ref, user.ref]
    assert work_unit["artifact_refs"] == [local.ref, lane.ref, coordinator.ref, user.ref]
    for view in (conversation, coordinator_view, lane_view, work_unit):
        serialized = repr(view).lower()
        assert "secret" not in serialized
        assert "untyped_private_field" not in view


def test_context_invalidation_is_transitive_and_compaction_replacement_survives_restart(vnext_module, tmp_path):
    context_module = vnext_module("context")
    database = tmp_path / "persistent-context.db"
    kernel = construct(require_symbol(context_module, "ContextKernel"), database)
    upstream = invoke(
        kernel,
        "build",
        scope="task",
        scope_id="T-api",
        content={"contract_ref": "contract://api@1", "known_facts": ["v1"]},
        inputs=("contract://api@1",),
    )
    child = invoke(kernel, "derive", upstream, {"work_unit_id": "WU-client"}, scope="work_unit", scope_id="WU-client")
    grandchild = invoke(kernel, "derive", child, {"active_work": ["consumer"]}, scope="work_unit", scope_id="WU-consumer")
    invalidation = invoke(
        kernel,
        "invalidate_from_contract_delta",
        {"target": "contract://api", "previous_revision": 1, "next_revision": 2, "delta_id": "delta-api-2"},
    )
    assert invalidation.invalidated_by == "delta-api-2"
    assert set(invalidation.dependent_refs) == {upstream.snapshot_ref, child.snapshot_ref, grandchild.snapshot_ref}
    assert kernel.connection.execute("SELECT validity FROM snapshots WHERE id = ?", (grandchild.snapshot_ref,)).fetchone()[0] == "stale"

    compacted = invoke(kernel, "compact", upstream)
    assert compacted.base_snapshot_ref is None
    original_ref = upstream.snapshot_ref
    compacted_ref = compacted.snapshot_ref
    kernel.close()

    reopened = construct(require_symbol(context_module, "ContextKernel"), database)
    assert invoke(reopened, "snapshot", original_ref).snapshot_ref == compacted_ref
    assert invoke(reopened, "reconstruct_content", original_ref)["known_facts"] == ["v1"]
    reopened.close()


def test_context_deep_chain_materializes_in_constant_queries_and_reuses_hot_cache(vnext_module, tmp_path):
    context_module = vnext_module("context")
    kernel = construct(require_symbol(context_module, "ContextKernel"), tmp_path / "deep-context.db")
    current = invoke(kernel, "build", scope="task", scope_id="deep", content={"known_facts": ["root"]})
    for depth in range(100):
        current = invoke(kernel, "derive", current, {"active_work": [f"step-{depth}"]})

    selects = 0

    def trace(statement):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    kernel.connection.set_trace_callback(trace)
    content = invoke(kernel, "reconstruct_content", current.snapshot_ref)
    cold_selects = selects
    content_again = invoke(kernel, "reconstruct_content", current.snapshot_ref)
    hot_selects = selects - cold_selects
    kernel.connection.set_trace_callback(None)

    assert content == content_again
    assert content["active_work"] == ["step-99"]
    assert cold_selects <= 4
    assert hot_selects <= 2
    metrics = invoke(kernel, "metrics")
    assert metrics["max_snapshot_depth"] == 101
    assert metrics["cache_hits"] >= 1
    assert metrics["raw_leakage_count"] == 0


def test_host_adapter_jit_permissions_and_legacy_import(vnext_module, tmp_path, fake_codex_host, git_fixture):
    host_module = vnext_module("adapters.host.codex_app")
    public_skill_module = vnext_module("packs.public_skill")
    compat_module = vnext_module("compat.legacy_plan")
    adapter = construct(require_symbol(host_module, "CodexAppHost"), fake_codex_host)
    assert invoke(adapter, "discover")["host_id"] == "fake-codex-host"
    public_skill = construct(require_symbol(public_skill_module, "SinglePublicSkillAPI"))
    permission = invoke(
        public_skill,
        "permission_at_action",
        "push",
        scopes=(str(git_fixture.repository),),
        policy="ask",
    )
    assert permission.action == "push"
    assert permission.status == "ask"
    imported = invoke(
        construct(require_symbol(compat_module, "LegacyPlanImporter")),
        "import_plan",
        {"goal": "legacy", "tasks": []},
    )
    assert imported["source_format"] == "legacy-plan"
    assert imported["write_back"] is False
    workspace_module = vnext_module("adapters.workspace.git")
    workspace = construct(
        require_symbol(workspace_module, "GitWorktreeAdapter"),
        worktree=git_fixture.worktree,
        repo_root=git_fixture.repository,
        base_commit=git_fixture.base_commit,
        ownership=("tracked.txt",),
    )
    ownership = invoke(
        workspace,
        "verify_changed_paths",
        {
            "worktree": git_fixture.worktree,
            "repo_root": git_fixture.repository,
            "base_commit": git_fixture.base_commit,
        },
        (),
    )
    assert ownership["valid"] is True
    assert ownership["identity"]["worktree"] == str(git_fixture.worktree)
    assert ownership["identity"]["base_commit"] == git_fixture.base_commit


def test_adapter_receipt_is_real_and_wait_fallback_is_evidenced(vnext_module, fake_codex_host):
    host_module = vnext_module("adapters.host.codex_app")
    adapter = construct(require_symbol(host_module, "CodexAppHost"), fake_codex_host)
    action = {
        "action_id": "action-1",
        "idempotency_key": "dispatch-1",
        "task_id": "T1",
        "dispatch_id": "dispatch-1",
        "target": {"type": "projectless", "directoryName": "adapter-test"},
        "prompt": "adapter test",
        "model": "gpt-5.6-luna",
        "title": "adapter test",
    }
    receipt = invoke(adapter, "create_top_level_task", action)
    assert receipt["actual"] is True
    assert receipt["thread_id"] == "thread-1"
    delayed = invoke(adapter, "wait_tasks", [{"thread_id": "thread-1"}], cursor=None)
    assert delayed["protocol"] == "host-receipt/v1"
