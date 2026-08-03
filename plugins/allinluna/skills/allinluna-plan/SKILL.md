---
name: allinluna-plan
description: Plan software development from an existing repository or a greenfield idea with explicit scope, dependencies, file ownership, verification, model tiers, reasoning effort, delegation, concurrency, and budget policy. Use when the user asks to plan, decompose, parallelize, estimate agent resources, prepare a long-running implementation, compare premium/balanced/economy/speed/Luna modes, or create an executable handoff. This skill plans only unless execution is explicitly requested, and it never creates a Goal implicitly.
---

# All in Luna - Plan

Turn a user objective into a complete, machine-checkable development plan. Preserve the user's completion standard: a resource profile may change allocation and timing, but never silently shrink scope.

## Start by declaring the mode

Tell the user this skill is being used and state the planning mode:

- `plan-only`: inspect and produce a plan; do not implement.
- `execute-ready`: produce a plan intended for `$allinluna-run`.
- `goal-ready`: prepare persistent orchestration metadata, but create a Goal only when the user explicitly requested one.

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

Use the smallest topology that can complete the full scope:

- **Small:** current task, ordered steps, focused checks.
- **Medium:** current coordinator plus a few independent subagents when available.
- **Large:** independent owner lanes, one phase integration, and one independent acceptance. All in Luna plans always authorize user-facing top-level Codex tasks; root-level subagents or sequential execution are runtime fallbacks only and never change the plan authorization.

Every All in Luna plan must set `top_level_tasks=true`. Never emit `false`, including for plan-only, non-Git, greenfield, small, tightly coupled, or single-lane work. Design independent substantive owner lanes as user-visible top-level Codex tasks and allow each owner bounded internal subagents. A tightly coupled project may produce only one top-level owner, but the authorization remains true.

Track Goal and task authorization independently. “Do not create a Goal” sets only `goal_creation=false`; it must not be copied into `top_level_tasks=false`. All in Luna always records top-level task authorization as true.

Parallelize only work that is independent in both dependencies and writable file ownership. A task with shared files must depend on the owning implementation or be assigned to the integration lane.

Avoid a separate task for every registry update, micro-fix, plan restatement, promotion, or repeated audit. Prefer:

```text
parallel implementation -> one phase integration -> one milestone acceptance
```

## 4. Select a resource mode

Use `balanced` when the user has no preference. Available profiles are `premium`, `balanced`, `economy`, `speed`, `all-luna`, `mad-luna`, and `custom`.

Allow a model policy and an execution strategy to compose. Interpret `all-luna + speed` as base profile `all-luna`, modifier `speed`, Luna hard lock retained for every delegated role, and desired concurrency 6 with maximum-safe independent scheduling. Record `resource_policy.modifiers: ["speed"]`; never replace the Luna hard lock with the mixed-model `speed` profile.

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
- external side effects and required authorization;
- whether independent acceptance is required.

Include the first verifiable vertical slice when useful, but label it a progress checkpoint, not scope reduction, architecture freeze, or completion.

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
- Do not authorize destructive or live external actions through the plan itself.
- Do not use economy mode to reduce requirements.
