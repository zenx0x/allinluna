# Resource planning

## Separate the controls

Do not collapse these into a single “quality” setting:

| Control | Governs |
| --- | --- |
| Model | Capability family and runtime availability |
| Reasoning | Deliberation depth for that role |
| Delegation | Current task, subagent, or user-owned top-level task |
| Concurrency | Simultaneous active owners within host and file limits |
| Budget | Token, credit, time, money, or user-defined ceiling |

Unknown telemetry is `unavailable`; do not estimate actual cost from model names alone.

## Hierarchical delegation default

The user conversation is the Sponsor and creates a separate primary Coordinator plus a risk-triggered CounterPilot. The Coordinator prefers user-visible top-level Codex tasks for substantive owner lanes. At 16+ desired concurrency it may add one level of child Coordinators for disjoint shards. Do not create a task for every micro-fix. Within an owner, bounded subagents may work under the same paths, model policy, and verification contract.

## Resource classes

- `authority`: irreversible scientific, security, migration, or canonical decisions.
- `architecture`: cross-cutting contracts and dependency design.
- `implementation-complex`: bounded but difficult engineering.
- `implementation-clear`: well-specified coding with strong tests.
- `mechanical`: scanning, formatting, generated updates, or repetitive fixtures.
- `integration`: shared-file reconciliation and product-chain verification.
- `acceptance`: independent journey and boundary validation.

Assign logical tiers such as `frontier`, `standard`, `fast`, or a user-requested model family. Resolve them against the runtime catalog later.

## Profile intent

- `premium`: prioritize decision quality. Use frontier reasoning for authority, architecture, and acceptance; do not use expensive independent duplicates for trivial work.
- `balanced`: frontier/standard planning, efficient engineering, bounded concurrency.
- `economy`: Luna or fast family for clear work, desired concurrency 4, decomposition before escalation, explicit approval when escalation crosses policy.
- `speed`: desired concurrency 12 for independent owners; do not trade away tests or ownership.
- `fast`: desired concurrency 24 with hierarchical coordination when useful.
- `ultra-fast`: desired concurrency 48 with a one-time high-quality decomposition choice.
- `all-luna`: hard Luna family lock, high reasoning, moderate concurrency.
- `mad-luna`: hard Luna family lock, maximum supported reasoning, maximum safe concurrency, and independent Luna verification for high-risk milestones.
- `custom`: every important field must be explicit; missing fields inherit from balanced only when the user permits inheritance.

Profiles and velocity modifiers compose. `all-luna + fast` means Luna-only roles plus fast scheduling; it does not permit mixed-model fallback. Store the base lock and modifier separately.

## Hard locks and fallbacks

A hard model lock means every actual assignment must match the lock. Legal policies are:

- `pause`: stop the affected lane if unavailable;
- `ask`: request a user choice;
- `fallback-list`: use only an explicit ordered list.

“Use the best available model” is not a valid fallback under a hard lock.

## Concurrency

When the user has not chosen resource settings, offer one optional preset choice and otherwise default to `balanced` with desired concurrency 8. Accept explicit values from 1–64. The choice belongs in planning, not as a repeated Run-stage authorization.

The planner automatically extracts independent owner lanes using the dependency DAG and exclusive writable ownership. All ready, conflict-free lanes may run together up to the desired target. Concurrency is therefore a scheduling target, not a request for artificial task fragmentation.

The effective concurrency is the minimum of:

- the user's requested concurrency, or the profile default when none was requested;
- host limit;
- number of dependency-ready tasks;
- number of conflict-free writable ownership sets;
- user budget cap.

More agents do not help tightly coupled work. Plan those steps sequentially or give shared files to integration.

Profile concurrency values are defaults rather than ceilings. At 16+, ask once whether a high-quality model should review dependencies, conflicts, ownership, and child-Coordinator shard boundaries. Record accepted plus its model, or declined. Do not encode current readiness as desired concurrency. Runtime capacity is capped independently by the host, ready work, ownership, machine, and budget.
