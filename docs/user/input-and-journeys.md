# Inputs and user journeys

The public Skill accepts four input shapes. Each is compiled into typed
contracts before execution; none is silently treated as a different kind of
input.

| Input | User intent | Boundary |
| --- | --- | --- |
| Idea or one-sentence goal | Start a new delivery run | The goal becomes `RunIntent`; done-when evidence is still required. |
| Existing plan | Reuse a plan as input | Import is read-only; the source plan is not rewritten. |
| Active run or legacy snapshot | Inspect or recover prior work | Recovery uses Store state and receipts; missing evidence stays unknown. |
| Research Routes packet | Explore a route-neutral question | Claims, Evidence, unknowns, contradictions, failure regimes, HumanDecision, and experiment authorization remain distinct. |

## Journey to execution

```text
user input
  -> public Skill
  -> typed RunIntent / TaskGraph
  -> Coordinator and Task Lanes
  -> local WorkUnits
  -> exact host/tool action
  -> receipt, artifact, and handoff evidence
```

For a repository-backed top-level action, project resolution must happen before
the exact host action is constructed. A `Task ID` is not a project ID. A
projectless run must use an explicit projectless target.

## Research boundary

The Research Routes bridge keeps a route-neutral map separate from
implementation authorization and canonical product state. A route can remain
interesting while its evidence is incomplete, contradictory, or not authorized
for an experiment. The bridge preserves those states instead of promoting them
implicitly.

## Resource and permission boundary

Requested, resolved, and actual resource values are separate observations. If a
host does not expose actual model or reasoning telemetry, `actual` remains
`null` and `actual_state` remains `unresolved`; this is not permission to infer
a fallback. Credentials, publication, deployment, push, destructive work, and
live external mutation are requested only at the action boundary.
