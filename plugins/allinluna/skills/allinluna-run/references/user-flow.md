# Ordinary user flow

This is the canonical newcomer route. The run skill is intentionally short and
routes here only when the user needs the policy details:

```text
one-sentence request or existing plan
  -> one resource confirmation card
  -> Coordinator
  -> dependency waves
  -> result
```

## 1. Bring one sentence or an existing plan

The user can state the outcome in one sentence, paste the relevant context, or
provide a local path to an existing plan. Intake preserves supplied context and
asks only for information that is genuinely missing. A complete third-party
plan uses `parallel-only`: All in Luna normalizes ownership, dependencies,
resources, recovery, and dispatch without redesigning the plan's direction.

The default remains local and reversible. Goal creation, push, publication,
deployment, credentials, and live external writes are separate authorizations;
none is inferred from a request to execute.

## 2. Confirm one resource card

Show one concise card and ask once before formal execution. It may contain the
delivery mode, velocity/model preferences, concurrency, the separate
Coordinator, and any user-provided skills, plugins, or MCP bindings. Record
`requested`, `resolved`, and `actual` capability values separately; an absent
host receipt is `unavailable`, not a guessed success. Do not reopen this card
for every wave.

The delivery mode controls topology, not the completion standard:

| Mode | Default path | Governance boundary |
| --- | --- | --- |
| `quick` | Coordinator plus only the Owner(s) needed for the bounded work | No Integration or independent Acceptance by default |
| `standard` | Coordinator plus multiple independent Owners when the dependency graph warrants them | One Integration only for a shared contract, actual cross-owner conflict, or an explicit plan requirement |
| `full` | Coordinator, Owners, and the complete risk-required path | Explicit upgrade for high-risk, large cross-contract, or scientific-authority evidence; the lean runtime still does not materialize Acceptance/CounterPilot lanes |

`quick` is the default for a small, clear request. Choose `standard` when
parallel dependency waves materially shorten complete delivery. Reserve `full`
for the risk conditions above; it is not a synonym for “more agents.”

The existing resource vocabulary remains available as an optional modifier:
`fast` and `ultra-fast` change scheduling targets, while `all-luna` keeps the
Luna-family lock. User skills, plugins, and MCP bindings remain in scope when
the user supplies them; capability resolution and permission are still
recorded per binding. See [resource profiles](resource-profiles.md) and
[delegation and model evidence](delegation-and-models.md) for the advanced
contract.

## 3. Coordinator and dependency waves

The separate Coordinator owns the dependency graph, releases ready conflict-free
work in waves, monitors the assigned Owners, and routes implementation defects
back to the owning lane. The Sponsor keeps product direction and human choices;
the Sponsor is not a hidden implementation Coordinator. Do not create micro
tasks merely to fill a concurrency target, and do not add a second governance
layer when the selected mode does not require it.

Each Owner receives an exclusive scope, a self-contained brief, and focused
verification. The Coordinator continues unrelated ready work while one lane is
blocked. Detailed delegation, Git/worktree, and recovery rules are routed from
the [run entry](../SKILL.md) only when needed.

## 4. Return the result

The result reports the completed scope, changed artifacts, checks, commit and
worktree evidence when Git is in scope, and remaining blockers. `standard` may
use one mechanical Integration pass when needed; `full` may add the independent
full risk/evidence path only under its risk boundary. No mode silently
turns a first slice, one commit, or one passing test into completion.

All in Luna does not default to multi-layer governance, frequent interruptions,
or a real canary on every run. A real first-use canary/receipt is an explicit
evidence check when the user or risk boundary calls for it, not a ritual on each
ordinary execution.

## Retained paths and handoffs

- `fast`, `ultra-fast`, and `all-luna` remain available through the resource
  policy; their details are on demand in [resource profiles](resource-profiles.md).
- Research Routes remains a route-neutral research surface. Its terrain map,
  Claims, Evidence, unknowns, contradictions, and reversible probes are not an
  implementation plan or canonical-state promotion. Hand a bounded evidence
  package to All in Luna when product delivery is authorized.
- A user-provided skill, plugin, or MCP binding is preserved as a requested
  capability and may fail closed when unavailable or unauthorized; it is not
  silently replaced by an unrecorded tool.

## On-demand references

| Need | Reference |
| --- | --- |
| control-plane roles and waves | [orchestration contract](orchestration-contract.md) |
| model, delegation, and velocity policy | [resource profiles](resource-profiles.md), [delegation and model evidence](delegation-and-models.md) |
| protected files and commits | [Git and ownership](git-and-ownership.md) |
| Integration/Acceptance boundary | [Integration and Acceptance](integration-and-acceptance.md) |
| restart and failure recovery | [Recovery](recovery.md) |
| real versus fixture first-use evidence | [first-use protocol](../../../../../docs/first-use-protocol.md) |
