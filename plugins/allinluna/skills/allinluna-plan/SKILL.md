---
name: allinluna-plan
description: Plan software development or normalize an existing user plan for parallel execution with explicit Sponsor, independent Coordinator, optional child coordinators, CounterPilot, ownership, models, reasoning, concurrency, and budget controls. Use for repository or greenfield planning, high-concurrency decomposition, fast/ultra-fast execution preparation, or a plan intended for All in Luna Run. This skill plans only unless execution is explicitly requested and never creates a Goal implicitly.
---

# All in Luna - Plan

Turn a user objective into a complete, machine-checkable development plan. Preserve the user's completion standard: a resource profile may change allocation and timing, but never silently shrink scope.

## Start by declaring the mode

Tell the user this skill is being used and state the planning mode:

- `plan-only`: inspect and produce a plan; do not implement.
- `execute-ready`: produce a plan intended for `$allinluna-run`.
- `goal-ready`: prepare persistent orchestration metadata, but create a Goal only when the user explicitly requested one.
- `parallel-only`: preserve a plan supplied by the user or another planning skill and normalize only
  dependencies, ownership, resources, and dispatch metadata; do not redesign product direction.

If the user requests planning and implementation together, finish and validate the plan, then invoke `$allinluna-run`. Do not treat plan completion as implementation completion.

## 1. Establish the completion contract

Extract the objective, inclusions, exclusions, authority boundaries, destructive or live external actions, expected deliverables, and completion evidence. Distinguish:

- requested scope from the first implementation checkpoint;
- implementation from integration and independent acceptance;
- known facts from assumptions and questions;
- reversible local work from credentialed, destructive, or live mutations.

Ask only when an unresolved choice would materially change the target architecture or authorization. Otherwise make and label a reasonable assumption.

Read [references/planning-contract.md](references/planning-contract.md) for the mandatory plan contract.

## 2. Inspect without wandering

For an existing project:

1. Read applicable `AGENTS.md` files and repository instructions first.
2. Inspect Git root, branch, HEAD, status, worktrees, manifests, test commands, and relevant architecture.
3. Preserve dirty or protected content. Never reset, clean, or normalize it to simplify planning.
4. Read only code, contracts, tests, and semantic sources needed for the requested implementation.
5. Stop repeated or unbounded corpus traversal once enough evidence exists to own the plan.

Use the deterministic inventory helper when useful:

```bash
python scripts/inspect_project.py PATH --pretty
```

For a new idea, do not invent repository facts. Record `repository.mode = greenfield`, derive the initial architecture from requirements, and include an explicit bootstrap task. See [references/repository-modes.md](references/repository-modes.md).

## 3. Choose scale and execution topology

Use risk-adaptive topology. The user conversation is always the Sponsor; Run creates a separate
top-level Coordinator. The Sponsor discusses direction, approvals, scope, and resources but does
not implement or dispatch ordinary owners.

- **Low risk / small:** Coordinator plus owner; integration is a proportional coordinator check.
- **Medium:** independent owners plus one integration; CounterPilot at material milestones.
- **High/critical:** owners, one integration, independent acceptance, and CounterPilot.
- **Parallel-only:** preserve the user's plan and dispatch it; do not add governance layers unless
  the supplied plan, authority risk, or live/destructive action requires them.

Every All in Luna plan must set `top_level_tasks=true`. Never emit `false`, including for plan-only, non-Git, greenfield, small, tightly coupled, or single-lane work. Design independent substantive owner lanes as user-visible top-level Codex tasks and allow each owner bounded internal subagents. A tightly coupled project may produce only one top-level owner, but the authorization remains true.

Every plan records `sponsor_role=user-conversation`,
`coordinator_role=separate-top-level-task`, `coordinator_product_implementation=forbidden`,
`owner_delegation=top-level-task`, and `owner_subagents=allowed-bounded`. Never merge Sponsor and
Coordinator. For 16+ desired concurrency or many independent lanes, use hierarchical coordination:
the primary Coordinator creates child coordinators, each owning a disjoint task shard.

Track Goal and task authorization independently. “Do not create a Goal” sets only `goal_creation=false`; it must not be copied into `top_level_tasks=false`. All in Luna always records top-level task authorization as true.

Parallelize only work that is independent in both dependencies and writable file ownership. A task with shared files must depend on the owning implementation or be assigned to the integration lane.

Defaults are economy 4, balanced 8, premium 12, speed 12, fast 24, ultra-fast 48,
all-luna 8, and mad-luna 24. Support explicit presets 8, 12, 16, 24, 48, and 64 plus any
value from 1 to 64. These are desired targets; the host cap, DAG, ownership, and machine capacity
determine effective concurrency.

