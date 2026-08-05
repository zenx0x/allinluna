# Minimal Desktop canary package

This package is the smallest real Desktop execution bundle for the v2 RC.
It contains a projectless request, an exact receipt template, and the relay
commands. The template is not a receipt until a real Desktop invocation fills
its placeholders.

## 1. Prepare the runtime

From the repository root in PowerShell:

```powershell
$env:PYTHONPATH = "plugins/allinluna/runtime"
$db = "canary-runtime.db"
python -m allinluna_runtime.cli --db $db start --goal "Run the phase 3 Desktop canary" --model "gpt-5.3-codex-spark" --reasoning high
```

The JSON output must contain a persisted exact `codex_app__create_thread`
action. With no bound HostAdapter, dispatch status is
`ACTION_RELAY_REQUIRED`; this is expected and is the relay handoff to Desktop.

## 2. Relay exactly

Copy only these fields from the emitted action into the Desktop call:

```text
target, prompt, model, thinking (when present), title
```

Call `codex_app__create_thread` exactly once. Do not use `target.task_id` as a
project identity and do not substitute another tool.

## 3. Ingest the real receipt

Copy `receipt.template.json` to a working file and replace every
`<PLACEHOLDER>` with values from the same Desktop call. In particular,
`actual_tool`, `actual_capability`, and `action_contract_hash` are mandatory;
the hash must equal the emitted action hash. Then run:

```powershell
python -m allinluna_runtime.cli --db $db ingest-receipt <RUN_ID> .\desktop-receipt.json
python -m allinluna_runtime.cli --db $db drive <RUN_ID>
```

Attach the raw action JSON, real Desktop export, filled receipt, and DB path
to the acceptance record. Do not claim success from the action alone.
