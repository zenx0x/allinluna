# All in Luna

[简体中文（默认）](README.md) | [English](README.en.md)

**Give All in Luna one outcome and get a real execution path to the result, not another long planning conversation.**

All in Luna hands the outcome to an independent Coordinator and releases the necessary Owners in dependency waves. Ordinary users do not need to learn schemas, run state, or layered governance first.

## The fixed ordinary-user path

```text
one-sentence request or existing plan → one resource confirmation card → Coordinator → dependency waves → result
```

1. **One sentence or existing plan:** state the outcome, or provide an existing/third-party plan, repository/local path, and context already available.
2. **One resource card:** confirm delivery mode, speed/model preference, concurrency, Coordinator, and any user-provided skills/plugins/MCP bindings once.
3. **Coordinator:** a separate Coordinator owns dependencies, Owner dispatch, recovery, and completion evidence; the Sponsor keeps direction and human choices.
4. **Dependency waves:** the Coordinator releases Owners whose dependencies are ready and whose writable paths do not conflict.
5. **Result:** return the complete scope, checks actually run, artifact/commit evidence, and remaining blockers.

### Three delivery modes

| Mode | Default path | Governance boundary |
| --- | --- | --- |
| `quick` | Coordinator + necessary Owner(s) | Default for small, clear work; no Integration/Acceptance by default |
| `standard` | Coordinator + multiple independent Owners | Use when the dependency graph benefits from parallelism; one Integration only for a shared contract, real cross-Owner conflict, or a plan requirement |
| `full` | Coordinator + Owners + the risk-required path | Only for high-risk, large cross-contract, or scientific-authority work; an explicit evidence upgrade while the lean runtime still materializes no Acceptance/CounterPilot lanes |

Modes change topology and waiting time, never the completion standard. All in Luna **does not default to multi-layer governance, frequent interruptions, or a real canary on every run**. One resource card is enough; pause again only for actual risk, missing authority, or a necessary human choice.

## Default execution topology (important)

The Sponsor keeps direction, a separate Coordinator owns dependency waves, and it dispatches sidebar-visible top-level Codex task Owners. `quick` creates only necessary Owners; `standard` runs multiple Owners when the dependency graph benefits; `full` explicitly raises the evidence boundary for high-risk, large cross-contract, or scientific-authority work while the lean runtime materializes no extra Acceptance/CounterPilot lanes.

## Retained compatibility paths

- A complete existing plan uses `parallel-only`: preserve its direction and completion standard, normalizing only the dependencies, ownership, resources, recovery, and dispatch needed for safe execution.
- `fast` and `ultra-fast` remain available for higher concurrency targets; `all-luna` and `mad-luna` retain their Luna-family hard lock. They are resource/speed choices and do not add governance automatically.
- User-provided skills, plugins, and MCP bindings remain in the resource card and execution evidence. Record `requested`, `resolved`, and `actual` separately; when the host has no receipt, show `unavailable` rather than claiming success.
- Goals, pushes, publication, deployment, credentials, and live external writes require separate authorization and do not happen by default.

## Resource modes (on demand)

These profiles remain available in the resource card and change allocation, model, or speed without changing the completion standard or adding governance by themselves: `premium`, `balanced`, `economy`, `speed`, `fast`, `ultra-fast`, `all-luna`, `mad-luna`, and `custom`.

## How work reaches the result

The Coordinator releases conflict-free Owners by dependency wave. Each Owner has an exclusive scope, self-contained brief, worktree/commit identity, and focused checks; an unrelated wave continues if one lane blocks. Completion is not the first slice, one dispatch, one commit, or one smoke test; it is the authorized scope closed end to end.

`quick` usually ends with Owner verification; `standard` uses at most one mechanical Integration pass when a shared result needs it; `full` is an explicit risk/evidence upgrade and does not materialize extra governance lanes in the lean runtime. Product/scientific semantic defects return to the original Owner.

## Relationship to Research Routes

Research Routes owns route-neutral Claims, Evidence, unknowns, contradictions, failure regimes, and reversible probes. It does not turn a terrain map into experiment authorization, implementation order, HumanDecision, or canonical state. When product delivery is authorized, hand the bounded evidence package to All in Luna and use the same one-card/dependency-wave path.

## First-use evidence (on demand)

An ordinary run does not require a real canary every time. When real host verification is needed, see [`docs/first-use-protocol.md`](docs/first-use-protocol.md): receipts distinguish `requested`, `resolved`, and `actual`; CI reports only `FIXTURE_PASS`, complete real evidence may report `REAL_PASS`, missing proof is `BLOCKED`/`UNVERIFIED`, and the Integration boundary is `mechanical-only`.

## Shortest entry examples

For an outcome:

```text
Use All in Luna to complete this outcome:
[one-sentence goal, users, constraints, and definition of done]
Show one resource confirmation card; then have the Coordinator execute dependency waves through the result.
```

For an existing plan:

```text
Use All in Luna parallel-only to execute this existing plan:
Plan path: [path or pasted plan]
Preserve its direction and completion standard; make one resource confirmation, then let the Coordinator run it through the result.
```

## Install and go deeper only when needed

Choose the local repository root in Codex Plugins, or choose `plugins/allinluna/` directly. Build both distributions with:

```powershell
python scripts/build_distributions.py --output dist
python scripts/validate_distributions.py
python scripts/validate_installations.py
```

Ordinary users can stop here. For precise control, read:

- [Conversation Intake](plugins/allinluna/skills/allinluna-intake/SKILL.md) for one-sentence context and existing plans;
- [Launch Confirmation](plugins/allinluna/skills/allinluna-launch/SKILL.md) for the single resource card;
- [Plan](plugins/allinluna/skills/allinluna-plan/SKILL.md) for an idea or incomplete plan;
- [Run](plugins/allinluna/skills/allinluna-run/SKILL.md) for the short entry and on-demand references;
- [First-use protocol](docs/first-use-protocol.md) for read-only real-receipt/fixture verification;
- [Research Routes distribution](distributions/overlays/research-routes/README.en.md) for the independent research surface and boundary.

Apache License 2.0. See [`LICENSE`](LICENSE).
