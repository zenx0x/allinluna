---
name: allinluna
description: Compile and execute a goal, existing plan, active run, or Research Routes packet through the vNext Coordinator, Task Lanes, WorkUnits, and Workflow Packs.
---

# All in Luna

All in Luna is one public entry point. Accept one of these inputs:

- an idea or one-sentence goal;
- an existing plan;
- an active run or legacy run snapshot;
- a Research Routes packet.

Compile the input to a typed `RunIntent`, select a registered Workflow Pack,
and compile its `TaskGraph` containing contracts, dependencies, ownership,
done-when conditions, WorkGraph templates, and resource defaults. Then call the
vNext runtime/CLI to persist the graph, release ready Lanes, ingest real host
receipts, and continue until the root result is complete or a concrete blocker
is returned.

## Runtime shape

```text
Conversation
  -> Global Coordinator
       -> independent Task Lane(s)
            -> bounded recursive WorkUnits
                 -> tools / skills / plugins / MCP
```

Keep raw tool output in the Artifact Store. Pass typed contracts, artifact
references, context slices, receipts, and handoffs upward. A child WorkUnit
must narrow its parent scope, authority, ownership, and resource envelope; a
cross-Lane request becomes a promotion request.

## Input and Pack routing

Use `SinglePublicSkillAPI.compile()` or `allinluna start`:

- plain goal -> `delivery` by default;
- existing plan -> read-only legacy import, then `delivery`;
- active run -> read-only run-state import and recovery-oriented compilation;
- Research Routes packet -> `research-routes-bridge`, preserving Claims,
  Evidence, unknowns, contradictions, failure regimes, HumanDecision, and
  experiment-authorization boundaries.

Use `gsd` when the user explicitly requests its workflow. Its executable Pack
provides clarify, specify, decompose, implement, verify, and integrate,
dynamic lane-local expansion, bounded WorkUnits, contract handoffs, and local
failure recovery. Do not add GSD phases to Core.

## Resources and permissions

Model and reasoning choices come from the Run resource envelope and may be
overridden by a narrower Task or WorkUnit resource envelope. They are not hard
locked: callers may select Luna at medium/high/xhigh/max, Codex Spark, or another
host-supported model. Preserve requested, resolved, and actual values
separately. If the host cannot provide an actual model receipt, record
`actual: unresolved`; never claim a fallback or fabricate a receipt. A narrower
scope may change compute resources but may not expand permissions or ownership.
The canonical host receipt is a Codex Desktop envelope assembled from the
Desktop host's exported App Server `thread/start`, optional `model/rerouted`,
and matching `turn/started` / `turn/completed` events. A separately launched
CLI App Server is a different host session and is not acceptance evidence. The
outer envelope identifies `codex_app__create_thread`; the nested events provide
resource evidence. It carries
`resource_receipt.requested`, `resolved`,
`actual`, `actual_state`, `evidence_source`, and `observed_at`. Mark it
`resolved` only when the host supplies matching model and reasoning values,
an explicit evidence source, and a valid observation timestamp. The Adapter
must compare the request against the persisted dispatch action and actual
against the final resolved route. A requested/resolved difference is valid
only with a coherent reroute chain; a receipt must never establish its own
verification baseline.

Request permissions just in time at the action boundary. Read-only compilation
does not request credentials, publication, deployment, push, destructive work,
or live external mutation. When such an action is reached, return a
`PermissionIntent` with `ask`, `allowed`, or `denied`; do not front-load a
questionnaire and do not silently perform the action.

## CLI and recovery

```text
allinluna start --goal "..."
allinluna status RUN_ID
allinluna next-actions RUN_ID
allinluna ingest-receipt RUN_ID RECEIPT.json
allinluna receipt-from-app-server --requested REQUEST.json --thread-start START.json --events EVENTS.json --action ACTION.json
allinluna pause RUN_ID
allinluna resume RUN_ID
allinluna retry RUN_ID --task TASK_ID
allinluna cancel RUN_ID --task TASK_ID
allinluna reconcile RUN_ID
```

The `thread-start` input must carry Desktop provenance (`source=codex_app`,
`actual_tool=codex_app__create_thread`, `event_origin=codex_desktop`); do not
normalize a bare response from an independently launched CLI App Server.

The runtime CLI exposes `start`, `status`, `next-actions`, `ingest-receipt`,
`pause`, `resume`, `retry`, `cancel`, `set-policy`, and `reconcile`. Use the
public compatibility APIs for legacy plan/run import; they return host-neutral
actions for the host adapter and never treat a pending client id as an active
receipt.
Recovery keeps immutable artifacts and re-computes ready actions after leases,
receipts, or context snapshots are reconciled.

The registry/launcher is only an internal discoverability mechanism. Ordinary
users enter through this contextual Skill and their goal or journey; experts
may inspect the Pack matrix and manifest contracts.
