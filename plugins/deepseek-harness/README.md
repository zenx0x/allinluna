# All in Flash for DeepSeek Harness

All in Flash is a Cordis plugin for DeepSeek Harness (DSH), powered by the
public, durable All in Luna CLI. It exposes three model-facing tools:

- `allinflash_start` creates a run from a goal.
- `allinflash_status` reads a run's durable state.
- `allinflash_next_actions` returns frozen host actions for the active run.
- `allinflash_relay_action` starts the matching DSH lane child and immediately
  persists its observed identity as the exact action receipt.

The plugin does not translate a returned `HostAction` into another operation.
Its exact relay and receipt semantics remain owned by All in Luna's runtime.

## Development installation

Create the dedicated `allinflash` DSH profile and resolve its package:

```powershell
python scripts/install_allinflash_profile.py
dsh plugin --profile allinflash install
```

The installer configures `uv run allinluna` and the repository's `runtime.db`; adapt
`$env:DSH_HOME/profiles/allinflash/cordis.patch.yml` if your All in Luna
runtime is installed elsewhere. Then start the profile with:

```powershell
dsh --profile allinflash web
```

`allinflash` is a new user-owned profile. It does not modify the DSH-shipped
`standard`, `code`, `minimal`, or `cordis` presets.

The profile runs All in Luna with `--adapter deepseek-harness`, which emits
only `allinflash__create_top_level_task` for top-level lanes. The relay tool
rejects a different opcode, substitutions, or an action-contract hash mismatch.
