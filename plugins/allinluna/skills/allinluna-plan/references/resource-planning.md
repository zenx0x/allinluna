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

## Two-level delegation default

At the root orchestration level, prefer user-visible top-level Codex tasks for independent substantive owner lanes. Do not create a top-level task for every micro-fix. Within an assigned top-level owner, allow bounded internal subagents for decomposable work under the same paths, model policy, and verification contract. Root-level subagents are a user-approved fallback, not a substitute for planned top-level owners.

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
- `economy`: Luna or fast family for clear work, concurrency 1–2, decomposition before escalation, explicit approval when escalation crosses policy.
- `speed`: increase concurrency only for independent owners; do not trade away tests or ownership.
- `all-luna`: hard Luna family lock, high reasoning, moderate concurrency.
- `mad-luna`: hard Luna family lock, maximum supported reasoning, maximum safe concurrency, and independent Luna verification for high-risk milestones.
- `custom`: every important field must be explicit; missing fields inherit from balanced only when the user permits inheritance.

Profiles and execution modifiers can compose. `all-luna + speed` means Luna-only roles from `all-luna` plus the `speed` concurrency strategy (desired 6, host-capped); it does not permit mixed-model fallback. Store the composition as `profile: "all-luna"` and `modifiers: ["speed"]`.

## Hard locks and fallbacks

A hard model lock means every actual assignment must match the lock. Legal policies are:

- `pause`: stop the affected lane if unavailable;
- `ask`: request a user choice;
- `fallback-list`: use only an explicit ordered list.

“Use the best available model” is not a valid fallback under a hard lock.

## Concurrency

The effective concurrency is the minimum of:

- profile request;
- host limit;
- number of dependency-ready tasks;
- number of conflict-free writable ownership sets;
- user budget cap.

More agents do not help tightly coupled work. Plan those steps sequentially or give shared files to integration.

Do not encode current dependency readiness as desired concurrency. Desired concurrency remains the selected profile target; the runtime effective value is capped independently. Empty or non-Git projects first receive a coordinator Git-bootstrap dependency and then use top-level owner tasks.
