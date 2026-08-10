---
artifact: interface_contracts
version: 1
protocol: contract-freeze/v1
task: run-allinluna-v2-rc2-product-surface:task:T0
status: frozen
---

# RC2 interface contracts

This file freezes the names, direction, and truth boundaries that connect the
product surface to the downstream implementation and acceptance lanes.

## Runtime protocol vocabulary

| Interface | Producer -> consumer | Required truth boundary |
| --- | --- | --- |
| `RunIntent` / TaskGraph | public Skill -> Coordinator | A user outcome compiles to typed Tasks, contracts, dependencies, ownership, done-when conditions, and resource policy. |
| `task-envelope/v1` | Coordinator -> Task Lane | The envelope carries the exact run/task/attempt identities and references; the Lane reopens the same Store. |
| `lane-bootstrap/v1` | Coordinator -> Lane | `LaneBootstrapEnvelope` includes DB, Task, Contract, Context, WorkGraph, workspace, allowed local capabilities, and forbidden global capabilities. |
| `work-handoff/v1` | local WorkUnit -> Lane | Completion contains the local WorkUnit ID, changed paths inside ownership, artifacts, exports, checks, and no cross-Lane write. |
| `lane-handoff/v1` | Lane -> Coordinator | Completion is schema-valid, contract-versioned, artifact-referenced, and does not self-sign evidence it did not collect. |
| `HostAction` / receipt | runtime -> HostAdapter -> runtime | The exact requested tool is invoked; observed tool/capability/hash are explicit; pending/unresolved is not completion. |
| Context snapshot | Store -> Lane / WorkUnit | Context is a replaceable, scope-labelled snapshot with source digest and exclusions; raw child transcripts are excluded by policy. |
| Artifact Store | producer -> downstream consumer | Payloads are immutable, content-addressed, and linked to the owning task/snapshot/handoff. |

## T0 exports

The T0 contract exports these version-1 artifacts. Each export is a durable
artifact reference in the lane handoff, not merely a filename claim.

| Export name | Artifact file | Consumers | Meaning |
| --- | --- | --- | --- |
| `product_contract` | `product-contract.md` | T1-T8 | Product invariants, user journeys, release posture, and non-goals. |
| `path_ownership` | `path-ownership.md` | T1-T8 | Exclusive write sets, overlap precedence, protected paths, and handoff path rules. |
| `interface_contracts` | `interface-contracts.md` | T1-T8 | Protocol vocabulary, direction, required fields, and evidence truth boundaries. |
| `acceptance_matrix` | `acceptance-matrix.md` | T3-T8 | Executable acceptance rows, owner mapping, evidence expectations, and unresolved states. |

Downstream Tasks have their own persisted contracts and do not gain source
ownership from these exports. The export-to-consumer relationship is an
integration handoff reference; it does not mutate the Global Task Graph or
retroactively add imports to a persisted contract.

## Lane and recursive authority

- A Task Lane may use `LocalScheduler` for its own WorkUnits only.
- A local WorkUnit may recursively delegate only inside the parent Lane and
  only with narrowed scope, authority, ownership, and resources.
- `create-top-level-task`, `create-global-task`, `modify-global-task`,
  `global-scheduler`, and `global-coordinator` are forbidden capabilities for
  this Lane.
- A need to alter another lane's contract, ownership, or dependency is a
  coordinator promotion request with an explicit reason and evidence refs.

## Evidence and completion semantics

The following are independent values and must not be collapsed:

```text
requested route -> resolved route -> actual host observation
declared export -> produced artifact -> verified receipt/evidence
current state -> historical event -> completion decision
```

`actual: null` with `actual_state: unresolved` is honest evidence when a host
does not expose telemetry. A pending action or a self-authored check is not a
verified completion receipt. Research claims and evidence remain separate from
implementation results and canonical product state.
