"""Deterministic host and workspace fixtures for future vNext integration."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FakeCodexHost:
    """A receipt-producing host double; it does not implement scheduler logic."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    receipts: dict[str, dict[str, Any]] = field(default_factory=dict)
    supports_wait: bool = True

    def discover(self) -> dict[str, Any]:
        return {
            "host_id": "fake-codex-host",
            "capabilities": {"wait_tasks": self.supports_wait, "create_top_level_task": True},
        }

    def create_top_level_task(self, action: dict[str, Any]) -> dict[str, Any]:
        key = action["idempotency_key"]
        self.calls.append({"method": "create_top_level_task", "action": action})
        if key not in self.receipts:
            self.receipts[key] = {
                "protocol": "host-receipt/v1",
                "receipt_id": f"receipt-{len(self.receipts) + 1}",
                "thread_id": f"thread-{len(self.receipts) + 1}",
                "host_id": "fake-codex-host",
                "idempotency_key": key,
                "actual": True,
            }
        return dict(self.receipts[key])

    def wait_tasks(self, targets: list[dict[str, Any]], cursor: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "wait_tasks", "targets": targets, "cursor": cursor})
        return {"protocol": "host-receipt/v1", "events": [], "cursor": cursor}

    def read_task(self, target: dict[str, Any], cursor: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "read_task", "target": target, "cursor": cursor})
        return {"protocol": "host-receipt/v1", "events": [], "cursor": cursor}

    def send_message(self, target: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": "send_message", "target": target, "envelope": envelope})
        return {"protocol": "host-receipt/v1", "receipt_id": "message-receipt", "actual": True}

    def cancel_task(self, target: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": "cancel_task", "target": target})
        return {"protocol": "host-receipt/v1", "receipt_id": "cancel-receipt", "actual": True}


@dataclass
class FakeSubagentHost:
    """A nested-host double with explicit delayed/lost receipt controls."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    receipts: dict[str, dict[str, Any]] = field(default_factory=dict)
    delayed: set[str] = field(default_factory=set)
    lost: set[str] = field(default_factory=set)

    def spawn(self, envelope: dict[str, Any]) -> dict[str, Any]:
        work_unit_id = envelope["work_unit_id"]
        self.calls.append({"method": "spawn", "envelope": envelope})
        if work_unit_id in self.lost:
            return {"protocol": "host-receipt/v1", "status": "lost", "work_unit_id": work_unit_id}
        if work_unit_id in self.delayed:
            return {"protocol": "host-receipt/v1", "status": "accepted", "work_unit_id": work_unit_id}
        receipt = self.receipts.setdefault(
            work_unit_id,
            {
                "protocol": "host-receipt/v1",
                "status": "active",
                "work_unit_id": work_unit_id,
                "receipt_id": f"subagent-receipt-{len(self.receipts) + 1}",
            },
        )
        return dict(receipt)

    def wait(self, work_unit_ids: list[str]) -> dict[str, Any]:
        self.calls.append({"method": "wait", "work_unit_ids": work_unit_ids})
        return {"protocol": "host-receipt/v1", "work_unit_ids": work_unit_ids, "events": []}


@dataclass(frozen=True)
class GitFixture:
    repository: Path
    worktree: Path
    branch: str
    base_commit: str


def make_git_fixture(root: Path) -> GitFixture:
    """Create a temporary repository and a real linked worktree."""

    repository = root / "repo"
    worktree = root / "lane-worktree"
    repository.mkdir()
    for args in (("init",), ("config", "user.email", "vnext-tests@example.invalid"),
                 ("config", "user.name", "vNext Tests")):
        subprocess.run(["git", *args], cwd=repository, check=True, capture_output=True, text=True)
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "test base"], cwd=repository, check=True, capture_output=True, text=True
    )
    branch = "codex/vnext-test-lane"
    subprocess.run(["git", "branch", branch], cwd=repository, check=True, capture_output=True, text=True)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(
        ["git", "worktree", "add", str(worktree), branch],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return GitFixture(repository, worktree, branch, base_commit)


def cleanup_git_fixture(fixture: GitFixture) -> None:
    """Remove the linked worktree before pytest removes its temporary directory."""

    if fixture.worktree.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(fixture.worktree)],
            cwd=fixture.repository,
            check=True,
            capture_output=True,
            text=True,
        )
