---
name: allinluna-plan
description: Turn an idea or existing plan into a complete, machine-checkable All in Luna plan while preserving scope, ownership, authorization, and the one-card user flow. Use parallel-only for a complete third-party plan; Goal creation remains opt-in.
---

# All in Luna - Plan

Plan the full requested outcome so Run can execute it. Planning is not
completion. The user-facing path remains:

```text
one-sentence request or existing plan
  -> one resource confirmation card
  -> Coordinator
  -> dependency waves
  -> result
```

The delivery-mode definitions (`quick`, `standard`, `full`) and retained
`fast`/`ultra-fast`/`all-luna` choices are shared with Run in
[`references/user-flow.md`](../allinluna-run/references/user-flow.md). Do not
make a user repeat a resource choice during Run.

## Declare the planning mode

State one mode at the start:

- `plan-only`: inspect and produce a plan; do not implement;
- `execute-ready`: produce a validated plan intended for `$allinluna-run`;
- `goal-ready`: prepare orchestration metadata only when the user explicitly
  authorized a Goal;
- `parallel-only`: preserve a supplied complete plan and normalize dependencies,
  ownership, resources, recovery, and dispatch without redesigning direction.

If the user requests planning and implementation together, validate the plan
then use Run for implementation. Do not call a plan, first slice, or first
passing test complete.

## Build the plan contract

1. Preserve the objective, full completion standard, inclusions, exclusions,
   assumptions, unknowns, and any protected/dirty paths.
2. Record independent authorizations for implementation writes, Git
   branch/worktree/commit actions, Goal creation, user-owned top-level tasks,
   destructive operations, live external mutation, and publication.
3. Select `quick`, `standard`, or risk-reserved `full`. Do not add default
   governance lanes outside an explicit `full` evidence boundary; the lean
   runtime does not materialize separate CounterPilot or Acceptance lanes and a
   mode must never shrink scope.
4. Create substantive dependency tasks with exclusive ownership, dependencies,
   complete deliverables/failure paths/recovery, focused verification, resource
   policy, external-action policy, and a measurable stop boundary.
5. Keep Sponsor, separate Coordinator, and Owner roles distinct. Every All in
   Luna plan records `top_level_tasks=true`; Goal authorization is independent.

Read the [planning contract](references/planning-contract.md) and
[resource planning](references/resource-planning.md) for the detailed fields.

## Validate and hand off

Use the checked-in example/schema as the artifact contract and validate before
handoff:

```bash
python scripts/validate_plan.py path/to/plan.json --pretty
```

Fix validation errors rather than merely reporting them. The handoff states the
objective and full completion standard, repository/base evidence, task order
and safe parallel lanes, selected mode/profile, top-level-task and Goal
authorizations, external actions needing later authorization, exact plan path,
and validation result. Start execution with `$allinluna-run` only after the
user's one resource confirmation.

## Non-negotiable boundaries

- Do not create a Goal because the work is large.
- Do not downgrade complete delivery into a demo, schema-only plan, or smoke
  test.
- Do not fabricate model availability, usage, cost, or acceptance evidence.
- Do not authorize destructive, credentialed, live, or publication actions in
  the plan itself.
- Keep detailed schema/field semantics in the routed [plan format](references/plan-format.md)
  and repository variants in [repository modes](references/repository-modes.md).
