from __future__ import annotations

import json
from pathlib import Path

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.store import Store

from scripts.validate_product_experience import drive_lane_direct
from tests.e2e._lane_direct_runtime import (
    prepare_workspace,
    public_request,
    qualify_native_required_negative,
    qualify_two_lane_runtime,
    task_spec,
)


def test_two_lane_export_dependency_releases_b_and_completes_root(tmp_path: Path) -> None:
    result = qualify_two_lane_runtime(
        tmp_path, intent_id="two-lane-export-dependency"
    )
    host = result["host"]

    assert [item.rsplit(":task:", 1)[-1] for item in result["initial_task_ids"]] == [
        "producer"
    ]
    assert json.loads(result["dependency"]["condition_json"]) == {
        "exports": ["ProducerArtifact"],
        "type": "exports_available",
    }
    assert result["driver"]["boundary"] == {"kind": "completed"}
    assert result["run_status"] == "completed"
    assert result["task_states"] == {"producer": "completed", "consumer": "completed"}
    assert result["coordinator_handoff_statuses"] == ["completed", "completed"]

    assert [item.rsplit(":task:", 1)[-1] for item in host.created_task_ids] == [
        "producer",
        "consumer",
    ]
    assert [item["task_id"].rsplit(":task:", 1)[-1] for item in host.child_bootstraps] == [
        "producer",
        "consumer",
    ]
    assert len(host.direct_plans) == 2
    assert all(item["protocol"] == "lane-direct-work/v1" for item in host.direct_plans)
    assert all(item["execution_mode"] == "native_preferred" for item in host.direct_plans)
    assert all(item["status"] == "completed" for item in host.child_handoffs)
    producer_export = host.child_handoffs[0]["evidence"]["exports"][0]
    assert producer_export["name"] == "ProducerArtifact"
    assert producer_export["artifact_ref"].startswith("artifact://sha256:")


def test_native_required_without_advertised_capability_blocks_truthfully(
    tmp_path: Path,
) -> None:
    result = qualify_native_required_negative(
        tmp_path, intent_id="native-required-negative"
    )
    assert result["executed"] == []
    assert result["driver"]["boundary"]["kind"] == "lane-blocked"
    assert result["work_unit_state"] == "blocked"
    assert len(result["handoffs"]) == 1
    assert result["handoffs"][0]["status"] == "blocked"
    assert "HOST_CAPABILITY_BLOCKED" in result["handoffs"][0]["payload_json"]
    assert "native_required" in result["handoffs"][0]["payload_json"]


def test_product_canary_persists_exact_lane_handoff_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "fixture"
    prepare_workspace(workspace)
    task = task_spec(
        workspace,
        task_id="canary",
        condition="canary result is independently verified",
        exports=["CanaryArtifact"],
    )
    db_path = tmp_path / "canary.db"
    artifact_root = tmp_path / "artifacts"
    started = SinglePublicSkillAPI().start(
        public_request(
            workspace,
            intent_id="persist-exact-lane-handoff",
            tasks=[task],
        ),
        db_path=db_path,
    )
    run_id = str(started["run_ref"]).removeprefix("run://")
    with Store(db_path) as store:
        GlobalScheduler(store).step(run_id)

    result = drive_lane_direct(
        db_path=db_path,
        run_id=run_id,
        task_id="canary",
        artifact_root=artifact_root,
    )

    assert result["handoff"]["status"] == "completed"
    assert result["handoff_artifact_ref"].startswith("artifact://")
    with Store(db_path) as store:
        task_id = result["task_id"]
        persisted = store.get_driver_handoff(
            "lane", task_id, result["handoff"]["handoff_id"]
        )
        assert persisted is not None
        assert persisted["payload"] == result["handoff"]
        raw = ArtifactStore(store, root=artifact_root).resolve(
            result["handoff_artifact_ref"]
        )
        assert json.loads(raw) == result["handoff"]
