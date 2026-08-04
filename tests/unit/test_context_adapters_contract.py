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
    assert compacted["base_snapshot_ref"] == snapshot["id"]
    invalidated = invoke(kernel, "invalidate", compacted, reason="upstream_contract_changed")
    assert invalidated["validity"] == "invalid"
    rebuilt = invoke(kernel, "reconstruct", invalidated, current_commit="abc123")
    assert rebuilt["validity"] == "current"
    trace = invoke(kernel, "trace_artifact", rebuilt, "artifact://check-log/1")
    assert trace["artifact_ref"] == "artifact://check-log/1"
    assert trace["snapshot_id"] == rebuilt["id"]


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
    }
    receipt = invoke(adapter, "create_top_level_task", action)
    assert receipt["actual"] is True
    assert receipt["thread_id"] == "thread-1"
    delayed = invoke(adapter, "wait_tasks", [{"thread_id": "thread-1"}], cursor=None)
    assert delayed["protocol"] == "host-receipt/v1"
