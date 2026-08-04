# All in Luna

[简体中文（默认）](README.md) | [English](README.en.md)

**Adaptive orchestration. Your models, your budget, your way.**

All in Luna is an open-source Codex plugin for planning and completing software projects with explicit control over models, reasoning effort, delegation, concurrency, and resource limits. It works with an existing repository or a greenfield idea, from a small plan-only request to a persistent multi-agent program.

The name is also a promise: you can run a mixed-model workflow, or go all in with a hard Luna-only policy—including the deliberately aggressive `mad-luna` mode.

```text
repository or idea
  -> complete validated plan
  -> Sponsor conversation creates an independent Coordinator + CounterPilot
  -> runtime model/capability resolution
  -> optional child Coordinators for high-concurrency shards
  -> conflict-free top-level owner lanes
  -> risk-proportional integration and acceptance
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
| `fast` | High-throughput delivery | 24 desired slots with hierarchical coordination when useful |
| `ultra-fast` | Maximum mixed-model throughput | 48 desired slots, host-capped, with reviewed decomposition |
| `all-luna` | Consistent Luna-only execution | Hard Luna family lock with high reasoning and moderate concurrency |
| `mad-luna` | Maximum Luna swarm | Hard Luna family lock, maximum reasoning, maximum safe concurrency, independent Luna verification on high-risk work |
| `custom` | Exact user control | Per-role model, reasoning, fallback, concurrency, and budget policy |

Resource modes change allocation and timing—not scope or completion criteria. A hard model lock is never silently bypassed. If the requested model is unavailable, All in Luna reports the mismatch and follows the configured pause or fallback policy.

Model, reasoning effort, delegation tier, concurrency, and budget are separate controls. Logical model tiers are resolved against the host's current catalog; the run state records the requested and actual values independently.

Resolution supports `ultra` reasoning and can rank catalog candidates using profile-weighted quality, speed, and economy metadata. Fallback lists are actually tried in order; missing scores preserve stable catalog order instead of inventing cost or performance telemetry.

The user's main conversation is the **Sponsor**, not the Coordinator. By default the Sponsor creates a separate user-visible primary Coordinator task and an independent CounterPilot task. The Coordinator dispatches substantive owner lanes as user-visible top-level Codex tasks. Each owner may create bounded internal subagents under its own ownership and model policy.

Only after the host tool catalog has been fully checked and the top-level task creation tool is genuinely absent does All in Luna automatically fall back to a root subagent and then sequential execution; it does not ask the user to reconfirm. The plan keeps `top_level_tasks=true`, while run state records the actual tier and `top-level-tool-unavailable`. If the fallback surface cannot satisfy a hard lock such as Luna-only, that lane pauses instead of pretending compliance or switching models.

On Codex App, All in Luna reads the user-visible top-level task catalog separately from the subagent catalog. A Luna model exposed by `create_thread` is therefore available for top-level execution even if subagents expose only Sol/Terra. Goal permission and top-level-task permission are independent.

Codex App commonly exposes `create_thread` as a deferred tool, so absence from the short initial tool list does not mean it is unavailable. All in Luna must search the host's full/deferred tool catalog (for example `functions.exec`'s `ALL_TOOLS` or a tool-search facility) and create top-level tasks when found. Reporting the tool unavailable without this discovery step is an execution defect.

If a user later asks to execute an older plan-only file, All in Luna creates a separate validated execute-ready revision. Current explicit task authorization is recorded in that revision; the historical plan is not mutated, and a stale `top_level_tasks=false` never causes a silent subagent fallback.

## Default execution topology (important)

All in Luna **does not default to completing everything sequentially inside the current task**. When launched through the packaged Plan/Run entry, it:

1. identifies substantive owner lanes that are safely parallel and have independent deliverables and file ownership;
2. creates multiple user-visible top-level Codex tasks for those lanes, visible in the Codex sidebar;
3. keeps the independent primary Coordinator focused on dependencies, monitoring, defect return, and risk-proportional integration/acceptance;
4. lets every top-level owner create bounded internal subagents when useful; and
5. creates child Coordinator tasks for disjoint shards when concurrency is high, while keeping one primary Coordinator authoritative.

The independent Coordinator is the mandatory default execution role and does not require a separate user toggle. The Sponsor remains available for product choices and external authority, but does not become an implementation owner. New plans contain this structured control-plane contract, and execute-ready revision upgrades older plans.

Run now uses a deterministic coordinator tick to produce next actions and complete owner briefs: dispatch ready top-level tasks, record thread/host/worktree/model evidence, wait, collect results, release dependencies, and tick again. Active plans can append tasks and stop boundaries. Acceptance defects return structurally to the original owner and unresolved defects prevent completion. Git evidence tooling verifies real commits, parents, trees, changed paths, and ownership.

“Sequential” means dependencies between top-level owners, not product implementation by the Sponsor or Coordinator. Even when only one owner is ready, the Coordinator creates that top-level task, monitors it, and releases the next owner afterward.

Desired concurrency defaults are: `economy` 4, `balanced` 8, `premium` 12, `speed` 12, `fast` 24, `ultra-fast` 48, `all-luna` 8, and `mad-luna` 24. Custom values from 1–64 are supported. Actual concurrency is limited by the host, machine, ready DAG width, writable ownership, and budget. All in Luna does not invent micro-tasks merely to fill slots.

These are presets, not fixed ceilings. During Plan, a user may provide a value from 1–64; if unanswered, Plan continues with `balanced + 8`. At 16 or more desired slots, Plan asks once whether a high-quality model should review the decomposition, dependencies, ownership, and conflict risks. Accepting enables that review; declining preserves the user's concurrency without repeated prompts.

When high concurrency is useful, one primary Coordinator can create child Coordinators for disjoint task shards (normally 2–12 owners per shard). Child Coordinators dispatch and monitor only their assigned owners. They do not form an open-ended hierarchy, share writable ownership, or replace the primary Coordinator's global dependency and completion authority.

The CounterPilot is a distinct top-level challenger, not another approval bureaucracy. It tests scope, assumptions, dependency safety, and failure recovery at risk-triggered points. `mad-luna` can request a second independent challenge pass for high-risk work. CounterPilot findings are evidence-bearing challenges and do not silently mutate implementation.

Every All in Luna plan must record `top_level_tasks=true` and `top_level_tasks_basis=allinluna-default`; there is no mode that emits `false`. The authorization remains true for small, non-Git, plan-only, or tightly coupled projects even when dependency analysis ultimately yields only one owner.

Non-Git projects enter a Git-readiness flow first. All in Luna checks whether Git is installed, whether the directory is initialized, and whether a baseline commit exists for worktrees, then requests one authorization to install Git, initialize the repository, and create that baseline. If accepted, All in Luna prepares Git and continues with isolated top-level tasks. If declined, it uses ordinary subagents or sequential execution while preserving `top_level_tasks=true` in the plan and recording the actual fallback reason.

Resource policies compose. For example, `all-luna + fast` keeps every delegated role hard-locked to Luna while applying the fast velocity policy. It does not switch the base profile to mixed-model `fast`.

## Parallel-only mode

If a user already has an approved implementation plan—whether written manually or produced by a planning skill such as Grill Me—`parallel-only` imports it without reopening product direction. All in Luna validates dependencies and ownership, builds the independent Coordinator/CounterPilot control plane, and focuses on concurrent execution. Integration and acceptance are added only when the declared risk or plan explicitly requires them.

First-time users can simply enter:

```text
使用 All in Luna 完整推进当前项目。先生成完整计划，再通过多个侧边栏可见的顶层 Codex 任务并行实施；每个顶层负责人可以按需要使用有界 subagents。使用 balanced 模式，不创建 Goal。
```

## Install

Add this repository as a Codex marketplace:

```powershell
codex plugin marketplace add zenx0x/allinluna
```

Then install **All in Luna** from the Codex Plugins directory and start a new task so the skills are discovered.

## Example prompts

```text
Use $allinluna-plan to inspect this repository and produce a complete plan only.
Use balanced mode. Plan independent owner lanes as user-visible top-level Codex
tasks; each owner may use bounded internal subagents. Do not create a Goal or begin implementation.
```

```text
Use $allinluna-run to execute the approved plan through implementation,
integration, and acceptance through top-level Codex task owners. Each owner may
use bounded internal subagents. Use economy mode and ask before escalation.
```

```text
Use $allinluna-run in mad-luna mode. Hard-lock every delegated role to Luna,
use maximum supported reasoning and maximum safe parallelism, persist run state,
create user-visible top-level Codex tasks, do not create a Goal, and never
substitute another model silently.
```

```text
Use $allinluna-run as a long-running Goal. Create isolated owner tasks for
independent lanes, keep the coordinator focused on dependencies and recovery,
and continue until the plan's completion standard is met.
```

## What it guarantees

- Full requested scope remains the completion standard; a first vertical slice is only a progress checkpoint.
- Goal creation is opt-in, never inferred.
- Every All in Luna plan authorizes user-visible top-level tasks; denying Goal creation never changes that field.
- When the top-level tool is genuinely absent, worktrees are unavailable, or Git preparation is declined, only actual delegation falls back; the plan field never returns to `false`, and verified tool absence does not require another confirmation.
- The Sponsor, primary Coordinator, CounterPilot, child Coordinators, and owners use distinct task identities.
- Each top-level owner may use bounded internal subagents; Coordinators do not substitute subagents for planned owner lanes.
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
  --profile mad-luna --catalog runtime-catalog.json `
  --delegation top-level-task --pretty

