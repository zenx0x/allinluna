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
