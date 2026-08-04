# Orchestration contract

## Control-plane roles

The user's main conversation is the **Sponsor**. It owns product intent, explicit authority, and user-facing status, but is not the implementation coordinator. The Sponsor creates and monitors two separate user-visible tasks:

- **Primary Coordinator**: owns the global DAG, resource policy, dependency release, owner dispatch, defect routing, and completion standard.
- **CounterPilot**: independently challenges scope, assumptions, dependency safety, and recovery claims with evidence and falsifiable probes.

Thread IDs for Sponsor, Coordinator, CounterPilot, child Coordinators, and implementation owners must be distinct.

## Hierarchical coordination

For high concurrency, the primary Coordinator may create child Coordinator tasks for disjoint shards. Auto mode enables this when desired concurrency is at least 16 and there are more implementation owners than the configured shard size. Each child:

- receives a fixed task-ID set and slot limit;
- dispatches and monitors only that set;
- cannot change global scope, resource locks, or completion criteria;
- cannot share exclusive writable ownership with another shard;
- reports blockers and evidence to the primary Coordinator.

The hierarchy is one level deep. Do not create coordinators for micro-fixes or to inflate parallelism.

## Continuous execution

```text
Sponsor monitors control plane
  -> primary Coordinator releases shards and owners
  -> child Coordinators monitor assigned owners
  -> owners implement and verify
  -> defects return to original owners
  -> risk-proportional integration/acceptance
  -> completion standard
```

Task creation, a progress report, one commit, or one successful checkpoint is not completion. If one lane blocks, continue unrelated lanes. Pause globally only for a required product choice, missing authority/credential, destructive action, live external mutation, or a dependency blocking every remaining lane.

## Ownership and completion

Every owner receives a self-contained brief and exclusive write set. Shared contracts belong to one owner or an integration task. Integration may repair mechanical shared-file issues; scientific or owner-semantic defects return to the original owner. Completion remains the full plan standard, never the first vertical slice.
