---
artifact: acceptance_matrix
version: 1
protocol: contract-freeze/v1
task: run-allinluna-v2-rc2-product-surface:task:T0
status: frozen
---

# RC2 acceptance matrix

This matrix is the frozen acceptance map for downstream lanes. `Owner` is the
lane that must produce the evidence; T0 defines the assertion but does not
claim a downstream PASS. `PASS` below means the input was observed and loaded
from the Runtime DB during T0 bootstrap, not that the full RC2 candidate is
qualified.

| ID | Assertion | Owner / evidence | State at T0 freeze |
| --- | --- | --- | --- |
| BOOT-01 | The supplied lane identities resolve to the exact run, Task, Contract, WorkGraph, and lane Context in the named Store. | T0; bootstrap output, task/contract/work inspect, ContextKernel snapshot | PASS |
| BOOT-02 | Persisted dispatch envelope validates against the Task and its task-envelope digest. | Coordinator/T0; dispatch outbox and `LaneBootstrapEnvelope.from_store` | PASS for persisted DB envelope |
| BOOT-03 | Delegation digest and persisted dispatch digest are reconciled without rewriting either Store object. | Coordinator; discrepancy evidence and follow-up | OBSERVED DIVERGENCE: supplied `ec3eb3…34e8c`, persisted `e4ac6d…96f9c` |
| CONTRACT-01 | All four T0 exports exist, are version 1, and remain inside `docs/architecture/v2-rc2/**`. | T0; artifact refs plus changed-path list | T0 deliverable |
| CONTRACT-02 | The public Skill remains the only ordinary user entry and internal registry remains discoverability-only. | T1/T6; source inspection and user-journey eval | DOWNSTREAM |
| CONTRACT-03 | Plain goal, plan import, active-run recovery, and Research Routes bridge preserve typed state and epistemic boundaries. | T1/T3/T6; executable journey evidence | DOWNSTREAM |
| CONTRACT-04 | Project identity is resolved before top-level create; projectless is explicit; Task ID is never used as project ID. | T3/T4/T6; host/action traces | DOWNSTREAM |
| CONTRACT-05 | Host actions use the exact tool/capability; no-host and missing-capability errors remain distinct; receipts carry observed tool/capability/hash. | T3/T4/T6; real or conformance host receipts | DOWNSTREAM |
| CONTRACT-06 | Requested, resolved, and actual resource layers remain distinct; missing actual telemetry stays unresolved. | T2/T3/T4; unit/integration/host evidence | DOWNSTREAM |
| CONTRACT-07 | Local scheduler and recursive delegation cannot mutate global Tasks or GlobalScheduler, and children narrow authority/ownership/resources. | T2/T3/T6; scheduler and fake-host tests | DOWNSTREAM |
| CONTRACT-08 | Context snapshots, artifacts, receipts, and handoffs are scope-labelled, immutable or append-only as appropriate, and traceable by refs. | T3/T4/T6; Store and handoff checks | DOWNSTREAM |
| RELEASE-01 | RC2 qualification does not create a stable tag, stable release, force-push, rebase, or unapproved publication. | T5/T7/T8; Git/release evidence | DOWNSTREAM |
| RELEASE-02 | Installed CLI/distributions and co-installation retain the frozen public/runtime interfaces. | T4/T5/T8; isolated install and artifact evidence | DOWNSTREAM |
| ACCEPT-01 | A completed lane handoff is schema-valid, contract-versioned, artifact-referenced, and names no unsupported PASS. | Each lane; `handoff.schema.json` plus Artifact Store links | DOWNSTREAM |

## T0 completion evidence

T0 is complete when the four version-1 artifacts are present, their content is
linked as immutable Store artifacts, the T0 WorkUnit handoff names the exact
changed paths, and the synthesized lane handoff is valid under
`lane-handoff/v1`. Downstream rows remain explicit until their owning lanes
provide evidence.

## Failure and unknown semantics

- `fail` means the assertion was exercised and contradicted by evidence.
- `unknown` means the required evidence was not available; it is not a PASS.
- `blocked` identifies an owner/action boundary that must be resolved before
  qualification; it does not authorize a scope expansion.
- A timed-out completeness check remains unverified.
- A pending host receipt remains pending; it never becomes completion by age or
  by the presence of an action record.
