# Orchestration contract

The ordinary execution chain is:

```text
Sponsor -> one resource card -> separate Coordinator -> dependency waves -> result
```

## Control-plane roles

The user's conversation is the Sponsor. It owns product intent, human choices,
and explicit authority. The Sponsor is not an implementation Coordinator.

The separate Coordinator owns the global dependency graph, resource resolution,
ready-wave release, Owner dispatch, defect routing, and completion evidence. Its
thread identity is distinct from the Sponsor and from every Owner.

The lean runtime does not materialize CounterPilot or Acceptance roles. Their
legacy plan fields remain readable for compatibility and are reported as ignored;
the explicit `full` boundary raises required evidence without adding governance
lanes.

## Delivery modes

- `quick` creates a Coordinator and only the Owner(s) required for bounded work.
  It finishes with focused Owner evidence; Integration and Acceptance are not
  added by default.
- `standard` creates multiple independent Owners when the dependency graph and
  exclusive paths support them. It uses at most one Integration pass when a
  shared contract, actual conflict, or plan requirement needs reconciliation.
- `full` is reserved for high-risk, large cross-contract, or scientific-
  authority work. It may use one mechanical Integration pass and the stronger
  evidence required by that risk.

Modes never reduce the requested completion standard. Do not create child
Coordinators for micro-fixes or to inflate parallelism; one bounded hierarchy
level is enough for genuinely large, disjoint shards.

## Dependency waves

The Coordinator dispatches all ready, conflict-free Owners up to the selected
resource target, then releases the next wave as dependencies complete. Each
Owner has an exclusive write set, a self-contained brief, a fixed base, and
focused checks. A blocked lane does not stop unrelated ready work.

Integration may reconcile mechanical shared-file issues only within its owned
scope. Product, scientific, authority, and Owner-specific behavioral defects
return to the original Owner. Required evidence remains read-only at the
verification boundary and never silently repairs implementation.

Do not multiply governance into promotion, registry revision, meta-review, and
repeated acceptance unless the risk contract explicitly requires them. All in
Luna does not default to multi-layer governance or frequent user interruption.