# Initialize, inspect, and validate resumable state
python plugins/allinluna/skills/allinluna-run/scripts/prepare_execution_plan.py `
  plan.json --output plan.execute-ready.json `
  --authorize-implementation-writes --authorize-top-level-tasks --deny-goal
# Import an already-approved plan without replanning product direction
python plugins/allinluna/skills/allinluna-run/scripts/import_parallel_plan.py `
  approved-plan.json --output parallel.execute-ready.json --profile fast `
  --high-concurrency-review accepted --decomposition-model gpt-5.6-sol
python plugins/allinluna/skills/allinluna-run/scripts/init_run.py plan.json `
  --profile balanced --catalog runtime-catalog.json
# Sponsor creates and monitors the independent control plane
python plugins/allinluna/skills/allinluna-run/scripts/bootstrap_control_plane.py RUN_DIRECTORY --pretty
python plugins/allinluna/skills/allinluna-run/scripts/sponsor_tick.py RUN_DIRECTORY --pretty
# The primary or a named child Coordinator advances its own disjoint task set
python plugins/allinluna/skills/allinluna-run/scripts/coordinator_tick.py RUN_DIRECTORY --pretty
python plugins/allinluna/skills/allinluna-run/scripts/coordinator_tick.py `
  RUN_DIRECTORY --coordinator-id subcoordinator-1 --pretty
