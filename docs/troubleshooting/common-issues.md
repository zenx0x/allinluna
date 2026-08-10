# Troubleshooting

## `ACTION_RELAY_REQUIRED`

This means no HostAdapter is bound in the current runtime process. It is not
evidence that the requested tool is unavailable and it is not a completed run.
Keep the exact action and its `action_contract_hash` for the Desktop or other
authorized host relay, then ingest the raw receipt and continue the runtime
loop.

## `HOST_CAPABILITY_BLOCKED`

Use this only after capability discovery confirms that the exact requested tool
is absent. Do not map an adapter error, a pending receipt, or a different tool's
success to this status. A receipt must report the observed
`actual_tool`, `actual_capability`, and `action_contract_hash` explicitly.

## Resource values are `unresolved`

The runtime intentionally separates `requested`, `resolved`, and `actual`.
Missing host telemetry leaves `actual: null` and `actual_state: unresolved`.
That state is honest evidence, not a silent model fallback. An exact
`codex_app__create_thread` action is executable only after route resolution
provides a non-empty model.

## Project resolution is missing

Repository-backed top-level creation requires a project-resolution receipt with
`projectId` and its worktree `environment`. If no project identity exists, the
runtime should emit the project-resolution action first. Never substitute a
Task ID for a project ID. For work with no repository, use an explicit
`{"type":"projectless"}` target.

## A run is pending after interruption

Use the same runtime database and let recovery recompute ready actions from
Store state, leases, receipts, workspace identity, and context snapshots:

```text
allinluna status RUN_ID
allinluna reconcile RUN_ID
allinluna drive RUN_ID
```

Do not copy rows into another database or turn a pending action into completion
because time has passed. Immutable artifacts remain evidence; a missing receipt
remains missing.

## Legacy input changes unexpectedly

Legacy plan and run imports are read-only parse/validate/translate operations.
If the source changes, inspect the imported warnings, losses, unknowns, and
model evidence. The importer must not write back to the source or invent an
actual host receipt.
