"""Trust and execution policy for verification commands.

Verification commands are data crossing an execution boundary.  A typed
``VerificationSpec`` is therefore not sufficient evidence that a command may
run: its provenance and execution policy must also be evaluated.  This module
keeps that decision deterministic and independent from the subprocess runner.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


PROVENANCE_KINDS = frozenset(
    {
        "repository-discovered",
        "pack-signed",
        "user-approved",
        "deployment-approved",
        "model-proposed",
        "legacy-imported",
        "external-packet",
    }
)
TRUST_STATES = frozenset({"trusted", "approval_required", "denied"})
SANDBOXES = frozenset({"worktree", "container", "none"})
NETWORK_POLICIES = frozenset({"deny", "allow"})
APPROVAL_SCOPES = frozenset({"command", "network", "destructive"})


class CommandTrustError(ValueError):
    """A command cannot be normalized or violates the trust boundary."""


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise CommandTrustError("command trust metadata must be an object")


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise CommandTrustError("env_allowlist must be a string or sequence of strings")
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def normalize_command(value: Any) -> tuple[str, ...]:
    """Normalize an argv sequence without invoking a shell.

    String commands are accepted for compatibility, but shell operators are
    rejected.  The resulting tuple is always passed to ``subprocess`` with
    ``shell=False`` by :class:`~allinluna_runtime.evidence.CheckRunner`.
    """

    if isinstance(value, str):
        if not value.strip():
            raise CommandTrustError("verification command is empty")
        if re.search(r"[;&|<>`\n\r]", value):
            raise CommandTrustError("shell operators are not allowed in verification commands")
        try:
            command = tuple(shlex.split(value, posix=False))
        except ValueError as exc:
            raise CommandTrustError(f"verification command cannot be parsed: {exc}") from exc
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        command = tuple(str(item) for item in value)
    else:
        raise CommandTrustError("verification command must be a string or sequence")
    if not command or any(not item.strip() for item in command):
        raise CommandTrustError("verification command must contain non-empty argv entries")
    # An argv sequence is already shell-free.  Characters such as ``;`` may
    # legitimately occur inside a Python ``-c`` payload or a test selector;
    # only string-form commands need the shell-operator rejection above.
    return command


def _provenance(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"source_kind": value}
    return _mapping(value)


def _inside(path: str | Path, root: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def command_digest(command: Any) -> str:
    """Return the stable digest used to bind approval to one exact argv."""

    argv = normalize_command(command)
    material = json.dumps(list(argv), ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _approval_scopes(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        value = tuple(key for key, enabled in value.items() if enabled)
    elif isinstance(value, str):
        value = (value,)
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise CommandTrustError("approval scope must be a string, sequence, or object")
    aliases = {"verification-command": "command", "execute": "command"}
    scopes = tuple(dict.fromkeys(aliases.get(str(item).strip(), str(item).strip()) for item in value))
    if not scopes or any(item not in APPROVAL_SCOPES for item in scopes):
        raise CommandTrustError(f"approval scope must use only {sorted(APPROVAL_SCOPES)}")
    return scopes


@dataclass(frozen=True)
class ApprovalEvidence:
    """Explicit human/deployment evidence bound to one normalized command."""

    decision_id: str
    actor: str
    scope: tuple[str, ...]
    command_digest: str
    approved_at: str

    def __post_init__(self) -> None:
        decision_id = str(self.decision_id).strip()
        actor = str(self.actor).strip()
        digest = str(self.command_digest).strip().lower()
        approved_at = str(self.approved_at).strip()
        if not decision_id or not actor:
            raise CommandTrustError("approval decision_id and actor must be non-empty")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise CommandTrustError("approval command_digest must be a sha256 digest")
        try:
            timestamp = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CommandTrustError("approval approved_at must be an ISO-8601 timestamp") from exc
        if timestamp.tzinfo is None:
            raise CommandTrustError("approval approved_at must include a timezone")
        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "scope", _approval_scopes(self.scope))
        object.__setattr__(self, "command_digest", digest)
        object.__setattr__(self, "approved_at", approved_at)

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | "ApprovalEvidence") -> "ApprovalEvidence":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise CommandTrustError("approval evidence must be an object")
        return cls(
            decision_id=str(value.get("decision_id") or ""),
            actor=str(value.get("actor") or ""),
            scope=_approval_scopes(value.get("scope")),
            command_digest=str(value.get("command_digest") or ""),
            approved_at=str(value.get("approved_at") or ""),
        )

    def covers(self, *scopes: str) -> bool:
        return set(scopes).issubset(self.scope)

    def matches(self, command: Any) -> bool:
        return self.command_digest == command_digest(command)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "actor": self.actor,
            "scope": list(self.scope),
            "command_digest": self.command_digest,
            "approved_at": self.approved_at,
        }


def _repository_source_path(provenance: Mapping[str, Any], workspace: str | Path | None) -> tuple[str | None, str | None]:
    source_ref = str(provenance.get("source_ref") or "").strip()
    if not source_ref:
        return None, "repository-discovered provenance requires source_ref"
    if workspace is None:
        return None, "repository-discovered provenance requires an owned workspace"
    raw_path = Path(source_ref)
    if raw_path.is_absolute():
        candidate = raw_path
    else:
        parsed = urlparse(source_ref)
        if parsed.scheme and parsed.scheme not in {"file", "repo"}:
            return None, "repository-discovered source_ref must be a repository path"
        if parsed.scheme == "file":
            candidate = Path(unquote(parsed.path.lstrip("/") if os.name == "nt" else parsed.path))
        elif parsed.scheme == "repo":
            candidate = Path(workspace) / unquote((parsed.netloc + parsed.path).lstrip("/"))
        else:
            candidate = Path(workspace) / raw_path
    if not _inside(candidate, workspace):
        return str(candidate), "repository-discovered source_ref escapes the owned workspace"
    if not candidate.is_file():
        return str(candidate), "repository-discovered source_ref must identify an existing file"
    return str(candidate), None


def _trusted_pack_registry(provenance: Mapping[str, Any]) -> bool:
    identity = provenance.get("registry_identity")
    if isinstance(identity, Mapping):
        identity_id = str(identity.get("id") or identity.get("ref") or "").strip()
        identity_trusted = str(identity.get("state") or "").strip().lower() == "trusted" or identity.get("trusted") is True
    else:
        identity_id = str(identity or "").strip()
        identity_trusted = provenance.get("registry_trusted") is True
    return bool(identity_id and identity_trusted)


def _looks_destructive(command: Sequence[str]) -> bool:
    text = " ".join(command).lower()
    patterns = (
        r"(^|\s)(rm|rmdir|del|erase|remove-item)(\s|$)",
        r"(^|\s)git\s+(reset|clean|push|checkout\s+--)(\s|$)",
        r"(^|\s)(format|mkfs|shutdown)(\s|$)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _looks_networked(command: Sequence[str]) -> bool:
    text = " ".join(command).lower()
    return bool(re.search(r"(^|\s)(curl|wget|invoke-webrequest|invoke-restmethod)(\s|$)", text))


@dataclass(frozen=True)
class CommandTrustDecision:
    """The persisted, inspectable decision for one verification command."""

    state: str
    reason: str
    provenance: Mapping[str, Any] = field(default_factory=dict)
    sandbox: str = "worktree"
    network: str = "deny"
    env_allowlist: tuple[str, ...] = ()
    destructive: bool = False
    timeout_seconds: float | None = None
    permission: str | None = None
    command: tuple[str, ...] = ()
    cwd: str | None = None
    approval: Mapping[str, Any] = field(default_factory=dict)
    required_permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in TRUST_STATES:
            raise CommandTrustError(f"unsupported trust state: {self.state!r}")
        if self.sandbox not in SANDBOXES:
            raise CommandTrustError(f"unsupported verification sandbox: {self.sandbox!r}")
        if self.network not in NETWORK_POLICIES:
            raise CommandTrustError(f"unsupported verification network policy: {self.network!r}")
        if self.timeout_seconds is not None and not 0 < float(self.timeout_seconds) <= 900:
            raise CommandTrustError("verification timeout must be between 0 and 900 seconds")
        object.__setattr__(self, "provenance", dict(self.provenance))
        object.__setattr__(self, "env_allowlist", tuple(self.env_allowlist))
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "approval", dict(self.approval))
        object.__setattr__(self, "required_permissions", tuple(self.required_permissions))

    @property
    def executable(self) -> bool:
        return self.state == "trusted" and not self.required_permissions

    @property
    def approval_required(self) -> bool:
        return self.state == "approval_required" or bool(self.required_permissions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "provenance": dict(self.provenance),
            "sandbox": self.sandbox,
            "network": self.network,
            "env_allowlist": list(self.env_allowlist),
            "destructive": self.destructive,
            "timeout_seconds": self.timeout_seconds,
            "permission": self.permission,
            "command": list(self.command),
            "cwd": self.cwd,
            "approval": dict(self.approval),
            "required_permissions": list(self.required_permissions),
        }


TrustDecision = CommandTrustDecision


class CommandTrustEvaluator:
    """Evaluate whether a declared command may execute in the local runner."""

    def evaluate(
        self,
        command: Any,
        *,
        provenance: Mapping[str, Any] | str | None = None,
        trust: Mapping[str, Any] | str | None = None,
        approval: Mapping[str, Any] | ApprovalEvidence | None = None,
        execution: Mapping[str, Any] | None = None,
        cwd: str | Path | None = None,
        workspace: str | Path | None = None,
        approved: bool = False,
    ) -> CommandTrustDecision:
        argv = normalize_command(command)
        provenance_value = _provenance(provenance)
        source_kind = str(provenance_value.get("source_kind") or "unknown").strip()
        provenance_value["source_kind"] = source_kind
        execution_value = _mapping(execution)
        trust_value = _mapping(trust) if not isinstance(trust, str) else {"state": trust}
        requested_state = str(trust_value.get("state") or "").strip()
        approval_value = dict(approval) if isinstance(approval, Mapping) else approval.to_dict() if isinstance(approval, ApprovalEvidence) else {}
        explicit_approval: ApprovalEvidence | None = None
        approval_error: str | None = None
        if approval is not None:
            try:
                explicit_approval = ApprovalEvidence.from_value(approval)
                if not explicit_approval.matches(argv):
                    approval_error = "approval command_digest does not match the verification command"
                    explicit_approval = None
                elif not explicit_approval.covers("command"):
                    approval_error = "approval scope does not authorize command execution"
                    explicit_approval = None
            except CommandTrustError as exc:
                approval_error = str(exc)
        sandbox = str(execution_value.get("sandbox") or ("worktree" if workspace else "none"))
        if sandbox not in SANDBOXES:
            return CommandTrustDecision(
                state="denied",
                reason=f"unsupported sandbox: {sandbox}",
                provenance=provenance_value,
                sandbox="none",
                command=argv,
                cwd=str(cwd) if cwd else None,
                approval=approval_value,
            )
        network = str(execution_value.get("network") or "deny")
        if network not in NETWORK_POLICIES:
            return CommandTrustDecision(
                state="denied",
                reason=f"unsupported network policy: {network}",
                provenance=provenance_value,
                sandbox=sandbox,
                command=argv,
                cwd=str(cwd) if cwd else None,
                approval=approval_value,
            )
        env_allowlist = _strings(execution_value.get("env_allowlist"))
        destructive = bool(execution_value.get("destructive", False)) or _looks_destructive(argv)
        timeout = execution_value.get("timeout_seconds")
        if timeout is not None:
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                return CommandTrustDecision(
                    state="denied",
                    reason="invalid verification timeout",
                    provenance=provenance_value,
                    sandbox=sandbox,
                    command=argv,
                    approval=approval_value,
                )
            if not 0 < timeout <= 900:
                return CommandTrustDecision(
                    state="denied",
                    reason="verification timeout is outside the allowed range",
                    provenance=provenance_value,
                    sandbox=sandbox,
                    command=argv,
                    approval=approval_value,
                )

        actual_cwd = str(
            cwd
            or execution_value.get("cwd")
            or (workspace if sandbox == "worktree" else "")
            or ""
        ) or None
        if actual_cwd and workspace and not _inside(actual_cwd, workspace):
            return CommandTrustDecision(
                state="denied",
                reason="verification command cwd is outside the owned workspace",
                provenance=provenance_value,
                sandbox=sandbox,
                network=network,
                env_allowlist=env_allowlist,
                destructive=destructive,
                timeout_seconds=timeout,
                permission="workspace-boundary",
                command=argv,
                cwd=actual_cwd,
                approval=approval_value,
            )

        if source_kind == "repository-discovered":
            _, repository_error = _repository_source_path(provenance_value, workspace)
            if repository_error and "escapes" in repository_error:
                return CommandTrustDecision(
                    state="denied",
                    reason=repository_error,
                    provenance=provenance_value,
                    sandbox=sandbox,
                    network=network,
                    env_allowlist=env_allowlist,
                    destructive=destructive,
                    timeout_seconds=timeout,
                    permission="workspace-boundary",
                    command=argv,
                    cwd=actual_cwd,
                    approval=approval_value,
                )
        else:
            repository_error = None

        if _looks_networked(argv) and network == "deny":
            return CommandTrustDecision(
                state="denied",
                reason="network-capable verification command conflicts with the deny network policy",
                provenance=provenance_value,
                sandbox=sandbox,
                network=network,
                env_allowlist=env_allowlist,
                destructive=destructive,
                timeout_seconds=timeout,
                permission="network-policy",
                command=argv,
                cwd=actual_cwd,
                approval=approval_value,
            )

        if requested_state == "denied":
            state, reason = "denied", str(trust_value.get("reason") or "command was denied by policy")
        elif not requested_state:
            state, reason = "approval_required", "missing command trust metadata requires explicit approval"
        elif requested_state not in TRUST_STATES:
            state, reason = "approval_required", f"unknown command trust state: {requested_state}"
        elif requested_state == "approval_required":
            state, reason = "approval_required", str(trust_value.get("reason") or "explicit approval is required")
        elif source_kind == "unknown" or source_kind not in PROVENANCE_KINDS:
            state, reason = "approval_required", f"unknown command provenance: {source_kind}"
        elif source_kind == "repository-discovered":
            if repository_error:
                state, reason = "approval_required", repository_error
            else:
                state, reason = "trusted", str(trust_value.get("reason") or "repository-discovered command")
        elif source_kind == "pack-signed":
            if _trusted_pack_registry(provenance_value):
                state, reason = "trusted", str(trust_value.get("reason") or "trusted registry Pack command")
            else:
                state, reason = "approval_required", "pack-signed command lacks a trusted registry identity"
        elif source_kind == "deployment-approved":
            state, reason = "trusted", str(trust_value.get("reason") or "deployment-approved command")
        elif source_kind == "user-approved":
            if explicit_approval is not None:
                state, reason = "trusted", "explicit approval evidence authorizes the command"
            else:
                detail = f": {approval_error}" if approval_error else ""
                state, reason = "approval_required", f"self-asserted user-approved provenance is not approval evidence{detail}"
        elif explicit_approval is not None:
            state, reason = "trusted", f"explicit approval evidence authorizes the {source_kind} command"
        else:
            detail = f": {approval_error}" if approval_error else ""
            state, reason = "approval_required", f"{source_kind} commands require explicit approval{detail}"

        # ``approved=True`` is retained as an input compatibility seam only.
        # A bare boolean has no actor, scope, timestamp, or command binding and
        # therefore never upgrades trust or just-in-time permissions.
        _ = approved
        required_permissions: list[str] = []
        if state != "denied" and network == "allow" and not (explicit_approval and explicit_approval.covers("network")):
            required_permissions.append("network")
        if state != "denied" and destructive and not (explicit_approval and explicit_approval.covers("destructive")):
            required_permissions.append("destructive")
        permission: str | None = None
        if required_permissions:
            state = "approval_required"
            permission = "+".join(required_permissions)
            reason = f"just-in-time permission required: {', '.join(required_permissions)}"
        return CommandTrustDecision(
            state=state,
            reason=reason,
            provenance=provenance_value,
            sandbox=sandbox,
            network=network,
            env_allowlist=env_allowlist,
            destructive=destructive,
            timeout_seconds=timeout,
            permission=permission,
            command=argv,
            cwd=actual_cwd,
            approval=explicit_approval.to_dict() if explicit_approval is not None else approval_value,
            required_permissions=tuple(required_permissions),
        )

    decide = evaluate


CommandTrust = CommandTrustEvaluator


def evaluate_command_trust(command: Any, **kwargs: Any) -> CommandTrustDecision:
    """Functional facade used by callers that do not need an evaluator object."""

    return CommandTrustEvaluator().evaluate(command, **kwargs)


def allowed_environment(allowlist: Sequence[str], *, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment restricted to the declared allowlist."""

    source = dict(base or os.environ)
    return {name: source[name] for name in dict.fromkeys(map(str, allowlist)) if name in source}


__all__ = [
    "APPROVAL_SCOPES",
    "ApprovalEvidence",
    "CommandTrust",
    "CommandTrustDecision",
    "CommandTrustError",
    "CommandTrustEvaluator",
    "NETWORK_POLICIES",
    "PROVENANCE_KINDS",
    "SANDBOXES",
    "TRUST_STATES",
    "TrustDecision",
    "allowed_environment",
    "command_digest",
    "evaluate_command_trust",
    "normalize_command",
]
