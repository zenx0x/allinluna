---
name: allinluna-run
description: Execute and resume a complete development plan with explicit model, reasoning, delegation, concurrency, Git ownership, verification, recovery, integration, and acceptance controls. Use when the user asks to implement or continue a plan, run parallel agents or top-level tasks, use a resource mode such as economy or mad-luna, persist a long-running workflow, or recover an interrupted multi-lane development effort. Every All in Luna plan authorizes top-level tasks; Goal creation remains separately opt-in.
---

# All in Luna - Run

Execute the approved scope to its real completion standard. A plan, task dispatch, first commit, or first passing test is progress, not completion.

## 1. Announce and load

Tell the user this skill is controlling execution and name the selected resource mode. Load:

1. the approved JSON plan;
2. applicable `AGENTS.md` and repository instructions;
3. current Git status and protected/dirty paths;
4. any existing All in Luna run state.

If no executable plan exists, use `$allinluna-plan` first. Do not repeatedly replan a plan whose assumptions still hold.

After plan validation, remain the root coordinator. This is mandatory default execution topology, not an optional optimization. Verify the plan's orchestration contract and do not start implementing product files in the current task. Initialize run state, complete or decline the Git-bootstrap decision, then create the first dependency-ready top-level owner task even when only one owner is ready. Continue dispatching newly ready owners as dependencies clear; a sequential dependency graph still uses separate top-level owners managed by the coordinator.

When the user asks All in Luna to execute a previously `plan-only` plan, do not run that stale snapshot and do not mutate it in place. Create a validated execution revision with `scripts/prepare_execution_plan.py`. The revision always sets `top_level_tasks=true`. “Do not create a Goal” independently keeps `goal_creation=false`.

```bash
python scripts/prepare_execution_plan.py PLAN.json \
  --output PLAN.execute-ready.json \
  --authorize-implementation-writes \
  --authorize-top-level-tasks \
  --deny-goal --pretty
```

Use `--authorize-git-operations` only when the user also authorized the required branch/worktree/commit operations. If a top-level-task execution requires Git worktrees but Git authorization is absent, ask for that specific authorization; do not fall back to subagents.

Validate the plan:

```bash
python ../allinluna-plan/scripts/validate_plan.py PLAN.json --pretty
```

Read [references/orchestration-contract.md](references/orchestration-contract.md) before dispatch.

## 2. Resolve runtime capabilities honestly

Determine which execution tier the host actually exposes:

1. **User-owned top-level tasks:** every All in Luna plan authorizes this tier; use it whenever the host exposes the capability.
2. **Subagents:** use for bounded independent work when available.
3. **Sequential:** use the current task when neither delegation tier exists or when work is tightly coupled.

The root coordinator's default delegation for every built-in resource profile is `top-level-task`, and every All in Luna plan records that authorization as true. For independent substantive owner lanes, create user-visible top-level Codex tasks rather than root-level subagents. Do not ask the user to repeat the authorization and do not use a root-level subagent as a substitute for a planned top-level owner.

Each created top-level owner may create its own bounded subagents when that helps complete its assigned scope. Authorize this in the self-contained owner brief. Owner subagents must remain inside the owner's paths, base, model lock, budget, and completion contract; they do not become additional top-level lanes and their output is not completion until the owner integrates and verifies it. In `all-luna` and `mad-luna`, owner subagents must inherit or explicitly use Luna and may not silently switch families.

Record requested and actual tier. Never represent subagents as top-level tasks or sequential work as parallel execution.

On Codex App hosts, discover top-level capability before inspecting subagents:

1. do not rely on the initially rendered tool list: when `functions.exec` is present, search its deferred `ALL_TOOLS` catalog for `codex_app__create_thread` and `codex_app__list_projects`; when a tool-search capability is present, search for `create_thread`, `list_projects`, and `list_threads` there;
2. load/call the discovered tools and treat a successful schema discovery as capability availability; the tool does not need to have been printed in the original prompt;
3. read the model and reasoning combinations declared by `codex_app__create_thread` itself;
4. treat that list as the `top-level-task` model catalog, even when the subagent catalog exposes different models;
5. call `codex_app__list_projects` before creating project-scoped worktree tasks;
6. create a delegation-scoped runtime catalog like `assets/runtime-catalog.example.json` outside the target repository;
7. resolve the requested profile against the intended delegation surface.

