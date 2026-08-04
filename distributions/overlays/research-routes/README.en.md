# Research Routes

**One-line outcome: map multiple research routes against evidence, preserve Claims, contradictions, failure regimes, and unknowns, and choose only a reversible next exploration step.**

Research Routes is an independent Codex research distribution, not a jump page and not a way to disguise a terrain map as an implementation plan. It shares core schemas, control plane, resources, recovery, routing, and tests with All in Luna; All in Luna handles development concurrency, while Research Routes handles multi-route evidence and reversible exploration.

## Three entry points

1. **`$research-routes-plan`**: define the problem boundary, candidate routes, Claims/Evidence structure, and unknowns.
2. **`$research-routes-explore`**: compare routes, preserve positive/negative and contradictory evidence, identify failure regimes, and design reversible probes.
3. **`$research-routes-run`**: run probes within explicitly authorized research scope, record results, and preserve rollback boundaries; hand a bounded evidence package to All in Luna only when it is ready for product implementation.

## Shortest install and use

The repository root is the marketplace root; in Codex Plugins, choose installation from a local path and select this directory. The actual plugin root is `plugins/research-routes/`. Do not run the source-repository builder again inside the distribution repository. Plugin entry skills live under `plugins/research-routes/skills/research-routes*`, the plugin manifest is `plugins/research-routes/.codex-plugin/plugin.json`, and shared contracts live under `plugins/research-routes/shared/`.

## Research boundary

A terrain map is not experiment authorization, implementation order, HumanDecision, or canonical-state promotion. Each Claim should cite Evidence; Evidence keeps its polarity; and the next probe must explicitly set `reversible: true`. The shared runtime validator fails closed on boundary violations.

## Good fits

- Compare two scientific routes without selecting one prematurely.
- Preserve contradictory results and failure regimes instead of retaining only positive conclusions.
- Choose a low-cost, reversible probe that can distinguish competing assumptions before implementation.

Advanced contracts live under `plugins/research-routes/skills/research-routes*/`, `plugins/research-routes/shared/`, `plugins/research-routes/.codex-plugin/plugin.json`, and the root `LICENSE`.
