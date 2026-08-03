# Delegation and model evidence

## Capability tiers

- `top-level-task`: a user-visible task/thread with its own lifecycle. Create only when the user explicitly requested it.
- `subagent`: a bounded child inside the current task tree.
- `sequential`: current-agent execution.

Use the highest tier that is both available and authorized—not the highest tier imagined by the plan. Record capability discovery in run state.

## Codex App discovery order

On Codex App, inspect `codex_app__create_thread` before concluding that a model is unavailable. Its tool declaration is the authoritative catalog for user-visible top-level tasks and may include models (for example `gpt-5.6-luna`) that are absent from the subagent tool declaration. Use `codex_app__list_projects` before creating a project-scoped task, then call `codex_app__create_thread` with the exact `model` and supported `thinking` requested by the profile.

Keep catalogs scoped by delegation surface. A model unavailable to subagents but available to top-level tasks is `available_on_top_level`, not globally unavailable. If the plan lacks top-level-task authorization, ask for that authorization; do not silently downgrade and do not call the model missing.

Goal authorization is independent. The phrase “do not create a Goal” changes only `goal_creation`; it does not set `top_level_tasks=false` unless the user separately denied task/thread creation.

## Stale plan authorization

The current explicit user message can supersede an older plan's authorization booleans. Preserve the old plan as history and create a new validated execution revision with `prepare_execution_plan.py`; never edit the source snapshot in place. If the user currently authorizes top-level tasks, record that in the revision and use the top-level catalog. Do not silently fall back to subagents because the old snapshot predates that authorization.

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
