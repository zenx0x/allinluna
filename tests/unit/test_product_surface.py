from __future__ import annotations

import json

from allinluna_runtime.cli import main
from allinluna_runtime.engine.coordinator import CoordinatorEngine
from allinluna_runtime.store import Store


def _output(capsys):
    return json.loads(capsys.readouterr().out)


def test_compile_and_plan_are_read_only(tmp_path, capsys):
    db = tmp_path / "must-not-exist.db"
    assert main(["--db", str(db), "compile", "--goal", "ship safely"]) == 0
    assert _output(capsys)["input_kind"] == "idea"
    assert not db.exists()


def test_app_server_receipt_compilation_is_read_only(tmp_path, capsys):
    db = tmp_path / "must-not-exist.db"
    requested = '{"model":"gpt-5.6-luna","reasoning":"medium"}'
    started = '{"source":"codex_app","actual_tool":"codex_app__create_thread","event_origin":"codex_desktop","result":{"thread":{"id":"thread-cli"},"model":"gpt-5.6-luna","reasoningEffort":"medium"}}'
    events = '[{"method":"turn/started","params":{"threadId":"thread-cli","turnId":"turn-cli","timestamp":"2026-08-05T12:00:00Z"}},{"method":"turn/completed","params":{"threadId":"thread-cli","turnId":"turn-cli","timestamp":"2026-08-05T12:00:01Z"}}]'
    action = '{"action_id":"action-cli","kind":"create-task","idempotency_key":"dispatch-cli","model":"gpt-5.6-luna","reasoning":"medium"}'
    assert main(["--db", str(db), "receipt-from-app-server", "--requested", requested, "--thread-start", started, "--events", events, "--action", action]) == 0
    receipt = _output(capsys)
    assert receipt["source"] == "codex_app"
    assert receipt["actual_tool"] == "codex_app__create_thread"
    assert receipt["resource_receipt"]["actual_state"] == "resolved"
    assert not db.exists()
    assert main(["--db", str(db), "plan", "--goal", "ship safely"]) == 0
    plan = _output(capsys)
    assert plan["kind"] == "plan" and plan["writes"] is False and plan["tasks"]
    assert not db.exists()


def test_start_dispatch_and_complete_inspection_surface(tmp_path, capsys):
    db = tmp_path / "runtime.db"
    assert main(["--db", str(db), "start", "--goal", "ship safely", "--model", "gpt-5.6-luna", "--reasoning", "medium"]) == 0
    started = _output(capsys)
    run_id = started["run_ref"].removeprefix("run://")

    with Store(db) as store:
        task = store.scheduler_snapshot(run_id)["tasks"][0]
        outbox = store._fetchone("SELECT id FROM dispatch_outbox WHERE run_id=? ORDER BY created_at LIMIT 1", (run_id,))
    for kind, identity in (("run", run_id), ("task", task["id"]), ("outbox", outbox["id"])):
        assert main(["--db", str(db), "inspect", kind, identity]) == 0
        assert _output(capsys) is not None
    assert main(["--db", str(db), "inspect", "artifacts"]) == 0
    assert _output(capsys) == []
    assert main(["--db", str(db), "dispatch", run_id]) == 0
    assert _output(capsys)["run_id"] == run_id

    with Store(db) as store:
        status = CoordinatorEngine(store).status(run_id)
    metrics = status["extensions"]["metrics"]
    pulse = status["extensions"]["progress_pulse"]
    assert {"slot_utilization", "duplicate_prevented", "blocker_age_seconds", "handoff_verification_failures"} <= metrics.keys()
    assert pulse["run_ref"] == f"run://{run_id}"
