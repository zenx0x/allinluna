from __future__ import annotations

import sys

import pytest

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.evidence import CheckRunner, EvidenceCollectionError, EvidenceCollector
from allinluna_runtime.packs.delivery import DeliveryPack
from allinluna_runtime.packs.gsd import GSDPack
from allinluna_runtime.packs.public_skill import SinglePublicSkillAPI
from allinluna_runtime.store import Store
from allinluna_runtime.verification import VerificationSpec
from tests.fixtures.vnext.trusted_checks import trusted_command_spec


def _store_task(store: Store, *, specs: list[dict] | None = None) -> dict:
    store.create_run("run-verification", "verification", {"workflow_pack": "delivery"}, "contract://root@1")
    store.put_contract(
        {
            "id": "contract-verification",
            "version": 1,
            "outcome": "deliver verified outcome",
            "done_when": ["semantic outcome is complete"],
            "verification_specs": specs or [],
        }
    )
    return store.create_task(
        {
            "id": "task-verification",
            "run_id": "run-verification",
            "outcome": "deliver verified outcome",
            "contract_id": "contract-verification",
        }
    )


def test_contract_command_spec_is_persisted_and_not_replaced_by_done_when(tmp_path):
    spec = trusted_command_spec(
        tmp_path,
        identifier="unit-tests",
        command=[sys.executable, "-c", "print('verified')"],
        satisfies=["semantic outcome is complete"],
        timeout_seconds=10,
    )
    with Store(tmp_path / "runtime.db") as store:
        task = _store_task(store, specs=[spec])
        contract = store.get_contract("contract-verification") or {}
        assert contract["verification_specs"] == [spec]
        artifacts = ArtifactStore(store, root=tmp_path / "artifacts")
        evidence = EvidenceCollector(
            store, artifact_store=artifacts, check_runner=CheckRunner(artifacts), profile="projectless-analysis"
        ).collect(task)

    assert evidence["verified"] is True
    assert evidence["manual_evidence_required"] is False
    assert evidence["checks"][0]["name"] == "unit-tests"
    assert evidence["checks"][0]["command"] != "unexecuted:semantic outcome is complete"
    assert evidence["done_when"] == [{"condition": "semantic outcome is complete", "satisfied": True, "source_receipts": [evidence["checks"][0]["receipt_id"]]}]


def test_contract_without_automatable_spec_requires_manual_evidence(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        task = _store_task(store)
        evidence = EvidenceCollector(store, profile="projectless-analysis").collect(task)
        assert evidence["manual_evidence_required"] is True
        assert evidence["verified"] is False
        assert "manual_evidence_required" in evidence["errors"]
        assert not evidence["checks"]
        with pytest.raises(EvidenceCollectionError, match="manual_evidence_required"):
            EvidenceCollector(store, profile="projectless-analysis").verify(task, evidence)


def test_untyped_or_natural_language_check_cannot_be_compiled_as_a_command(tmp_path):
    with Store(tmp_path / "runtime.db") as store:
        task = _store_task(store)
        with pytest.raises(EvidenceCollectionError, match="verification spec must be an object"):
            EvidenceCollector(store, profile="projectless-analysis").collect(
                task,
                checks=["run the tests"],
            )


def test_goal_compiler_propagates_only_typed_verification_specs():
    spec = {
        "id": "syntax-check",
        "kind": "command",
        "command": [sys.executable, "-c", "pass"],
        "satisfies": ["compile complete"],
    }
    graph = SinglePublicSkillAPI().compile(
        {
            "intent_id": "verification-compiler",
            "goal": "compile a typed verification contract",
            "done_when": ["compile complete"],
            "pack": {
                "id": "delivery",
                "version": "1.0.0",
                "config": {"outcome_domains": [{"id": "deliver", "outcome": "compile", "done_when": ["compile complete"], "verification_specs": [spec]}]},
            },
        }
    )
    contract = graph.task_graph.contracts[0]
    assert [item.to_dict() for item in contract.verification_specs] == [spec]


def test_builtin_packs_expose_only_typed_pack_verifiers():
    graph = SinglePublicSkillAPI().compile({"intent_id": "typed-pack", "goal": "deliver typed pack verifiers"})
    for pack, task in ((DeliveryPack(), graph.task_graph.tasks[0]), (GSDPack(), graph.task_graph.tasks[0])):
        specs = pack.verifiers(task)
        assert specs and all(isinstance(spec, VerificationSpec) and spec.kind == "pack" for spec in specs)
