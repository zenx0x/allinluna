# Delegation and model evidence

## Capability tiers

- `top-level-task`: a user-visible task/thread with its own lifecycle; use for
  the separate Coordinator and substantive Owner lanes when exposed and
  authorized.
- `subagent`: a bounded child inside an Owner; it inherits the Owner's paths,
  base, model policy, reasoning ceiling, budget, tests, and reporting contract.
- `sequential`: a recorded runtime fallback when the preferred surface is not
  available; it is not presented as parallel execution.

The selected delivery mode determines how many Owner lanes are needed. It does
not authorize a hidden Sponsor implementation lane, micro-task inflation, or a
CounterPilot outside the `full` risk boundary.

## Required task evidence

Every Coordinator/Owner brief includes the complete objective and acceptance
conditions, absolute repository/worktree and branch, exact base commit, owned
and forbidden paths, applicable sources, requested model/reasoning and fallback
policy, focused checks, external-action policy, and final report fields.

Owner subagents cannot independently satisfy an Owner task. The Owner must
integrate their work and verify the complete lane. Hard family locks apply
recursively.

## Codex App discovery order

On Codex App, discover the user-visible top-level-task tool before concluding
that a model or delegation surface is unavailable. Project-scoped worktree
tasks require project discovery first. Top-level and subagent catalogs may
expose different models; record the catalog source and keep the requested,
resolved, actual, and fallback fields distinct.

## Requested versus actual

For every assignment record:

- `requested.model`, `requested.reasoning`, `requested.delegation`;
- `actual.model`, `actual.reasoning`, `actual.delegation`;
- a resolution such as `exact`, `fallback`, `unresolved`, or `unavailable`;
- the host/tool receipt that proves the actual value, when exposed.

Do not call an unavailable receipt a pass. A hard model-family lock may pause
the affected lane but may not silently switch families.
