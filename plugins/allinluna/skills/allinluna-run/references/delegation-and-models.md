# Delegation and model evidence

## Capability tiers

- `top-level-task`: a user-visible task/thread with its own lifecycle. Create only when the user explicitly requested it.
- `subagent`: a bounded child inside the current task tree.
- `sequential`: current-agent execution.

Use the highest tier that is both available and authorized—not the highest tier imagined by the plan. Record capability discovery in run state.

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
