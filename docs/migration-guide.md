# v2.0.0-rc.1 migration guide

This guide covers the release-hardening contract changes from the previous
All in Luna surface (`1.1.1`) and Research Routes surface (`0.2.0`). The RC
keeps the existing Core, TaskGraph, Context Kernel, GSD, and Research Routes
semantics.

## Runtime database

The canonical SQLite runtime schema is v8. Existing databases must be opened
through `Store` so migrations and journal checks run before scheduling. Do not
copy rows between databases or treat a JSON status projection as a second
state store.

## Top-level targets

`codex_app__create_thread` no longer uses `target.task_id` as a project
identity. For a repository-backed Task, run the project-resolution action and
ingest a receipt containing an explicit `projectId` and worktree `environment`.
The next exact action then contains:

```json
{
  "type": "project",
  "projectId": "<resolved-project-id>",
  "environment": {"type": "worktree"}
}
```

For a projectless Task, the target is explicit:

```json
{"type": "projectless", "directoryName": "<runtime-generated-name>"}
```

If a project is required but no resolution receipt exists, the runtime emits
`codex_app__list_projects` before any create-thread action.

## Resource route and model policy

The Core asks for a capability/resource envelope. It does not choose a
concrete model name. The RC deployment envelope is Luna-class models,
normally with medium/high/xhigh reasoning, max only for critical work, plus
`gpt-5.3-codex-spark` outside Luna.

An exact create-thread action is executable only after host route resolution
provides a non-empty `model`. If resolution remains unknown, inspect and relay
the non-executable `resolve-resource-route` action; do not add a model by
hand, substitute a tool, or freeze a create action with missing arguments.

## Host relay and receipts

When Python has no bound HostAdapter, the result is
`ACTION_RELAY_REQUIRED`. The persisted exact action remains pending for the
Desktop coordinator. `HOST_CAPABILITY_BLOCKED` is valid only after capability
discovery confirms that the exact tool is absent.

An externally ingested active top-level receipt must explicitly include:

- `actual_tool`;
- `actual_capability`;
- `action_contract_hash` equal to the persisted exact action.

The requested action is not evidence of what ran. A missing or mismatched
observed field produces `HOST_PROTOCOL_VIOLATION`; it must not activate a
Task. Only a runtime direct call through a trusted HostAdapter may sign the
observed fields.

## CLI changes

`start` persists ready Tasks and emits or previews actions. Use `drive` to
continue the Coordinator loop and `lane` to operate an independently
bootstrapped Task Lane:

```text
allinluna start --goal "..."
allinluna drive RUN_ID
allinluna lane start RUN_ID TASK_ID
allinluna lane tick RUN_ID TASK_ID
allinluna lane drive RUN_ID TASK_ID
allinluna lane handoff RUN_ID TASK_ID
```

For a real Desktop canary, use the minimal package in
`docs/phase3-desktop-canary/`. Replace every placeholder in its receipt
template with values observed from the same Desktop invocation before
running `ingest-receipt`.
