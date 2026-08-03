# All in Luna

**Adaptive orchestration. Your models, your budget, your way.**

All in Luna is an open-source Codex plugin for planning and completing software projects with explicit control over models, reasoning effort, delegation, concurrency, and resource limits. It works with an existing repository or a greenfield idea, from a small plan-only request to a persistent multi-agent program.

The name is also a promise: you can run a mixed-model workflow, or go all in with a hard Luna-only policy—including the deliberately aggressive `mad-luna` mode.

```text
repository or idea
  -> complete validated plan
  -> runtime model/capability resolution
  -> conflict-free owner lanes
  -> one phase integration
  -> one independent acceptance
  -> accepted common baseline
```

## Included skills

- `allinluna-plan` inspects a repository or idea and produces a complete, dependency-aware development plan. It does not create a Goal or begin implementation unless the user asks.
- `allinluna-run` executes an approved plan, records requested and actual runtime settings, coordinates isolated owners when useful, survives task compaction, and continues through integration and acceptance.

## Resource modes

| Mode | Purpose | Default behavior |
| --- | --- | --- |
| `premium` | Highest decision quality | Frontier planning and acceptance; strong engineering models; independent review for high-risk work |
| `balanced` | Best general default | Strong planning, efficient implementation, bounded parallelism |
| `economy` | Reduce resource use | Luna-first workers, low concurrency, explicit approval before escalation |
| `speed` | Reduce wall-clock time | Aggressive parallelism for genuinely independent owners |
| `all-luna` | Consistent Luna-only execution | Hard Luna family lock with high reasoning and moderate concurrency |
| `mad-luna` | Maximum Luna swarm | Hard Luna family lock, maximum reasoning, maximum safe concurrency, independent Luna verification on high-risk work |
| `custom` | Exact user control | Per-role model, reasoning, fallback, concurrency, and budget policy |

Resource modes change allocation and timing—not scope or completion criteria. A hard model lock is never silently bypassed. If the requested model is unavailable, All in Luna reports the mismatch and follows the configured pause or fallback policy.

Model, reasoning effort, delegation tier, concurrency, and budget are separate controls. Logical model tiers are resolved against the host's current catalog; the run state records the requested and actual values independently.

## Install

Add this repository as a Codex marketplace:

```powershell
codex plugin marketplace add zenx0x/allinluna
```

Then install **All in Luna** from the Codex Plugins directory and start a new task so the skills are discovered.

## Example prompts

```text
Use $allinluna-plan to inspect this repository and produce a complete plan only.
Use balanced mode. Do not create a Goal or begin implementation.
```

```text
Use $allinluna-run to execute the approved plan through implementation,
integration, and acceptance. Use economy mode and ask before escalation.
```

```text
Use $allinluna-run in mad-luna mode. Hard-lock every delegated role to Luna,
use maximum supported reasoning and maximum safe parallelism, persist run state,
and never substitute another model silently.
```

```text
Use $allinluna-run as a long-running Goal. Create isolated owner tasks for
independent lanes, keep the coordinator focused on dependencies and recovery,
and continue until the plan's completion standard is met.
```

## What it guarantees

- Full requested scope remains the completion standard; a first vertical slice is only a progress checkpoint.
- Goal creation is opt-in, never inferred.
- Project instructions and dirty worktrees are inspected and preserved.
- Independent writers receive explicit ownership; defects return to the owning task.
- Requested and actual model/reasoning settings are recorded separately.
- Missing usage or cost telemetry is reported as `unavailable`, never invented.
- Live external mutation and destructive operations still require the user's authority.
- Runtime state defaults to `~/.codex/allinluna/runs`, outside the target repository.

## Development

The deterministic helpers use only Python's standard library and require Python 3.11 or newer.

```powershell
python -m unittest discover -s tests -v
python scripts/validate_repository.py
```

The plugin contains no MCP server, telemetry, hosted service, or implicit network call. It orchestrates only capabilities exposed by the user's Codex environment and degrades honestly from top-level tasks to subagents to sequential execution.

## Deterministic helpers

The skills include portable, standard-library tools for planning and recovery:

```powershell
# Bounded read-only repository inventory
python plugins/allinluna/skills/allinluna-plan/scripts/inspect_project.py . --pretty

# Validate an executable plan
python plugins/allinluna/skills/allinluna-plan/scripts/validate_plan.py plan.json --pretty

# Resolve mad-luna against a host model catalog
python plugins/allinluna/skills/allinluna-run/scripts/resolve_profile.py `
  --profile mad-luna --catalog runtime-catalog.json --pretty

# Initialize, inspect, and validate resumable state
python plugins/allinluna/skills/allinluna-run/scripts/init_run.py plan.json --profile balanced
python plugins/allinluna/skills/allinluna-run/scripts/render_status.py RUN_DIRECTORY
python plugins/allinluna/skills/allinluna-run/scripts/validate_run.py RUN_DIRECTORY --pretty
```

Schemas and editable examples live beside each skill under `assets/`. Trigger and behavioral evaluation cases live under `evals/` and run in CI with the lifecycle tests.

## Design influences

The implementation is original and draws general workflow lessons from the Agent Skills specification, OpenAI plugin examples, Anthropic's skill evaluation guidance, Vercel's progressive-disclosure patterns, and Superpowers' planning and verification practices. No third-party skill text or code is copied into this repository.

## License

Apache License 2.0. See [LICENSE](LICENSE).
