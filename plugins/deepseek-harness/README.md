# All in Flash for DeepSeek Harness

All in Flash is a Cordis plugin for DeepSeek Harness (DSH), powered by the
public, durable All in Luna CLI. It exposes four model-facing tools:

- `allinflash_start` creates a run from a goal.
- `allinflash_status` reads a run's durable state.
- `allinflash_next_actions` returns frozen host actions for the active run.
- `allinflash_relay_action` starts the matching DSH lane child and immediately
  persists its observed identity as the exact action receipt.

The plugin does not translate a returned `HostAction` into another operation.
Its exact relay and receipt semantics remain owned by All in Luna's runtime.

## Installation

Install the All in Luna CLI, then let the published package create and resolve
a dedicated DSH profile:

```powershell
python -m pip install "allinluna==2.0.0rc3"
npx @zenx0x/allinflash@0.2.0 init --profile allinflash
dsh --profile allinflash
```

The initializer verifies the `allinluna` command, creates an idempotent managed
profile, enables the All in Flash bundle, writes its Cordis configuration, and
runs the DSH profile package installation. It refuses to overwrite an unmanaged
profile unless `--force` is explicit.

Check an installation without starting the server:

```powershell
npx @zenx0x/allinflash@0.2.0 doctor --profile allinflash
```

`allinflash` is a new user-owned profile. It does not modify the DSH-shipped
`standard`, `code`, `minimal`, or `cordis` presets.

Use `--db`, `--cwd`, `--model`, or `--runtime-command` with `init` when the
defaults do not match the deployment. Repository contributors can instead run
`python scripts/install_allinflash_profile.py --force`; that development path
uses `uv run allinluna` and the checkout's `runtime.db`.

The profile runs All in Luna with `--adapter deepseek-harness`, which emits
only `allinflash__create_top_level_task` for top-level lanes. The relay tool
rejects a different opcode, substitutions, or an action-contract hash mismatch.
