# All in Luna v2.0.0-rc.2 Desktop canary

The current corrective qualification is **PASS**. It starts with
`SinglePublicSkillAPI`, relays two dependency-ordered top-level Tasks through
the real Desktop `codex_app__create_thread` capability, and closes both Tasks
and the root Run through real `LaneDriver`, lane-direct WorkUnits,
`WorkHandoff`, `EvidenceCollector`, `LaneHandoff`, and `HandoffProcessor`
records.

The authoritative records are:

- `corrective-qualification.json`: complete result, runtime and artifact refs,
  dependency evidence, native classification, and the superseded attempt.
- `corrective-actions.json`: both exact action identities and contract hashes.
- `corrective-receipts.json`: both raw Desktop receipt identities and thread
  IDs.

The historical `acceptance.json`, `desktop-action.json`,
`desktop-receipt.json`, and `desktop-result.json` remain immutable evidence of
the earlier blocked qualification. They are not rewritten as a pass;
`acceptance.json` now only points to the fresh corrective run that supersedes
it.

Both qualification databases were created under a fresh temporary T5 root.
No historical `.allinluna` database or receipt was mutated. The final canary
is intentionally projectless, so neither Desktop Task writes into a repository
project.

## Corrective result

The final run proves this sequence:

```text
SinglePublicSkillAPI.start
→ CoordinatorDriver
→ real Desktop Producer Lane
→ lane-direct WorkUnit and verified export
→ WorkHandoff → LaneHandoff → HandoffProcessor
→ Consumer becomes ready
→ real Desktop Consumer Lane
→ lane-direct WorkUnit → WorkHandoff → LaneHandoff → HandoffProcessor
→ root Run completed
```

The runtime adapter did not advertise `native_subagent`, so the conditional
native recursive canary is `NOT_APPLICABLE`. This does not block the release:
both `native_preferred` WorkUnits truthfully selected `lane_direct` and
completed. The separate `native_required` negative canary selected no direct
executor and ended at `lane-blocked` with `HOST_CAPABILITY_BLOCKED`.

## Historical relay procedure

The remainder documents the original single-Lane relay procedure retained for
reproducibility.

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