In the official Codex desktop app, `codex_app__create_thread` is a deferred app tool and should normally be discoverable even when omitted from the short initial tool list. Saying “top-level tasks are unavailable” without performing the deferred search above is a workflow defect. If discovery returns its declaration, use it; do not offer or select fallback merely because direct invocation was not initially visible.

Require Git-backed isolation for parallel top-level implementation owners. Run `scripts/inspect_git_readiness.py PROJECT_ROOT --pretty` first. If Git is missing, ask once for permission to install Git with the host's supported package manager. If the directory is not a repository or has no commit, ask in the same request for permission to initialize it, preserve the existing files, create an initial baseline commit, and enable worktrees. After authorization, perform the setup, re-run readiness inspection, then dispatch project-scoped worktree tasks. Never install software, initialize Git, configure identity, or commit without that authorization.

If the user declines Git setup, keep the plan's `top_level_tasks=true` invariant but record actual delegation as `subagent` when available, otherwise `sequential`, with fallback reason `user-declined-git-bootstrap`. The refusal is approval for this ordinary fallback only; it does not authorize other model-family fallbacks, destructive actions, publication, or live mutation. Do not retry invalid worktree parameters or claim top-level tasks were created.

Never use “empty project”, “non-Git”, “only one task is ready”, “core and GUI have dependencies”, or “no Git authorization yet” to begin current-thread implementation. The first three require coordinator-managed top-level owners; missing Git authorization requires the Git question first.

Do not infer that Luna is unavailable from a subagent-only model list. When an older plan says false, create the execution revision above and set it true. Never choose subagents merely because the stale plan says false. “Do not create a Goal” controls only Goal creation and does not deny top-level tasks.

Resolve models against the current host catalog. Keep requested and actual model/reasoning fields separate. If a requested model or reasoning level is unavailable:

- with a hard lock, pause that lane or use only an explicitly configured fallback;
- without a hard lock, apply the profile's fallback policy and disclose it;
- never invent a model assignment after the fact.

Read [references/resource-profiles.md](references/resource-profiles.md) and [references/delegation-and-models.md](references/delegation-and-models.md).

## 3. Initialize persistent state

State defaults outside the repository under `~/.codex/allinluna/runs`:

```bash
python scripts/init_run.py PLAN.json --profile balanced --catalog RUNTIME_CATALOG.json
```

Pass `--runtime-tier top-level-task` when the user authorized user-visible tasks. If the user requested a specific model such as Luna, pass the exact supported model and reasoning to `codex_app__create_thread`; do not ask the user to select it manually when the tool can set it.

Before declaring the preferred top-level surface unavailable, exhaust the host's callable-tool discovery and distinguish an absent tool from a failed invocation or invalid worktree argument. When the tool is genuinely not exposed, automatically fall back to an available root `subagent`, then to `sequential`; do not ask the user to repeat authorization. Keep the plan's `top_level_tasks=true`, record actual delegation and `fallback_reason=top-level-tool-unavailable`, and never claim that the fallback created sidebar-visible tasks. If the fallback surface cannot satisfy a hard model lock such as Luna-only, pause that lane instead of bypassing the lock. `--allow-delegation-fallback` remains only as a compatibility override for custom profiles that explicitly retain approval-required fallback.

Pass `--goal-authorized` only when the user explicitly requested a Goal. Goal authorization in the plan and command must both be true.

The run directory contains:

- `run-state.json`: current state, tasks, assignments, commits, and checks;
- `events.jsonl`: append-only transition history;
- `plan.json`: the exact validated plan snapshot.

Update it after dispatch, material progress, commit, defect return, integration, acceptance, pause, and completion. If usage telemetry is absent, leave it as `unavailable`.

## 4. Prepare execution safely

Before writes:

1. verify repository root, HEAD, status, and worktree inventory;
2. preserve user changes and protected sources;
3. give each independent writer a clean worktree and `codex/*` branch when the repository and host support it;
4. assign exclusive paths; shared files belong to the integration owner;
5. make each task brief self-contained, including base commit, scope, exclusions, tests, and completion report.

Do not delete, reset, clean, force-push, rewrite history, mutate credentials, or perform live external writes without the user's authority. See [references/git-and-ownership.md](references/git-and-ownership.md).

