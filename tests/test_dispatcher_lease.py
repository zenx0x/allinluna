from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "allinluna" / "skills" / "allinluna-run" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from dispatcher_lease import (  # noqa: E402
    DispatcherLeaseConflict,
    DispatcherLeaseError,
    dispatcher_session,
    load_lease,
    make_owner_identity,
)


class DispatcherLeaseTests(unittest.TestCase):
    def make_run(self, root: Path) -> Path:
        run = root / "run"
        run.mkdir()
        (run / "run-state.json").write_text(json.dumps({"run_id": "lease-test"}), encoding="utf-8")
        (run / "events.jsonl").write_text("", encoding="utf-8")
        return run

    def test_restart_reuses_one_epoch_and_owner_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.make_run(Path(temporary))
            owner = make_owner_identity(
                role="primary-coordinator",
                run_id="lease-test",
                coordinator_id="primary",
                thread_id="coordinator-thread",
                host_id="host-1",
                repository_identity={"root": "repo", "head": "base"},
                worktree_identity={"kind": "projectless", "name": "control"},
            )
            with dispatcher_session(run, owner, purpose="first process") as first:
                self.assertEqual(first.decision, "acquired")
                self.assertEqual(first.epoch, 1)
            with dispatcher_session(run, owner, purpose="restarted process") as resumed:
                self.assertEqual(resumed.decision, "reuse")
                self.assertEqual(resumed.epoch, 1)
                self.assertEqual(resumed.owner_identity, owner)
            lease = load_lease(run)
            self.assertEqual(lease["epoch"], 1)
            events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["evidence"]["owner_identity"], owner)

    def test_second_owner_requires_explicit_sponsor_failure_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.make_run(Path(temporary))
            old = make_owner_identity(
                role="primary-coordinator",
                run_id="lease-test",
                coordinator_id="primary",
                thread_id="old-thread",
                host_id="host-old",
            )
            new = make_owner_identity(
                role="primary-coordinator",
                run_id="lease-test",
                coordinator_id="primary",
                thread_id="new-thread",
                host_id="host-new",
            )
            with dispatcher_session(run, old, purpose="old owner") as acquired:
                old_epoch = acquired.epoch
            with self.assertRaises(DispatcherLeaseConflict):
                with dispatcher_session(run, new, purpose="unapproved replacement"):
                    pass
            bad_event = {
                "type": "dispatcher-failure-recovery",
                "actor": "coordinator",
                "event_id": "failure-1",
                "reason": "host disappeared",
                "failed_owner_identity": old,
            }
            with self.assertRaises(DispatcherLeaseError):
                with dispatcher_session(run, new, purpose="bad recovery", recovery_event=bad_event):
                    pass
            recovery = {
                "type": "dispatcher-failure-recovery",
                "actor": "sponsor",
                "event_id": "failure-1",
                "reason": "host disappeared and Sponsor authorized recovery",
                "failed_owner_identity": old,
            }
            with dispatcher_session(run, new, purpose="approved recovery", recovery_event=recovery) as taken:
                self.assertEqual(taken.decision, "takeover")
                self.assertEqual(taken.epoch, old_epoch + 1)
            events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            takeover = events[-1]
            self.assertEqual(takeover["evidence"]["transition"]["event_id"], "failure-1")
            self.assertEqual(takeover["evidence"]["previous_owner_identity"], old)
            self.assertEqual(takeover["evidence"]["owner_identity"], new)

    def test_handoff_is_narrowly_limited_to_bootstrap_sponsor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self.make_run(Path(temporary))
            sponsor = make_owner_identity(
                role="sponsor-bootstrap",
                run_id="lease-test",
                thread_id="sponsor:lease-test",
                worktree_identity={"kind": "projectless", "name": "bootstrap"},
            )
            coordinator = make_owner_identity(
                role="primary-coordinator",
                run_id="lease-test",
                coordinator_id="primary",
                thread_id="coordinator-thread",
                worktree_identity={"kind": "projectless", "name": "primary-coordinator"},
            )
            with dispatcher_session(run, sponsor, purpose="bootstrap") as first:
                first_epoch = first.epoch
            handoff = {
                "type": "dispatcher-handoff",
                "actor": "sponsor",
                "from_owner_identity": sponsor,
                "to_owner_identity": coordinator,
                "reason": "real Coordinator receipt arrived",
            }
            with dispatcher_session(
                run,
                coordinator,
                purpose="handoff",
                handoff_event=handoff,
            ) as second:
                self.assertEqual(second.decision, "handoff")
                self.assertEqual(second.epoch, first_epoch + 1)


if __name__ == "__main__":
    unittest.main()
