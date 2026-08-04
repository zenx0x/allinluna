# Planning contract

Use this contract for every plan, regardless of project size. The planning
entry shares the ordinary user path with Run:

```text
one-sentence request or existing plan -> one resource card -> Coordinator -> dependency waves -> result
```

## Required truths

The plan preserves the user's objective and full completion standard, explicit
inclusions/exclusions, repository instructions, protected/dirty paths,
authority boundaries, and unknowns/assumptions. Implementation, mechanical
Integration, and publication remain distinct states; the lean runtime keeps
Acceptance/CounterPilot as legacy plan metadata rather than materializing extra
lanes.

Select `quick`, `standard`, or `full` using the risk-adaptive rules in
[`allinluna-run`'s user-flow reference](../../allinluna-run/references/user-flow.md).
Do not add extra governance merely because the plan is non-trivial; `full` is an
explicit upgrade for high-risk, large cross-contract, or scientific-authority
evidence, while the lean runtime still materializes only the required
Coordinator, Owner, and mechanical Integration work.

## Independent authorizations

Record separate booleans for implementation writes, Git branch/worktree/commit
operations, Goal creation, user-owned top-level tasks, destructive filesystem or
Git operations, live external mutation, and publication/deployment. Permission
for one state never implies another. “Do not create a Goal” does not deny
top-level tasks; every All in Luna plan still records `top_level_tasks=true`.

## Completeness

Task deliverables describe the requested capability, failure paths, recovery,
permissions, isolation, tests, and artifacts—not a demo or first-slice
substitute. A first vertical slice is a progress checkpoint only.

## Questions and handoff

Ask only when two plausible answers materially change architecture,
authorization, or scientific/legal authority. Otherwise record an assumption
and proceed. A capable Owner must be able to execute from the plan, named
repository/base, owned paths, sources, and checks without hidden chat context.