python plugins/allinluna/skills/allinluna-run/scripts/render_status.py RUN_DIRECTORY
python plugins/allinluna/skills/allinluna-run/scripts/validate_run.py RUN_DIRECTORY --pretty

# Incremental active-plan revision, owner repair, and human controls
python plugins/allinluna/skills/allinluna-run/scripts/revise_active_plan.py `
  RUN_DIRECTORY --patch revision.json --reason "user added scope"
python plugins/allinluna/skills/allinluna-run/scripts/manage_defect.py `
  RUN_DIRECTORY --action create --defect-id D1 --owner-task T1 `
  --summary "..." --reproduction "..." --reason "independent acceptance failed"
python plugins/allinluna/skills/allinluna-run/scripts/control_run.py `
  RUN_DIRECTORY --action set-concurrency --concurrency 12 --reason "user changed concurrency"
python plugins/allinluna/skills/allinluna-run/scripts/refresh_task_resources.py `
  RUN_DIRECTORY --catalog runtime-catalog.json `
  --role engineer=gpt-5.6-luna:high --reason "user changed model and reasoning"
```

Resource changes apply only to undispatched or retryable owners. They never rewrite actual
runtime evidence for running or completed tasks.

Schemas and editable examples live beside each skill under `assets/`. Trigger and behavioral evaluation cases live under `evals/` and run in CI with the lifecycle tests.

## Design influences

The implementation is original and draws general workflow lessons from the Agent Skills specification, OpenAI plugin examples, Anthropic's skill evaluation guidance, Vercel's progressive-disclosure patterns, and Superpowers' planning and verification practices. No third-party skill text or code is copied into this repository.

## License

Apache License 2.0. See [LICENSE](LICENSE).
