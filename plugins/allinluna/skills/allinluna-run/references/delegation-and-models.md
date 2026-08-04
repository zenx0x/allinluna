# Delegation and model evidence

## Capability tiers

- `top-level-task`: a user-visible task/thread with its own lifecycle. Every All in Luna plan authorizes this tier.
- `subagent`: a bounded child inside the current task tree.
- `sequential`: current-agent execution.

Use the highest tier that is both available and authorized—not the highest tier imagined by the plan. Record capability discovery in run state.

All built-in profiles make `top-level-task` the independent primary Coordinator's preferred delegation, and every All in Luna plan records `top_level_tasks=true`. Sponsor-thread or Coordinator-thread product implementation is not the default. Subagent and sequential execution are runtime fallbacks, not substitutes for owner lanes and not reasons to rewrite plan authorization.

Use root-level fallback automatically only after verified host unavailability of the top-level task tool, or after the user declines the required Git bootstrap. For verified tool absence, try `subagent` before `sequential` and record `top-level-tool-unavailable`; do not request confirmation again. Do not treat an undiscovered tool, invalid task parameters, or a failed first invocation as proof of absence. A fallback that violates a hard family/model lock remains forbidden and must pause rather than silently switch models.

Top-level owners may use bounded internal subagents without returning ownership to a Coordinator. Their task brief must explicitly allow this and preserve the owner's exclusive paths, base commit, model policy, reasoning ceiling, budget, tests, and reporting contract. Internal subagents cannot independently satisfy the owner task; the owner must integrate and verify their work. Hard family locks apply recursively.

At high concurrency the primary Coordinator may delegate disjoint task-ID shards to one level of child Coordinators. A child Coordinator is a user-visible top-level task, not an implementation owner and not a substitute for owner work. Its brief contains its shard, slot limit, forbidden global changes, and return protocol.

## Codex App discovery order

On Codex App, inspect `codex_app__create_thread` before concluding that a model is unavailable. It is commonly a deferred tool: search `functions.exec`'s `ALL_TOOLS` catalog or the host's tool-search facility even when it is absent from the short initial list. Its declaration is the authoritative catalog for user-visible top-level tasks and may include models (for example `gpt-5.6-luna`) that are absent from the subagent declaration. Use `codex_app__list_projects` before creating a project-scoped task, then call `codex_app__create_thread` with the exact `model` and supported `thinking` requested by the profile. A discovered declaration means the capability exists; do not fall back just because the tool was lazily loaded.

Parallel top-level implementation uses Git worktree isolation. For a non-Git project, request one explicit authorization covering any required Git installation, `git init`, preservation of existing files, local identity setup if needed, and an initial baseline commit. After setup, verify readiness and create worktree tasks. If the user refuses, use ordinary subagents or sequential execution and record `user-declined-git-bootstrap`; keep the plan authorization true.

Keep catalogs scoped by delegation surface. A model unavailable to subagents but available to top-level tasks is `available_on_top_level`, not globally unavailable. If an older plan lacks top-level-task authorization, create an execution revision with it set true; do not silently downgrade and do not call the model missing.

Goal authorization is independent. The phrase “do not create a Goal” changes only `goal_creation`; every All in Luna plan still records `top_level_tasks=true`.

## Stale plan authorization

Preserve an older plan as history and create a new validated execution revision with `prepare_execution_plan.py`; never edit the source snapshot in place. The revision always records `top_level_tasks=true` and uses the top-level catalog. Do not silently fall back to subagents because the old snapshot predates this invariant.

## Task brief

Every delegated task must include:

- objective and complete acceptance conditions;
- absolute repository/worktree and branch;
- exact base commit;
- owned paths and forbidden paths;
- required sources and applicable instructions;
- requested model/reasoning and fallback policy;
- targeted checks;
- external-action prohibition or authorization;
- required final report and commit evidence.

Do not rely on the coordinator's implicit chat context.

## Requested versus actual

For every assignment record:

- `requested.model`, `requested.reasoning`, `requested.delegation`;
- `actual.model`, `actual.reasoning`, `actual.delegation`;
- `resolution`: exact, fallback, unresolved, or unavailable;
- source of evidence when exposed by the host.

If the platform cannot report an actual field, store `unavailable`. The task may still run if policy allows, but the final report must not claim verification of that setting.

## Model selection

Match capability to risk:

- authority and irreversible semantics: strongest reasoning available;
- complex architecture or urgent blockers: strong, responsive model;
- bounded implementation: efficient engineering model;
- scans, bulk validation, and repetitive fixes: fast model with deterministic checks;
- independent acceptance: capable model not involved in implementation.

Do not mechanically assign the most expensive model to every task unless the user deliberately selected a mode such as `mad-luna`.
