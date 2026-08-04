# Development plan format

The canonical JSON artifact conforms to `assets/development-plan.schema.json` schema version `2.0`.

## Top-level fields

- `mode`: `plan-only`, `execute-ready`, or `goal-ready`.
- `execution_style`: `managed` or `parallel-only`.
- `risk_level`: `low`, `medium`, `high`, or `critical`.
- `objective` and `completion_standard`: full requested outcome and measurable finish.
- `repository`: existing/greenfield roots, revision, and dirty-state evidence.
- `authorizations`: implementation, Git, Goal, top-level tasks, external actions.
- `orchestration`: Sponsor, separate primary Coordinator, optional child-coordinator strategy, shard size, and high-concurrency review choice.
- `resource_policy`: profile/modifiers, hard locks, fallback, desired concurrency 1–64, and budget.
- `tasks`, `milestones`, `stop_boundary`, `assumptions`, and `unknowns`.

Every plan keeps `top_level_tasks=true`; actual runtime fallback is recorded separately. Desired concurrency never becomes 1 merely because only one task is currently ready.

## Task fields and ownership

Every task has stable identity, dependencies, exclusive owned paths, role/resource class, deliverables, verification, validation level, and external side effects. Concurrent exclusive ownership may not overlap unless dependency ordering prevents simultaneous writes.

In `parallel-only`, preserve the user's approved plan direction. Normalize dependencies and ownership only enough for safe execution; do not invent product redesign. Mechanical Integration remains risk-adaptive, while the lean runtime does not materialize separate Acceptance or CounterPilot lanes.

At desired concurrency 16 or above, `high_concurrency_review` must be explicitly `accepted` or `declined`; accepted review requires a concrete decomposition model.
