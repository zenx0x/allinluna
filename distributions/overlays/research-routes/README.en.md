# Research Routes

**One-line outcome: map multiple research routes against evidence, preserve Claims, contradictions, failure regimes, and unknowns, and choose only a reversible next exploration step.**

Research Routes is an independent Codex research distribution, not a jump page and not a way to disguise a terrain map as an implementation plan. It shares core schemas, control plane, resources, recovery, routing, and tests with All in Luna; All in Luna handles development concurrency, while Research Routes handles multi-route evidence and reversible exploration.

## Three entry points

1. **`$research-routes-plan`**: define the problem boundary, candidate routes, Claims/Evidence structure, and unknowns.
2. **`$research-routes-explore`**: compare routes, preserve positive/negative and contradictory evidence, identify failure regimes, and design reversible probes.
3. **`$research-routes-run`**: run probes within explicitly authorized research scope, record results, and preserve rollback boundaries; hand a bounded evidence package to All in Luna only when it is ready for product implementation.

## Shortest install and use

```powershell
python scripts/build_distributions.py --output dist
python scripts/validate_distributions.py
python scripts/validate_installations.py
```

Install `dist/research-routes` as a local Codex plugin. It uses the distinct `research-routes` name and can coexist with `allinluna`.

## Research boundary

A terrain map is not experiment authorization, implementation order, HumanDecision, or canonical-state promotion. Each Claim should cite Evidence; Evidence keeps its polarity; and the next probe must explicitly set `reversible: true`. The shared runtime validator fails closed on boundary violations.

## Good fits

- Compare two scientific routes without selecting one prematurely.
- Preserve contradictory results and failure regimes instead of retaining only positive conclusions.
- Choose a low-cost, reversible probe that can distinguish competing assumptions before implementation.

Advanced contracts live under `plugins/research-routes/skills/`, `distributions/distribution-manifest.json`, and `scripts/validate_route_packet.py`.
