# All in Luna × Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

**One-line outcome: one source now builds two independently installable and co-installable Codex distributions: All in Luna for development concurrency, and Research Routes for multi-route evidence and reversible exploration.**

Quick image/GIF location: place the quick demo in [`docs/media/`](docs/media/); [`docs/media/README.md`](docs/media/README.md) records the asset location for this checkout.

## Three entry points

1. **All in Luna**: use `$allinluna-plan` for a complete development plan, then `$allinluna-run` for Coordinator, Owners, worktrees, recovery, integration, and acceptance.
2. **Research Routes**: use `$research-routes` for a route-neutral terrain map, separated Claims/Evidence, route comparison, unknowns, failure regimes, and reversible probes. It is a real research entry, not a jump page.
3. **Local build/release**: run the deterministic builder and parity/coexistence validators to produce both packages; this does not create or publish a GitHub repository.

## Shortest install and use

```powershell
# Build both distributions from this source repository
python scripts/build_distributions.py --output dist
python scripts/validate_distributions.py
python scripts/validate_installations.py
```

Install `dist/all-in-luna` and `dist/research-routes` as separate local Codex plugin sources. Their plugin names are `allinluna` and `research-routes`, so they can coexist. Existing All in Luna users can keep using the original `plugins/allinluna` entry.

## Which one to use

| Need | Entry | Boundary |
| --- | --- | --- |
| Complete authorized work | All in Luna | Plan, concurrent owners, implementation, recovery, integration, acceptance |
| Compare research routes | Research Routes | Claims, Evidence, contradictions, failure regimes, unknowns, reversible probes |
| Execute an approved plan directly | All in Luna `parallel-only` | Preserve direction; normalize dependencies, ownership, and execution |

A Research Routes terrain map is not experiment authorization, implementation order, HumanDecision, or canonical-state promotion. Only after explicit authorization should a bounded route/evidence package be handed to All in Luna.

## Default execution topology (important)

The All in Luna Sponsor conversation creates an independent Coordinator, which dispatches user-visible top-level Codex tasks and uses child coordinators, CounterPilot, worktrees, and recovery when needed. Development concurrency is not the same thing as Research Routes' multi-route evidence comparison.

## Real cases

- New feature: create a complete All in Luna dependency plan, dispatch sidebar-visible top-level tasks by risk, and finish through integration and acceptance.
- Existing plan: use `parallel-only` without reopening product direction.
- Research exploration: keep routes A/B, positive/negative evidence, and failure regimes visible while selecting only a reversible next probe.

## Shared core and two distributions

Both packages come from one source repository and share core, schema, control plane, resources, recovery, router, tests, and evals. Brand, README, default entry, skill metadata, cases, topics, and social copy are explicit overlays only. The builder records source commit/tree/parent/ref, and the validators reject shared-file drift or overlay leakage.

## Advanced docs and development checks

- Plan and resource contracts: `plugins/allinluna/skills/allinluna-plan/references/` and `plugins/allinluna/skills/allinluna-run/references/`
- Dual-distribution contract: `distributions/distribution-manifest.json`
- Build, parity, and provenance: `scripts/build_distributions.py` and `scripts/validate_distributions.py`
- Co-installation validation: `scripts/validate_installations.py`
- Full checks: `python -m unittest discover -s tests -v` and `python scripts/validate_repository.py`

## Resource modes

`premium`, `balanced`, `economy`, `speed`, `fast`, `ultra-fast`, `all-luna`, `mad-luna`, and `custom` change allocation and timing, not completion criteria. Requested and actual values are recorded separately; unavailable models or telemetry are never silently invented.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
