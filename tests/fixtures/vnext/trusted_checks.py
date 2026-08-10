"""Trusted command fixtures for verification-contract tests.

These helpers materialize the repository source claimed by command provenance.
They keep tests explicit about trust instead of relying on the pre-RC2
fail-open behavior for commands with missing metadata.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any


def trusted_command_spec(
    workspace: str | Path,
    *,
    identifier: str,
    command: str | Sequence[str],
    satisfies: Sequence[str] = (),
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Build a repository-discovered command spec with a real source file."""

    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = root / "pyproject.toml"
    if not source.exists():
        source.write_text(
            "[project]\nname = 'allinluna-verification-fixture'\n",
            encoding="utf-8",
        )
    return {
        "id": identifier,
        "kind": "command",
        "command": list(command) if not isinstance(command, str) else command,
        "satisfies": list(satisfies),
        "timeout_seconds": timeout_seconds,
        "provenance": {
            "source_kind": "repository-discovered",
            "source_ref": str(source),
        },
        "trust": {"state": "trusted"},
        "execution": {
            "sandbox": "worktree",
            "network": "deny",
            "workspace": str(root),
            "timeout_seconds": timeout_seconds,
        },
    }


__all__ = ["trusted_command_spec"]
