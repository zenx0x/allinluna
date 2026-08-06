"""Trust and execution policy for verification commands.

Verification commands are data crossing an execution boundary.  A typed
``VerificationSpec`` is therefore not sufficient evidence that a command may
run: its provenance and execution policy must also be evaluated.  This module
keeps that decision deterministic and independent from the subprocess runner.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


def _looks_destructive(command: Sequence[str]) -> bool:
    text = " ".join(command).lower()
    patterns = (
        r"(^|\s)(rm|rmdir|del|erase|remove-item)(\s|$)",
        r"(^|\s)git\s+(reset|clean|push|checkout\s+--)(\s|$)",
        r"(^|\s)(format|mkfs|shutdown)(\s|$)",
        r"(^|\s)(curl|wget|invoke-webrequest|invoke-restmethod)(\s|$)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


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

    @property
    def executable(self) -> bool:
        return self.state == "trusted" and self.permission is None

    @property
    def approval_required(self) -> bool:
        return self.state == "approval_required" or self.permission is not None

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
        execution: Mapping[str, Any] | None = None,
        cwd: str | Path | None = None,
        workspace: str | Path | None = None,
        approved: bool = False,
    ) -> CommandTrustDecision:
        argv = normalize_command(command)
        provenance_value = _provenance(provenance)
        source_kind = str(provenance_value.get("source_kind") or "user-approved")
        execution_value = _mapping(execution)
        trust_value = _mapping(trust) if not isinstance(trust, str) else {"state": trust}
        requested_state = str(trust_value.get("state") or "").strip()
        sandbox = str(execution_value.get("sandbox") or ("worktree" if workspace else "none"))
        if sandbox not in SANDBOXES:
            return CommandTrustDecision(
                "denied", f"unsupported sandbox: {sandbox}", provenance_value, "none", command=argv, cwd=str(cwd) if cwd else None
            )
        network = str(execution_value.get("network") or "deny")
        if network not in NETWORK_POLICIES:
            return CommandTrustDecision(
                "denied", f"unsupported network policy: {network}", provenance_value, sandbox, command=argv, cwd=str(cwd) if cwd else None
            )
        env_allowlist = _strings(execution_value.get("env_allowlist"))
        destructive = bool(execution_value.get("destructive", False)) or _looks_destructive(argv)
        timeout = execution_value.get("timeout_seconds")
        if timeout is not None:
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                return CommandTrustDecision("denied", "invalid verification timeout", provenance_value, sandbox, command=argv)
            if not 0 < timeout <= 900:
                return CommandTrustDecision("denied", "verification timeout is outside the allowed range", provenance_value, sandbox, command=argv)

        actual_cwd = str(cwd or execution_value.get("cwd") or "") or None
        if actual_cwd and workspace and not _inside(actual_cwd, workspace):
            return CommandTrustDecision(
                "denied",
                "verification command cwd is outside the owned workspace",
                provenance_value,
                sandbox,
                network,
                env_allowlist,
                destructive,
                timeout,
                "workspace-boundary",
                argv,
                actual_cwd,
            )

        if source_kind in {"model-proposed", "legacy-imported", "external-packet"}:
            if approved and source_kind == "model-proposed":
                state, reason = "trusted", "explicit user approval for model-proposed command"
            else:
                state, reason = "approval_required", f"{source_kind} commands require explicit approval"
        elif source_kind not in PROVENANCE_KINDS:
            state, reason = "approval_required", f"unknown command provenance: {source_kind}"
        elif requested_state == "denied":
            state, reason = "denied", str(trust_value.get("reason") or "command was denied by policy")
        elif source_kind in {"repository-discovered", "pack-signed", "user-approved", "deployment-approved"}:
            state, reason = "trusted", str(trust_value.get("reason") or f"{source_kind} command")
        else:  # pragma: no cover - the provenance set above is exhaustive.
            state, reason = "approval_required", "command approval is required"

        permission: str | None = None
        if network == "allow" and not approved:
            state, reason, permission = "approval_required", "network access requires just-in-time permission", "network"
        if destructive and not approved:
            state, reason, permission = "approval_required", "destructive verification requires just-in-time permission", "destructive"
        if requested_state == "approval_required" and not approved:
            state, reason = "approval_required", str(trust_value.get("reason") or "explicit approval is required")
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
    "evaluate_command_trust",
    "normalize_command",
]
