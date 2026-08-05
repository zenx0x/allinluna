---
name: research-routes
description: Compare multiple research routes with explicit Claims, Evidence, unknowns, failure regimes, and reversible probes. Use when a person needs route-neutral exploration before implementation, experiment authorization, HumanDecision, or canonical-state promotion.
---

# Research Routes

Keep a route-neutral terrain map separate from decisions and execution.

1. Inventory candidate routes and source-backed evidence.
2. Keep observations, interpretations, Claims, Evidence, contradictions, and unknowns distinct.
3. Compare assumptions, mature-method comparators, costs, failure regimes, and falsifiable probes.
4. Preserve every evidence polarity, including `support`, `counter`, `null`, `boundary`, `conflict`, `failure`, `context`, `mixed`, and `unknown`.
5. Choose only a reversible next probe when the user authorizes exploration.
6. Record `FailureRecord`, `RewindProposal`, `Lesson`, `ReopenedProblem`, and canonical downgrade as append-only research records; never erase the failed path.
7. State that a terrain map is not a route choice, experiment authorization, implementation, HumanDecision, or canonical-state promotion.

The Pack runtime lives under `runtime/research_routes_runtime/` and compiles
`research-pack/v1`. It uses only generic host, artifact, snapshot, decision,
and promotion boundaries supplied by All in Luna; it does not add research
state to Core. A route authorization must name a confirmed HumanDecision, and
canonical promotion needs its own explicit decision scope.

When a route is authorized for product work, hand off its bounded decision and evidence package to `$allinluna`; do not silently turn research notes into implementation tasks.