For a greenfield or non-Git root, record Git bootstrap as a pre-dispatch coordinator action,
not as product implementation. It checks readiness and requests the combined Git installation,
repository initialization, baseline commit, and worktree authorization. Subsequent implementation
owners remain top-level tasks. If Git preparation is declined at runtime, use the documented
ordinary fallback without rewriting the plan topology.

Avoid a separate task for every registry update, micro-fix, plan restatement, promotion, or repeated audit. Prefer:

```text
parallel implementation -> risk-required integration/acceptance -> completion standard
```

## 4. Select a resource mode

Use `balanced` when the user has no preference. Available profiles are `premium`, `balanced`,
`economy`, `speed`, `fast`, `ultra-fast`, `all-luna`, `mad-luna`, and `custom`.

At the beginning of planning, if the user has not specified resource mode or desired concurrency,
offer one compact non-blocking choice: balanced 8, economy 4, speed 12, fast 24, ultra-fast 48,
or custom up to 64. If unanswered, continue with balanced 8. For 16+, ask once whether a strong
planner should review dependencies, conflicts, ownership, and shard boundaries. Record accepted or
declined; if accepted, record the actual decomposition model. Do not ask again during Run.

Decompose for parallel execution by default. Identify every substantively independent owner lane whose dependencies and writable paths do not overlap, and represent those lanes as separate top-level tasks. Schedule all dependency-ready, conflict-free owners concurrently up to the desired target. Do not require the user to enumerate lanes, and do not manufacture micro-tasks merely to fill the target.

Allow model policy and velocity to compose. `all-luna + fast` and `all-luna + ultra-fast` retain
the Luna lock while adopting 24 or 48 desired concurrency and hierarchical scheduling. A custom
value overrides only the numeric target, not the model lock.

Keep these controls independent:

1. model or model family;
2. reasoning effort;
3. delegation tier;
4. concurrency;
5. token, credit, time, or monetary budget.

Resolve logical model tiers against models actually exposed by the host at execution time. Never claim a requested model was used without runtime evidence. A hard model lock cannot silently upgrade or substitute. `mad-luna` means Luna-only, maximum supported reasoning, maximum safe concurrency, and independent Luna verification for high-risk work; it still respects host limits and user budget caps.

Read [references/resource-planning.md](references/resource-planning.md) before assigning roles.

## 5. Build the dependency graph

Each task must include:

- stable ID, title, phase, and concrete description;
- dependencies and why they exist;
- owned paths or a precise non-file scope;
- role and resource class;
- complete deliverables, not a sample or MVP substitute;
- focused verification;
- validation level: owner `focused`, integration `cross-lane`, acceptance `milestone`, or explicitly justified `full`;
- external side effects and required authorization;
- whether independent acceptance is required.

Include the first verifiable vertical slice when useful, but label it a progress checkpoint, not scope reduction, architecture freeze, or completion.

Require governance proportionally: managed medium/high/critical plans use integration; high,
critical, authority-sensitive, security-sensitive, or live/destructive plans use independent
acceptance. Low-risk and parallel-only plans do not mechanically add these layers. Record an
explicit `stop_boundary` in every plan.

For large work, separate scientific/product authority decisions from mechanical engineering. Use the strongest available reasoning for irreversible semantic decisions and cost-efficient models for bounded, easily tested work.

## 6. Emit and validate the plan

Use [assets/development-plan.example.json](assets/development-plan.example.json) as the editable starting point and [assets/development-plan.schema.json](assets/development-plan.schema.json) as the public contract. The human explanation and JSON plan must agree.

Validate before handoff:

```bash
python scripts/validate_plan.py path/to/plan.json --pretty
```

Fix errors rather than merely reporting them. Warnings may remain only when they identify an honest runtime unknown such as model availability or usage telemetry.

The final planning response must state:

- objective and full completion standard;
- repository/greenfield evidence;
- task dependency order and safe parallel lanes;
- selected resource mode plus overrides;
- invariant top-level task authorization (`top_level_tasks=true`, `top_level_tasks_basis=allinluna-default`);
- Goal authorization (`false` unless explicit);
- external actions needing later confirmation;
- exact plan artifact path and validation result;
- the command or prompt that starts `$allinluna-run`.

See [references/plan-format.md](references/plan-format.md) for field semantics.

## Non-negotiable behavior

- Do not create a Goal merely because the work is large.
- Do not turn a broad implementation request into a plan-only answer.
- Do not call a smoke test, scaffold, first slice, or partial lane complete.
- Do not fabricate model availability, token use, elapsed cost, or expected dollars.
- Do not require multi-agent work when the host lacks it; record the fallback topology.
- Do not make the Sponsor the Coordinator. Run must create the separate Coordinator before owners.
- Do not call a non-Git directory, empty greenfield, shared-file dependency, or a single ready task a reason for `concurrency: 1` or current-thread implementation.
- Do not authorize destructive or live external actions through the plan itself.
- Do not use economy mode to reduce requirements.