## 5. Execute continuously

For every ready task:

1. dispatch according to dependencies, ownership, capability tier, and the requested concurrency target subject to actual runtime constraints;
2. persist task/thread ID, host ID when available, worktree, branch, base commit, requested/actual model, and reasoning;
3. monitor with the host's wait/status tools rather than declaring success at dispatch;
4. require real code or project artifacts, focused tests, and an auditable commit when Git is in scope;
5. return implementation defects to the original owner instead of silently repairing them in acceptance;
6. continue independent lanes while one lane is blocked;
7. keep the user informed during long work without treating status messages as completion.

The first vertical slice must run through every layer required by that task (for example domain -> service -> API -> client -> UI -> focused test), but it remains only an implementation checkpoint. Complete all journeys, failure paths, recovery semantics, permissions, isolation, and final deliverables in the plan.

Use focused tests within owner lanes. Reserve broader suites for phase integration and critical milestone acceptance unless the plan explicitly requires otherwise.

## 6. Resource-mode behavior

- `premium`: assign frontier reasoning to architecture, authority, and acceptance; use independent review for high-risk decisions.
- `balanced`: mix strong planning with efficient bounded implementation.
- `economy`: prefer Luna/fast workers, concurrency 1-2, decompose before escalation, and ask before crossing the escalation policy.
- `speed`: maximize safe independent lanes while preventing shared-file writers.
- `all-luna`: hard-lock all roles to the Luna family with high reasoning.
- `mad-luna`: hard-lock all roles to Luna, request maximum supported reasoning and maximum safe concurrency, and add an independent Luna verifier to high-risk milestones.
- `custom`: follow exact role assignments and limits.

For `all-luna + speed`, retain the `all-luna` hard model lock and roles, apply the `speed` modifier, and default to concurrency 6 subject to runtime constraints. If the user supplied another positive concurrency value, preserve it. Do not resolve the composition as the mixed-model `speed` profile.

`mad-luna` is not permission to exceed host capacity, user budget, repository safety, or external-action boundaries. No mode may lower the completion standard.

## 7. Integrate once, accept independently

At each planned phase boundary:

1. verify owner commits and changed-path ownership;
2. integrate field-by-field when shared contracts changed;
3. run the milestone's proportional checks;
4. use one independent acceptance pass for the user journey and authority boundaries;
5. send defects back to the owning lane and re-run only affected acceptance plus required regression checks;
6. establish the accepted common baseline, then immediately release newly unblocked work.

Do not multiply governance into implementation review, integration review, acceptance, promotion, registry revision, and repeated re-review unless the plan or release risk truly requires those distinct actions. Read [references/integration-and-acceptance.md](references/integration-and-acceptance.md).

## 8. Recover without redispatching completed work

On compaction, restart, timeout, or task failure:

1. load `run-state.json` and verify it with `validate_run.py`;
2. compare recorded commits and worktrees with current Git evidence;
3. mark stale running tasks `blocked` or return them to `ready` with an event;
4. resume incomplete owners; never recreate completed owners merely to recover context;
5. re-check external approvals because authorization can expire or be one-shot.

Use:

```bash
python scripts/validate_run.py RUN_DIR --pretty
python scripts/render_status.py RUN_DIR
```

Read [references/recovery.md](references/recovery.md) for transition rules.

## 9. Completion standard

Finish only when:

- every required task is completed or explicitly user-approved as skipped;
- implementation and integration commits are recorded and reachable;
- planned verification passes or an exact external blocker is documented;
- independent acceptance passes where required;
- no unauthorized live or destructive action occurred;
- the run's full completion standard is satisfied.

Mark the run complete only after `validate_run.py` accepts it. The final response must state completed changes, files/commits, checks, actual resource/capability tier, remaining blockers, and the next useful step.

## Non-negotiable behavior

- Goal creation remains opt-in; top-level task authorization is always true in All in Luna plans and is never derived from Goal state.
- A model lock is a hard constraint, not a preference.
- Requested settings never substitute for actual runtime evidence.
- Cost and token values remain `unavailable` when the platform does not expose them.
- Economy changes resources, not scope.
- Acceptance does not silently modify implementation.
- No old UI, architecture, or fixture may replace the plan's canonical implementation merely because it is easier.
