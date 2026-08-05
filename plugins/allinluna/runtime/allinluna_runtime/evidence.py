"""Independent evidence collection for completed Lane handoffs.

Lane execution is intentionally unable to assert its own completion.  This
module is the observation boundary: checks are run by :class:`CheckRunner`,
payloads are recorded in :class:`~allinluna_runtime.artifacts.ArtifactStore`,
and workspace facts come from a real ``WorkspaceAdapter``.  The resulting
bundle is the only evidence shape accepted by ``HandoffProcessor``.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .adapters.workspace.base import stable_digest
from .artifacts import ArtifactError, ArtifactStore
from .verification import VerificationSpec, VerificationSpecError, verification_specs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _raw(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    method = getattr(value, "to_dict", None)
    if callable(method):
        result = method()
        if isinstance(result, Mapping):
            return dict(result)
    return dict(vars(value))


def _status(value: Any) -> str:
    text = str(value or "failed").strip().lower()
    if text in {"passed", "success", "succeeded", "ok", "completed"}:
        return "pass"
    if text in {"pass", "failed", "fail", "error", "blocked", "skipped", "unknown"}:
        return "pass" if text == "pass" else text
    return "failed"


def _artifact_ref(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        raw = value.get("artifact_ref") or value.get("ref") or value.get("uri")
        return str(raw) if raw else None
    return None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class CheckRunnerProtocol(Protocol):
    """A bounded external check runner.

    The built-in implementation is intentionally the only in-process runner:
    it delegates checks to ``subprocess.run(..., timeout=...)``.  Evidence
    collection does not execute caller-provided Python callables because Python
    cannot safely terminate an arbitrary blocked callable on every host.
    """

    def run(self, check: Any, *, task_id: str | None = None, scope: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CheckReceipt:
    """A receipt produced by an executed check, not by a Lane producer."""

    name: str
    status: str
    receipt_id: str
    command: str | tuple[str, ...]
    exit_code: int | None
    source: str
    observed_at: str
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    satisfies: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        command: str | list[str] = list(self.command) if isinstance(self.command, tuple) else self.command
        return {
            "name": self.name,
            "status": self.status,
            "receipt_id": self.receipt_id,
            "command": command,
            "exit_code": self.exit_code,
            "source": self.source,
            "observed_at": self.observed_at,
            "stdout_artifact_ref": self.stdout_ref,
            "stderr_artifact_ref": self.stderr_ref,
            "satisfies": list(self.satisfies),
            "details": dict(self.details),
        }


class CheckRunner:
    """Run a declared check and persist its stdout/stderr as artifacts.

    A mapping containing only ``status=pass`` is deliberately not executable
    evidence. Callers must provide a command; direct Python callables are
    rejected because an arbitrary callable cannot be safely terminated. This
    prevents a Lane from self-signing or indefinitely blocking verification.
    """

    source = "allinluna.check-runner"
    DEFAULT_TIMEOUT_SECONDS = 60.0
    MAX_TIMEOUT_SECONDS = 900.0

    def __init__(
        self,
        artifact_store: ArtifactStore | None = None,
        *,
        cwd: str | Path | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.artifact_store = artifact_store
        self.cwd = str(cwd) if cwd is not None else None
        self.timeout_seconds = self._timeout(timeout_seconds)

    @classmethod
    def _timeout(cls, value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("check timeout_seconds must be a positive number")
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("check timeout_seconds must be a positive number") from exc
        if not 0 < seconds <= cls.MAX_TIMEOUT_SECONDS:
            raise ValueError(f"check timeout_seconds must be between 0 and {cls.MAX_TIMEOUT_SECONDS:g}")
        return seconds

    def run(
        self,
        check: Any,
        *,
        task_id: str | None = None,
        scope: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if isinstance(check, str):
            raise ValueError("natural-language checks are not executable; declare a typed command VerificationSpec")
        value = _raw(check)
        name = str(value.get("name") or value.get("id") or value.get("command") or "unnamed-check")
        command_value = value.get("command")
        runner = value.get("runner") or value.get("run")
        timeout_seconds = self._timeout(value.get("timeout_seconds", self.timeout_seconds))
        started = _now()
        stdout = ""
        stderr = ""
        exit_code: int | None = None
        details: dict[str, Any] = {
            "task_id": task_id,
            "scope": dict(scope or {}),
            "timeout_seconds": timeout_seconds,
        }
        status = "failed"
        command: str | tuple[str, ...] = ""
        try:
            if callable(runner):
                raise ValueError(
                    "direct callable checks are disabled; use an executable command with timeout_seconds"
                )
            elif command_value:
                if isinstance(command_value, str):
                    command = tuple(shlex.split(command_value, posix=False))
                elif isinstance(command_value, Sequence) and not isinstance(command_value, (bytes, bytearray)):
                    command = tuple(str(item) for item in command_value)
                else:
                    raise TypeError("check command must be a string or sequence")
                if not command:
                    raise ValueError("check command is empty")
                completed = subprocess.run(
                    list(command),
                    cwd=str(value.get("cwd") or (scope or {}).get("worktree") or self.cwd or "" ) or None,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_seconds,
                )
                exit_code = int(completed.returncode)
                stdout = completed.stdout
                stderr = completed.stderr
                status = "pass" if exit_code == 0 else "fail"
            else:
                details["error"] = "check has no executable command or runner"
                command = f"unexecuted:{name}"
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.stdout or "")
            timeout_error = f"check timed out after {timeout_seconds:g}s"
            stderr = str(exc.stderr or timeout_error)
            details.update({"error": timeout_error, "error_code": "timeout"})
            status = "timeout"
            command = command or f"timed-out:{name}"
        except (OSError, TypeError, ValueError) as exc:
            details["error"] = f"{type(exc).__name__}: {exc}"
            details["error_code"] = "execution-error"
            status = "failed"
            command = command or f"failed:{name}"

        stdout_ref = self._put_output(stdout, kind="check-log", name=name, task_id=task_id)
        stderr_ref = self._put_output(stderr, kind="tool-log", name=name, task_id=task_id)
        observed = _now()
        receipt_id = "check-receipt-" + stable_digest({
            "name": name,
            "command": list(command) if isinstance(command, tuple) else command,
            "status": status,
            "exit_code": exit_code,
            "stdout_ref": stdout_ref,
            "stderr_ref": stderr_ref,
            "observed_at": observed,
        })
        satisfies = tuple(str(item) for item in value.get("satisfies", ()) or ())
        return CheckReceipt(
            name=name,
            status=status,
            receipt_id=receipt_id,
            command=command,
            exit_code=exit_code,
            source=self.source,
            observed_at=observed,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            satisfies=satisfies,
            details={**details, "started_at": started},
        ).to_dict()

    run_check = run

    def _put_output(self, content: str, *, kind: str, name: str, task_id: str | None) -> str | None:
        if self.artifact_store is None or not content:
            return None
        record = self.artifact_store.put(
            content.encode("utf-8"),
            kind=kind,
            produced_by=self.source,
            # The payload identity is content-addressed.  Do not make
            # otherwise identical output immutable-conflicting merely because
            # two Tasks ran the same check text.
            metadata={"check_output": True},
        )
        return record.ref


@dataclass(frozen=True)
class EvidenceProfile:
    name: str
    checks_required: bool = True
    workspace_required: bool = True
    artifacts_required: bool = False
    exports_required: bool = False
    allow_projectless_workspace: bool = False


EVIDENCE_PROFILES: dict[str, EvidenceProfile] = {
    "software": EvidenceProfile("software", workspace_required=True),
    "projectless-analysis": EvidenceProfile("projectless-analysis", workspace_required=False, allow_projectless_workspace=True),
    "research": EvidenceProfile("research", workspace_required=False, artifacts_required=True, allow_projectless_workspace=True),
    "docs": EvidenceProfile("docs", workspace_required=True),
    "custom": EvidenceProfile("custom", workspace_required=True),
}


class EvidenceCollectionError(ValueError):
    """A collected evidence bundle is malformed or cannot be verified."""


class EvidenceCollector:
    """Collect and validate external evidence for a Lane handoff."""

    API_VERSION = 1
    COLLECTOR = "allinluna.evidence-collector/v1"

    def __init__(
        self,
        store: Any,
        *,
        artifact_store: ArtifactStore | None = None,
        workspace_adapter: Any = None,
        workspace: Any = None,
        check_runner: CheckRunnerProtocol | None = None,
        profile: str | EvidenceProfile | None = None,
        verifier_profile: str | EvidenceProfile | None = None,
        profile_config: Mapping[str, Any] | None = None,
    ) -> None:
        self.store = store
        root = None
        path = getattr(store, "path", None)
        if path is not None and str(path) != ":memory:":
            root = Path(path).parent / "artifacts"
        self.artifacts = artifact_store or ArtifactStore(store, root=root)
        self.workspace_adapter = workspace_adapter if workspace_adapter is not None else workspace
        self.profile = profile if profile is not None else verifier_profile
        self.profile_config = dict(profile_config or {})
        if check_runner is not None and not isinstance(check_runner, CheckRunner):
            raise TypeError(
                "EvidenceCollector accepts only CheckRunner; external callable runners cannot be force-terminated"
            )
        self.check_runner = check_runner or CheckRunner(self.artifacts)

    def selected_profile(self, task: Mapping[str, Any], explicit: str | EvidenceProfile | None = None) -> EvidenceProfile:
        value: Any = explicit or self.profile
        run = self.store.get_run(str(task.get("run_id"))) or {}
        policy = run.get("policy") if isinstance(run.get("policy"), Mapping) else {}
        if value is None:
            value = task.get("evidence_profile") or policy.get("evidence_profile")
        if value is None:
            pack = str(policy.get("workflow_pack") or "delivery")
            repository_mode = str(policy.get("repository_mode") or "")
            value = "research" if pack == "research-routes-bridge" else "projectless-analysis" if repository_mode == "projectless" else "software"
        if isinstance(value, EvidenceProfile):
            return value
        name = str(value)
        if name not in EVIDENCE_PROFILES:
            raise EvidenceCollectionError(f"unknown evidence profile: {name}")
        base = EVIDENCE_PROFILES[name]
        config = self.profile_config
        return EvidenceProfile(
            name=base.name,
            checks_required=bool(config.get("checks_required", base.checks_required)),
            workspace_required=bool(config.get("workspace_required", base.workspace_required)),
            artifacts_required=bool(config.get("artifacts_required", base.artifacts_required)),
            exports_required=bool(config.get("exports_required", base.exports_required)),
            allow_projectless_workspace=bool(config.get("allow_projectless_workspace", base.allow_projectless_workspace)),
        )

    def collect(
        self,
        task: Mapping[str, Any] | str,
        handoff: Mapping[str, Any] | None = None,
        *,
        checks: Sequence[Any] | None = None,
        artifacts: Sequence[Any] | None = None,
        exports: Sequence[Any] | None = None,
        workspace_scope: Mapping[str, Any] | None = None,
        profile: str | EvidenceProfile | None = None,
    ) -> dict[str, Any]:
        task_value = self.store.get_task(str(task)) if isinstance(task, str) else dict(task)
        if task_value is None:
            raise KeyError(task)
        handoff_value = dict(handoff or {})
        selected = self.selected_profile(task_value, profile)
        contract = self.store.get_contract(str(task_value.get("contract_id") or ""), int(task_value.get("contract_version", 1))) or {}
        done_conditions = tuple(str(item) for item in contract.get("done_when", ()) or ())
        try:
            declared_specs = verification_specs(contract.get("verification_specs", ()))
            supplied_specs = verification_specs(checks) if checks is not None else verification_specs(handoff_value.get("verification_specs", ()))
        except VerificationSpecError as exc:
            raise EvidenceCollectionError(str(exc)) from exc
        if checks is None and supplied_specs and tuple(spec.to_dict() for spec in supplied_specs) != tuple(spec.to_dict() for spec in declared_specs):
            raise EvidenceCollectionError("handoff cannot replace contract verification_specs")
        # ``checks=`` is a direct Collector compatibility seam for callers
        # that already provide typed procedures.  It never accepts language as
        # a command and the Lane handoff path cannot use it to alter a contract.
        active_specs = declared_specs or supplied_specs
        check_specs = tuple(spec for spec in active_specs if spec.kind == "command")
        run_check = getattr(self.check_runner, "run", None) or getattr(self.check_runner, "run_check", None)
        if not callable(run_check):
            raise TypeError("check_runner must expose run or run_check")
        check_receipts: list[dict[str, Any]] = []
        for item in check_specs:
            try:
                check_receipts.append(
                    dict(run_check(item.to_dict(), task_id=str(task_value["id"]), scope=workspace_scope))
                )
            except Exception as exc:
                check_receipts.append(self._check_error_receipt(item, str(task_value["id"]), exc))
        errors: list[str] = []
        workspace = self._collect_workspace(task_value, handoff_value, workspace_scope, selected, errors)
        observation_receipts = self._observe_specs(active_specs, workspace)
        check_receipts.extend(observation_receipts)
        automatable_specs = tuple(spec for spec in active_specs if spec.kind in {"command", "artifact", "workspace"})
        manual_evidence_required = not automatable_specs or any(spec.kind == "human" for spec in active_specs)
        if manual_evidence_required:
            errors.append("manual_evidence_required")
        if selected.checks_required and automatable_specs and (not check_receipts or any(not self._valid_check(item) for item in check_receipts)):
            errors.append("checks_not_verified")

        done_evidence = []
        for condition in done_conditions:
            matching = [item for item in check_receipts if condition in {str(name) for name in item.get("satisfies", ())}]
            satisfied = bool(matching) and all(self._valid_check(item) for item in matching)
            done_evidence.append({"condition": condition, "satisfied": satisfied, "source_receipts": [item.get("receipt_id") for item in matching]})
            if not satisfied:
                errors.append(f"done_when:{condition}")

        artifact_values = list(artifacts if artifacts is not None else handoff_value.get("artifacts") or ())
        export_values = list(exports if exports is not None else handoff_value.get("exports") or ())
        artifact_refs = self._verified_artifacts(artifact_values, errors)
        artifact_refs = sorted(set(artifact_refs) | {
            str(ref)
            for item in check_receipts
            for ref in (item.get("stdout_artifact_ref"), item.get("stderr_artifact_ref"))
            if ref
        })
        collected_exports = self._verified_exports(export_values, contract, errors)
        if selected.artifacts_required and not artifact_refs:
            errors.append("artifacts_required")
        declared = self._declared_exports(contract)
        if selected.exports_required and declared and not collected_exports:
            errors.append("exports_required")
        if declared and not declared.issubset({str(item.get("name")) for item in collected_exports}):
            errors.append("declared_exports_missing")

        bundle: dict[str, Any] = {
            "kind": "evidence-bundle",
            "schema_version": "1.0",
            "protocol": "evidence-bundle/v1",
            "collector": self.COLLECTOR,
            "collection_id": "collection-" + stable_digest({"task": task_value["id"], "checks": check_receipts, "workspace": workspace, "artifacts": artifact_refs, "exports": collected_exports}),
            "task_id": str(task_value["id"]),
            "run_ref": f"run://{task_value['run_id']}",
            "contract_revision": int(task_value.get("contract_version", 1)),
            "profile": selected.name,
            "checks": check_receipts,
            "verification_specs": [spec.to_dict() for spec in active_specs],
            "manual_evidence_required": manual_evidence_required,
            "done_when": done_evidence,
            "artifacts": artifact_refs,
            "exports": collected_exports,
            "workspace_evidence": workspace,
            "changed_paths": list(workspace.get("changed_paths", ())) if isinstance(workspace, Mapping) else [],
            "workspace_valid": bool(workspace.get("valid") is True) if isinstance(workspace, Mapping) else False,
            "errors": sorted(set(errors)),
            "verified": not errors,
            "created_at": _now(),
        }
        bundle["evidence_digest"] = self._digest(bundle)
        return bundle

    def verify(self, task: Mapping[str, Any], bundle: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(bundle)
        errors: list[str] = []
        contract = self.store.get_contract(str(task.get("contract_id") or ""), int(task.get("contract_version", 1))) or {}
        try:
            declared_specs = verification_specs(contract.get("verification_specs", ()))
            bundled_specs = verification_specs(value.get("verification_specs", ()))
        except VerificationSpecError as exc:
            errors.append(f"verification_specs:{exc}")
            declared_specs = bundled_specs = ()
        if declared_specs and tuple(item.to_dict() for item in declared_specs) != tuple(item.to_dict() for item in bundled_specs):
            errors.append("verification_specs_contract_mismatch")
        if value.get("kind") != "evidence-bundle" or value.get("protocol") != "evidence-bundle/v1" or value.get("collector") != self.COLLECTOR:
            errors.append("collector_provenance")
        if value.get("verified") is not True:
            errors.append("bundle_not_verified")
        if str(value.get("task_id")) != str(task.get("id")):
            errors.append("task_identity")
        if str(value.get("run_ref")) != f"run://{task.get('run_id')}":
            errors.append("run_identity")
        if int(value.get("contract_revision", -1)) != int(task.get("contract_version", 1)):
            errors.append("contract_revision")
        if value.get("evidence_digest") != self._digest(value):
            errors.append("evidence_digest")
        if value.get("errors"):
            errors.extend(str(item) for item in value.get("errors", ()))
        if value.get("manual_evidence_required") is True:
            errors.append("manual_evidence_required")
        if not value.get("checks") or any(not self._valid_check(item) for item in value.get("checks", ())):
            errors.append("checks_not_verified")
        if not all(isinstance(item, Mapping) and item.get("satisfied") is True for item in value.get("done_when", ())):
            errors.append("done_when_not_verified")
        workspace = value.get("workspace_evidence")
        if not isinstance(workspace, Mapping) or workspace.get("valid") is not True or workspace.get("ownership_valid", True) is not True or workspace.get("protected_unchanged", True) is not True or workspace.get("source") != self.COLLECTOR:
            errors.append("workspace_not_verified")
        for ref in self._all_refs(value):
            try:
                self.artifacts.verify(ref)
            except ArtifactError:
                errors.append(f"artifact_unverified:{ref}")
        if errors:
            raise EvidenceCollectionError("; ".join(sorted(set(errors))))
        return value

    def _collect_workspace(
        self,
        task: Mapping[str, Any],
        handoff: Mapping[str, Any],
        scope: Mapping[str, Any] | None,
        profile: EvidenceProfile,
        errors: list[str],
    ) -> dict[str, Any]:
        if self.workspace_adapter is None:
            if profile.allow_projectless_workspace or profile.name == "projectless-analysis":
                return {"adapter": "projectless", "operation": "not-applicable", "status": "not-applicable", "valid": True, "changed_paths": [], "ownership_valid": True, "protected_unchanged": True, "source": self.COLLECTOR}
            errors.append("workspace_adapter_missing")
            return {"adapter": "none", "operation": "unavailable", "status": "rejected", "valid": False, "changed_paths": [], "ownership_valid": False, "protected_unchanged": False, "errors": ["workspace_adapter_missing"]}
        adapter_scope = dict(scope or {})
        identity = self.workspace_adapter.identity(adapter_scope)
        identity_value = identity.to_dict() if hasattr(identity, "to_dict") else dict(identity)
        actual_paths = tuple(str(item) for item in identity_value.get("changed_paths", ()) or ())
        evidence = self.workspace_adapter.verify_changed_paths(adapter_scope, actual_paths)
        value = evidence.to_dict() if hasattr(evidence, "to_dict") else dict(evidence)
        reported = handoff.get("changed_paths")
        if reported is None and isinstance(handoff.get("workspace_evidence"), Mapping):
            reported = handoff["workspace_evidence"].get("changed_paths")
        if reported not in (None, (), [] ) and tuple(sorted(map(str, reported or ()))) != tuple(sorted(actual_paths)):
            errors.append("changed_paths_claim_mismatch")
        if value.get("valid") is not True or value.get("ownership_valid", True) is not True or value.get("protected_unchanged", True) is not True:
            errors.append("workspace_not_verified")
        value["identity"] = identity_value
        value["changed_paths"] = list(actual_paths)
        value["source"] = self.COLLECTOR
        return value

    def _observe_specs(
        self,
        specs: Sequence[VerificationSpec],
        workspace: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Produce receipts for non-command observable procedures.

        This deliberately contains no interpretation of ``done_when``.  A
        procedure only satisfies conditions which its typed ``satisfies`` list
        declares.
        """
        receipts: list[dict[str, Any]] = []
        for spec in specs:
            if spec.kind == "command" or spec.kind == "human" or spec.kind == "pack":
                continue
            status = "pass"
            details: dict[str, Any] = {"verification_kind": spec.kind}
            if spec.kind == "artifact":
                try:
                    self.artifacts.verify(str(spec.artifact_ref))
                except ArtifactError as exc:
                    status = "failed"
                    details["error"] = str(exc)
            elif spec.kind == "workspace":
                if workspace.get("valid") is not True or workspace.get("ownership_valid", True) is not True or workspace.get("protected_unchanged", True) is not True:
                    status = "failed"
                    details["error"] = "workspace evidence is not valid"
            observed = _now()
            receipt_id = "observation-receipt-" + stable_digest({"id": spec.id, "kind": spec.kind, "status": status, "observed_at": observed})
            receipts.append(
                CheckReceipt(
                    name=spec.id,
                    status=status,
                    receipt_id=receipt_id,
                    command=f"observe:{spec.kind}",
                    exit_code=0 if status == "pass" else None,
                    source=self.COLLECTOR,
                    observed_at=observed,
                    satisfies=spec.satisfies,
                    details=details,
                ).to_dict()
            )
        return receipts

    def _verified_artifacts(self, values: Sequence[Any], errors: list[str]) -> list[str]:
        refs: list[str] = []
        for item in values:
            ref = _artifact_ref(item)
            if not ref:
                errors.append("artifact_ref_missing")
                continue
            try:
                self.artifacts.verify(ref)
            except ArtifactError:
                errors.append(f"artifact_unverified:{ref}")
                continue
            refs.append(ref)
        return sorted(set(refs))

    def _check_error_receipt(self, spec: Any, task_id: str, exc: Exception) -> dict[str, Any]:
        value = {"name": str(spec)} if isinstance(spec, str) else _raw(spec)
        name = str(value.get("name") or value.get("id") or "unnamed-check")
        error = f"{type(exc).__name__}: {exc}"
        stderr_ref = None
        try:
            record = self.artifacts.put(
                error.encode("utf-8"),
                kind="tool-log",
                produced_by=self.COLLECTOR,
                metadata={"check_error": True, "task_id": task_id},
            )
            stderr_ref = record.ref
        except ArtifactError:
            pass
        return CheckReceipt(
            name=name,
            status="error",
            receipt_id="check-error-" + stable_digest({"task": task_id, "name": name, "error": error}),
            command="collector:run-check",
            exit_code=None,
            source=self.COLLECTOR,
            observed_at=_now(),
            stderr_ref=stderr_ref,
            satisfies=tuple(str(item) for item in value.get("satisfies", ()) or ()),
            details={"error": error, "error_code": "runner-exception"},
        ).to_dict()

    def _verified_exports(self, values: Sequence[Any], contract: Mapping[str, Any], errors: list[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in values:
            value = {"name": str(item)} if isinstance(item, str) else dict(item) if isinstance(item, Mapping) else {}
            name = str(value.get("name") or value.get("port_name") or "")
            ref = _artifact_ref(value)
            if not name or not ref:
                errors.append("export_ref_missing")
                continue
            try:
                self.artifacts.verify(ref)
            except ArtifactError:
                errors.append(f"export_unverified:{name}")
                continue
            result.append({"name": name, "artifact_ref": ref, "version": int(value.get("version", 1)), "evidence_source": self.COLLECTOR})
        return sorted(result, key=lambda item: item["name"])

    @staticmethod
    def _declared_exports(contract: Mapping[str, Any]) -> set[str]:
        return {str(item.get("name")) for item in contract.get("exports", ()) if isinstance(item, Mapping) and item.get("name")}

    @staticmethod
    def _valid_check(value: Any) -> bool:
        return isinstance(value, Mapping) and value.get("status") == "pass" and bool(value.get("receipt_id")) and str(value.get("source", "")).startswith("allinluna.check-runner")

    @staticmethod
    def _digest(value: Mapping[str, Any]) -> str:
        material = {key: item for key, item in value.items() if key != "evidence_digest"}
        return "sha256:" + hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()

    def _all_refs(self, value: Mapping[str, Any]) -> tuple[str, ...]:
        refs = [str(_artifact_ref(item)) for item in value.get("artifacts", ()) if _artifact_ref(item)]
        for item in value.get("exports", ()):
            ref = _artifact_ref(item)
            if ref:
                refs.append(ref)
        for item in value.get("checks", ()):
            if isinstance(item, Mapping):
                for key in ("stdout_artifact_ref", "stderr_artifact_ref"):
                    if item.get(key):
                        refs.append(str(item[key]))
        return tuple(sorted(set(refs)))

    collect_handoff_evidence = collect
    collect_for_task = collect
    verify_bundle = verify


__all__ = [
    "CheckReceipt",
    "CheckRunner",
    "CheckRunnerProtocol",
    "EVIDENCE_PROFILES",
    "EvidenceCollectionError",
    "EvidenceCollector",
    "EvidenceProfile",
]
