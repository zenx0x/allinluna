# Orchestration contract

## Root coordinator responsibilities

The root coordinator owns:

- current baseline and protected-worktree verification;
- dependency release and concurrency limits;
- self-contained task dispatch;
- requested-versus-actual resource records;
- continuous wait/status monitoring;
- defect routing back to original owners;
- one phase integration and one milestone acceptance;
- persistent run state and final handoff.

The coordinator never absorbs substantive product implementation merely to save a dispatch. Tight coupling produces dependency-ordered top-level owners; it does not authorize the root to become an owner. Root implementation occurs only as an honestly recorded runtime fallback after delegation capability or Git bootstrap is genuinely unavailable.

## Continuous execution

After dispatch, continue:

```text
monitor → collect evidence → return defects → re-verify → integrate → accept → release dependents
```

Do not stop because a task was created, a progress report arrived, one commit landed, or one integration passed while downstream planned work remains.

`coordinator_tick.py` is the deterministic next-action source. Every successful dispatch is
recorded immediately; every wait result is reconciled; every completion or defect is followed by
another tick until completion, an authorized stop boundary, or a true global blocker.

If one lane blocks, continue unrelated ready lanes. Pause the whole run only for an architecture-changing user choice, missing irreplaceable authority/credential, destructive action, live external mutation, or a dependency that blocks every remaining lane.

## Ownership boundaries

Every owner receives a full brief and an exclusive write set. Read access may be broader when needed. Shared contracts belong to one owner or to the integration task after upstream commits exist.

Integration may resolve mechanical/shared-file conflicts and adapter/schema mismatches within its scope. Scientific or product-semantic defects return to the semantic owner.

Acceptance is read-only unless the plan explicitly combines roles for a small task. An independent acceptor must not silently repair and then approve its own repair.

## Completion

The run finishes only at the plan's completion standard. “Implemented but not runtime-tested,” “backend only,” “fixture only,” and “works for the original project only” are incomplete when the plan requires the full product journey.
