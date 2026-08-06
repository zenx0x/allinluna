# All in Luna v2.0.0-rc.2 Desktop canary

This package is the real Desktop relay record for the RC2 product surface. It
is intentionally projectless so the canary does not create or mutate a
repository project. The request and receipt files are inputs/templates only;
they are not evidence until the exact Desktop action has been invoked and the
receipt has been exported by that same invocation.

## Prepare the exact action

Run from the repository root with the installed CLI (or use
`python -m allinluna_runtime.cli` with `PYTHONPATH=plugins/allinluna/runtime`):

```powershell
$db = Join-Path $env:TEMP "allinluna-rc2-desktop-canary.db"
allinluna --db $db start `
  --intent-id desktop-canary-v2-rc2 `
  --goal "Run the v2.0.0-rc.2 real Desktop canary" `
  --model "gpt-5.3-codex-spark" `
  --reasoning high
```

The output must contain one persisted `codex_app__create_thread` action. With
no HostAdapter bound, `ACTION_RELAY_REQUIRED` is the expected result. Save the
raw action JSON beside the canary receipt.

## Relay exactly once

Invoke the real Desktop `codex_app__create_thread` tool using only the action's
`target`, `prompt`, `model`, `thinking` (when present), and `title` arguments.
Do not replace the opcode with a subagent, current-thread action, or local
fallback. The public project target must contain only the Desktop-supported
typed environment; a projectless target must remain explicitly projectless.

## Export and ingest the receipt

Copy `receipt.template.json` to a working file and fill every placeholder from
the same Desktop invocation. In particular, preserve the emitted
`action_contract_hash` and explicitly record `actual_tool` and
`actual_capability` as observed by Desktop. A `clientThreadId` without a real
`threadId` remains pending and cannot activate the Task.

```powershell
allinluna --db $db ingest-receipt desktop-canary-v2-rc2 .\desktop-receipt.json
allinluna --db $db drive desktop-canary-v2-rc2
allinluna --db $db status desktop-canary-v2-rc2
```

The acceptance record must include the DB path, raw action, exported Desktop
result, filled receipt, and the observed task/receipt status. If the Desktop
tool is unavailable, retain the exact relay action as `ACTION_RELAY_REQUIRED`;
do not rewrite it as `HOST_CAPABILITY_BLOCKED` or claim a simulated PASS.
