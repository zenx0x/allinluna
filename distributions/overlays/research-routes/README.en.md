# Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

**Put a question into Research Routes and get a route-neutral evidence map: Claims, Evidence, contradictions, failure regimes, and unknowns stay separate, and the next step is limited to reversible exploration.**

Research Routes is an independently installable Codex research distribution that can also coexist with All in Luna. Use it to understand a problem and compare routes first; use All in Luna for complete software delivery after the scope is explicitly authorized.

## Understand it in 30 seconds

It is not a jump page, and it does not disguise a terrain map as an implementation plan. Keep routes A/B, positive and negative evidence, and failure regimes visible until there is enough basis to choose the next probe. AI inference does not become experiment authorization, implementation order, HumanDecision, or canonical state automatically.

## Three entry points

1. **`$research-routes-plan`**: define the problem boundary, candidate routes, Claims/Evidence structure, and unknowns.
2. **`$research-routes-explore`**: compare routes, preserve positive, negative, and contradictory evidence, identify failure regimes, and design a reversible probe.
3. **`$research-routes-run`**: run a probe only within explicitly authorized research scope, record its result, and preserve rollback boundaries; hand a bounded evidence package to All in Luna when product implementation is ready.

## The real experience and a small example

```text
Use Research Routes to compare sparse retrieval and knowledge-graph retrieval for this question:
Preserve Claims, Evidence, unknowns, contradictory results, and failure regimes; do not select a route prematurely. Design only one reversible next probe.
```

The expected sidebar result is multiple route Owners comparing routes and evidence in parallel, with an independent read-only CounterPilot challenge. The output is a terrain map and a reversible next probe, not an unauthorized conclusion.

## Shortest install and first use

From the source repository, choose installation from a local path in Codex Plugins and select the repository root; its marketplace lists both All in Luna and Research Routes. If you received a standalone distribution, select that package root; its actual plugin root is `plugins/research-routes/`, and you do not need to run the source-repository builder again inside it.

The plugin entry points live under `plugins/research-routes/skills/`, the manifest is `plugins/research-routes/.codex-plugin/plugin.json`, and shared contracts live under `plugins/research-routes/shared/`.

## Research boundary

A terrain map is not experiment authorization, implementation order, HumanDecision, or canonical-state promotion. Each Claim should point to Evidence; Evidence keeps its polarity; and the next probe must explicitly set `reversible: true`. The shared runtime fails closed on boundary violations.

When product implementation is ready, hand off a bounded route/evidence package and state which facts remain unknown; do not treat the research map as an implementation plan.

## Good fits

- Compare two scientific routes without selecting one prematurely.
- Preserve contradictory results and failure regimes instead of retaining only positive conclusions.
- Choose a low-cost, reversible probe that can distinguish competing assumptions before implementation.
- Build a traceable route map from software options, paper directions, experiment records, or existing material.

## Frequently asked questions

**Will Research Routes choose a route for me?** No. It keeps differences, evidence, and unknowns visible; a human choice still requires an explicit HumanDecision.

**Can it edit product code directly?** Research execution preserves route boundaries. When software delivery is ready, hand the bounded evidence package to All in Luna.

**Can I use only All in Luna?** Yes. All in Luna is the fit when the outcome is clear and you need complete development concurrency and delivery.

## Advanced contracts and license

See `plugins/research-routes/skills/research-routes/SKILL.md`, `plugins/research-routes/skills/research-routes-run/SKILL.md`, `plugins/research-routes/shared/`, and `plugins/research-routes/.codex-plugin/plugin.json` for the detailed boundary contracts.

Apache License 2.0. See `LICENSE`.
