from __future__ import annotations

from pathlib import Path

from allinluna_runtime.engine.coordinator_driver import CoordinatorDriver
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.store import Store

from tests.e2e._lane_direct_runtime import (
    LaneDirectTopLevelHost,
    prepare_workspace,
    public_request,
    task_spec,
)


def test_plain_goal_closes_through_coordinator_real_lane_and_direct_workunit(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "fixture"
    prepare_workspace(repository_root)
    task = task_spec(
        repository_root,
        task_id="deliver",
        condition="fixture delivery is independently verified",
    )
    request = public_request(
        repository_root,
        intent_id="plain-goal-verification",
        tasks=[task],
    )
    db_path = tmp_path / "runtime.db"
    started = SinglePublicSkillAPI().start(request, db_path=db_path)
    run_id = str(started["run_ref"]).removeprefix("run://")
    host = LaneDirectTopLevelHost()

    with Store(db_path) as store:
        result = CoordinatorDriver(store, host=host).drive(
            run_id, max_cycles=8, monitor=True
        )
        persisted_task = store.get_task("deliver", run_id=run_id) or {}
        handoffs = store._fetchall(
            "SELECT status, payload_json FROM driver_handoffs "
            "WHERE driver_kind = 'coordinator' AND scope_id = ?",
            (run_id,),
        )

        assert result["boundary"] == {"kind": "completed"}
        assert store.get_run(run_id)["status"] == "completed"
        assert persisted_task["state"] == "completed"
        assert len(handoffs) == 1
        assert handoffs[0]["status"] == "completed"

    assert len(host.created_task_ids) == 1
    assert host.created_task_ids[0].endswith(":task:deliver")
    assert len(host.direct_plans) == 1
    assert host.direct_plans[0]["protocol"] == "lane-direct-work/v1"
    assert host.work_handoffs[0]["state"] == "executed-and-ingested"
    assert any(event["verified"] is True for event in host.evidence_events)
    assert host.child_handoffs[0]["protocol"] == "lane-handoff/v1"
    assert host.child_handoffs[0]["evidence"]["verified"] is True
