# Development plan format

The canonical artifact is JSON conforming to `assets/development-plan.schema.json`.

## Top-level fields

- `schema_version`: format compatibility, currently `1.0`.
- `plan_id`: stable lowercase identifier.
- `title`: human-readable project objective.
- `mode`: `plan-only`, `execute-ready`, or `goal-ready`.
- `objective`: concise requested outcome.
- `completion_standard`: measurable conditions for whole-plan completion.
- `repository`: existing/greenfield mode, roots, revision evidence, and dirty-state notes.
- `authorizations`: explicit action permissions.
- `resource_policy`: profile, locks, fallback, concurrency, and budget.
- `tasks`: dependency graph and ownership.
- `milestones`: phase-level integration and acceptance.
- `assumptions`: progress-enabling assumptions.
- `unknowns`: facts requiring runtime verification or user input.

## Task fields

Every task has:

- `id`, `title`, `phase`, `description`;
- `dependencies` containing existing task IDs;
- `ownership.paths` and `ownership.exclusive`;
- `role` and `resource_class`;
- `deliverables` and `verification`, both non-empty;
- `external_side_effects`, even when empty;
- `acceptance_required`.

Paths use repository-relative forward slashes. Directory ownership ends in `/`; glob ownership may use `*` or `**`. Concurrent tasks with overlapping exclusive ownership are invalid unless ordered by dependencies.

## Milestones

A milestone names the included tasks, integration evidence, acceptance evidence, and the tasks it unlocks. Do not create a milestone solely to restate task completion.

## Stable identity

Do not change task IDs after execution begins. Split a task by adding new child IDs and an event explaining the supersession. This allows run recovery to distinguish old completion from new work.
