# All in Luna 2.0.0-rc.1

[简体中文](README.md)

All in Luna is a layered execution runtime. It compiles a user outcome into a global Task Graph, releases independent Task Lanes through a Global Coordinator, and lets each Lane recursively schedule bounded WorkUnits until typed contracts, artifacts, receipts, and handoffs support the result.

## User entry

There is one public Skill: `plugins/allinluna/skills/allinluna/SKILL.md`. It accepts:

- an idea or one-sentence goal;
- an existing plan;
- an active run;
- a Research Routes packet.

The Skill compiles `RunIntent` and `TaskContracts`, selects a Workflow Pack, and calls the vNext runtime/CLI. Users do not need to learn internal schemas or scheduler state first.

## Execution model

```text
Conversation → Global Coordinator → Task Lanes → WorkUnits → tools/skills/plugins/MCP
```

The Coordinator owns cross-Lane dependencies, contracts, resource allocation, and root completion. A Lane owns its local WorkGraph, local scheduler, context slice, subagent receipts, synthesis, and handoff. A child WorkUnit can only narrow scope, authority, ownership, and resources; cross-Lane work uses a promotion request.

## Workflow Packs

- `delivery`: a real software-delivery compiler with configurable TaskGraph templates, contract expansion, done-when conditions, handoff, promotion, and resource defaults.
- `gsd`: an executable clarify → specify → decompose → implement → verify → integrate workflow with dynamic expansion, bounded lanes/work units, and failure recovery.
- `research-routes-bridge`: a route-neutral bridge for Claims, Evidence, unknowns, contradictions, failure regimes, HumanDecision, and experiment authorization. It never turns research input into implementation authorization or canonical state.

Packs use Store, Context, Artifact, Host, and Capability only through public Core APIs. The registry loader validates manifests, entrypoints, capabilities, permissions, and version compatibility.

## Resources and permissions

Model and reasoning choices come from the Run resource policy and may be overridden by a narrower Task or WorkUnit policy. This RC allows Luna-class models, normally at medium/high/xhigh reasoning with max reserved for critical work, plus `gpt-5.3-codex-spark` outside Luna. Core does not hardcode a concrete model route. Keep requested, resolved, and actual evidence separate. Requested and resolved describe routing; actual is recorded only from explicit host evidence and is never inferred from either route or task prose.

Host resource-route telemetry is optional adapter diagnostics. If model, reasoning, or reroute telemetry is unavailable, `actual` remains `null` and `actual_state` remains `unresolved`; ordinary execution, handoff, and result completion continue normally. An exact `codex_app__create_thread` action is frozen only after host route resolution supplies a non-empty model; an unresolved route emits a non-executable resolution action. `runtime.db` schema v8 persists requested/resolved/actual resource values, outbox, receipts, and recovery state.

Top-level create targets accept `projectId + environment` only from a project-resolution receipt; a projectless Task uses an explicit `{"type":"projectless"}` target. When project identity is absent, the runtime emits a `codex_app__list_projects` resolve-project action first and never substitutes the Task ID for project identity. External top-level receipts must explicitly provide `actual_tool`, `actual_capability`, and `action_contract_hash`; only a trusted HostAdapter called directly by the runtime may sign those observed fields.

Permissions are requested JIT at the action boundary. Credentials, push, deploy, publish, destructive work, and live external mutation do not happen by default; they proceed only after explicit authorization at the reached action.

## CLI, status, and recovery

```text
allinluna start --goal "..."
allinluna drive RUN_ID
allinluna status RUN_ID
allinluna next-actions RUN_ID
allinluna ingest-receipt RUN_ID RECEIPT.json
allinluna pause RUN_ID
allinluna resume RUN_ID
allinluna retry RUN_ID --task TASK_ID
allinluna cancel RUN_ID --task TASK_ID
allinluna reconcile RUN_ID
allinluna set-policy RUN_ID POLICY.json
allinluna lane start RUN_ID TASK_ID
allinluna lane status RUN_ID TASK_ID
allinluna lane tick RUN_ID TASK_ID
allinluna lane drive RUN_ID TASK_ID
allinluna lane ingest-receipt RUN_ID TASK_ID RECEIPT.json
allinluna lane handoff RUN_ID TASK_ID
```

`start` defaults to persisting ready Tasks and emitting or previewing actions. With no HostAdapter bound it returns `ACTION_RELAY_REQUIRED`, preserves the exact relay, and does not mislabel the state as `HOST_CAPABILITY_BLOCKED`. Blocking is allowed only after capability discovery confirms the exact tool is absent. `drive` continues the Coordinator loop and `lane` commands drive an independent Task Lane. Legacy plan/run import is exposed through the read-only API below. Recovery uses SQLite state/journal, real host receipts, leases, Git/workspace identity, and snapshot validity to recompute ready actions; route assurance is expressed through `observe_if_exposed`, `request_only`, or stricter policy rather than fabricated actual evidence. Unrecoverable conditions return a blocker while immutable artifacts are retained.
Host-conformance diagnostics verify `requested`, `resolved`, and `actual` resource layers alongside host `identity`.
Neutral action checks require coherent `create`, `read`, `wait`, `cancel`, and `idempotency` traces for tool and policy changes; missing traces return `BLOCKED`.

## Legacy import

`LegacyPlanImportAPI`, `LegacyRunStateImportAPI`, and `LegacyResourceTranslator` are read-only parse/validate/translate APIs. Legacy plans and run snapshots are never written back; resource profiles become `ResourceEnvelope` values, and losses, unknowns, warnings, and model evidence are explicit. Without an actual receipt, model evidence remains unresolved.

## Install and example

Choose `plugins/allinluna/` in Codex Plugins. Python entry example:

```python
from allinluna_runtime.packs import SinglePublicSkillAPI

compiled = SinglePublicSkillAPI().compile({
    "goal": "Implement the requested software outcome",
    "done_when": ["tests and changed-path evidence are available"],
})
print(compiled.task_graph.to_dict())
```

Apache License 2.0. See `LICENSE`.
