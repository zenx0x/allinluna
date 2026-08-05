"""Canonical lane-handoff verification before any Task completion write."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Mapping

from .artifacts import ArtifactError, ArtifactStore
from .core.protocol import LANE_HANDOFF_PROTOCOL


class HandoffVerificationError(ValueError):
    """A completed handoff failed one explicit verification stage."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"handoff {stage} verification failed: {message}")


def _artifact_ref(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        raw = value.get("artifact_ref") or value.get("ref") or value.get("uri")
        return str(raw) if raw else None
    return None


class HandoffProcessor:
    """Fail-closed schema, evidence, workspace, Pack, and contract verifier."""

    REQUIRED = frozenset({
        "kind", "protocol", "handoff_kind", "handoff_id", "run_ref", "status",
        "summary", "artifacts", "checks", "blockers", "promotion_requests",
        "task_id", "contract_revision", "exports", "done_when", "workspace_evidence",
    })

    def __init__(
        self,
        store: Any,
        *,
        artifacts: ArtifactStore | None = None,
        workspace_verifier: Callable[[Mapping[str, Any], tuple[str, ...]], Any] | None = None,
        packs: Any = None,
    ) -> None:
        self.store = store
        root = None
        path = getattr(store, "path", None)
        if path is not None and str(path) != ":memory:":
            root = path.parent / "artifacts"
        self.artifacts = artifacts or ArtifactStore(store, root=root)
        self.workspace_verifier = workspace_verifier
        if packs is None:
            from .packs.manifest import builtin_registry
            packs = builtin_registry()
        self.packs = packs

    def verify(self, task: Mapping[str, Any], handoff: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(handoff)
        missing = sorted(self.REQUIRED - set(value))
        if missing:
            raise HandoffVerificationError("schema", f"missing fields: {missing}")
        if value.get("kind") != "handoff" or value.get("protocol") != LANE_HANDOFF_PROTOCOL or value.get("handoff_kind") != "lane":
            raise HandoffVerificationError("schema", f"expected {LANE_HANDOFF_PROTOCOL}")
        if value.get("status") != "completed":
            raise HandoffVerificationError("status", "processor only verifies completed handoffs")
        expected_ids = {str(task["id"]), str(task.get("local_id") or "")}
        if str(value.get("task_id")) not in expected_ids:
            raise HandoffVerificationError("identity", "Task identity mismatch")
        if str(value.get("run_ref")) != f"run://{task['run_id']}":
            raise HandoffVerificationError("identity", "Run identity mismatch")
        if int(value.get("contract_revision")) != int(task["contract_version"]):
            raise HandoffVerificationError("contract", "stale contract revision")
        if value.get("blockers"):
            raise HandoffVerificationError("blockers", "unresolved blockers remain")

        checks = list(value.get("checks") or ())
        if not checks or any(not isinstance(item, Mapping) or item.get("status") != "pass" for item in checks):
            raise HandoffVerificationError("checks", "passing check evidence is required")

        contract = self.store.get_contract(str(task["contract_id"]), int(task["contract_version"])) or {}
        declared = {str(item.get("name")) for item in contract.get("exports", ()) if isinstance(item, Mapping) and item.get("name")}
        delivered = {str(item.get("name")) for item in value.get("exports", ()) if isinstance(item, Mapping) and item.get("name")}
        if not declared.issubset(delivered):
            raise HandoffVerificationError("exports", f"missing declared exports: {sorted(declared - delivered)}")

        done_evidence = value.get("done_when")
        done_evidence = list(done_evidence) if isinstance(done_evidence, (list, tuple)) else []
        satisfied = {
            str(item.get("condition")) for item in done_evidence
            if isinstance(item, Mapping) and item.get("satisfied") is True and item.get("condition")
        }
        expected_done = {str(item) for item in contract.get("done_when", ())}
        if not expected_done.issubset(satisfied):
            raise HandoffVerificationError("done_when", f"unsatisfied conditions: {sorted(expected_done - satisfied)}")

        refs = {_artifact_ref(item) for item in value.get("artifacts", ())}
        refs.update(_artifact_ref(item) for item in value.get("exports", ()))
        for ref in sorted(item for item in refs if item):
            try:
                self.artifacts.verify(ref)
            except ArtifactError as exc:
                raise HandoffVerificationError("artifacts", f"{ref}: {exc}") from exc

        workspace = value.get("workspace_evidence")
        if not isinstance(workspace, Mapping):
            raise HandoffVerificationError("workspace", "workspace_evidence must be an object")
        changed = tuple(str(item) for item in workspace.get("changed_paths", value.get("changed_paths", ())) or ())
        evidence = self.workspace_verifier(workspace, changed) if self.workspace_verifier else workspace
        if hasattr(evidence, "to_dict"):
            evidence = evidence.to_dict()
        if not isinstance(evidence, Mapping) or evidence.get("valid") is not True:
            raise HandoffVerificationError("workspace", "WorkspaceVerifier did not return valid evidence")
        if evidence.get("ownership_valid", True) is not True or evidence.get("protected_unchanged", True) is not True:
            raise HandoffVerificationError("workspace", "ownership or protected-path verification failed")
        value["workspace_evidence"] = dict(evidence)
        # Pack verifiers consume the normalized verdict, never an unverified
        # producer assertion.  Keeping this at the processor boundary makes
        # GSD integrate checks and every future Pack share one authority.
        value["workspace_valid"] = True

        run = self.store.get_run(str(task["run_id"])) or {}
        policy = run.get("policy") if isinstance(run.get("policy"), Mapping) else {}
        pack_id = str(policy.get("workflow_pack") or "delivery")
        pack = self.packs.require(pack_id)
        task_view = SimpleNamespace(id=str(task.get("local_id") or task["id"]))
        if any(not bool(verifier(value)) for verifier in pack.verifiers(task_view)):
            raise HandoffVerificationError("pack", f"{pack_id} verifier rejected the handoff")
        return value


__all__ = ["HandoffProcessor", "HandoffVerificationError"]
