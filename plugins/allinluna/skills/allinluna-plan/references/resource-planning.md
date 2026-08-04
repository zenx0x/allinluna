# Resource planning

Keep these controls separate:

| Control | Governs |
| --- | --- |
| Delivery mode | `quick`, `standard`, or risk-reserved `full` topology |
| Model | Capability family and runtime availability |
| Reasoning | Deliberation depth for that role |
| Delegation | Current task, bounded subagent, or user-owned top-level task |
| Concurrency | Simultaneous ready Owners within host and file limits |
| Budget | Token, credit, time, money, or user-defined ceiling |

The delivery-mode definitions are canonical in
[`allinluna-run/references/user-flow.md`](../../allinluna-run/references/user-flow.md).
The one resource card records the selected mode and any retained profile such
as `fast`, `ultra-fast`, or `all-luna`; Run must not ask for the same choice
again.

## Delegation and lanes

The user conversation is the Sponsor. Run creates a separate Coordinator, and
the Coordinator dispatches substantive Owners with exclusive writable paths.
Use dependency-ready, conflict-free lanes concurrently up to the requested
target. Do not manufacture micro-tasks to fill a number. Within an Owner,
bounded subagents inherit the Owner's paths, model lock, reasoning ceiling,
budget, tests, and reporting contract.

`full` is not a default planning mode. Use it only when high risk, large
cross-contract scope, or scientific authority requires a stronger evidence
boundary; the lean runtime does not materialize separate CounterPilot or
Acceptance lanes, and mechanical Integration follows the actual shared-result
boundary.

## Profiles and evidence

`balanced` is the default resource profile when no profile is requested. Keep
`fast`, `ultra-fast`, `all-luna`, and other profiles available; profiles change
allocation and speed, never scope. Resolve logical roles against the actual
delegation-specific host catalog. Record `requested`, `resolved`, and `actual`
model/reasoning/delegation separately; missing telemetry is `unavailable`.

At desired concurrency 16 or above, ask once whether a high-quality model should
review dependencies, ownership, conflicts, and shard boundaries. Record
accepted/declined and do not repeat the question during Run.

## Risk and stop boundary

Use strongest reasoning for irreversible authority decisions and efficient
models for clear bounded work. State a measurable stop boundary. Do not
authorize destructive, credentialed, live, or publication actions through a
plan alone.
