# Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

**Put a question into Research Routes and get a route-neutral evidence map: Claims, Evidence, unknowns, contradictions, failure regimes, and reversible probes stay separate.**

Research Routes is independently installable and can coexist with All in Luna. It owns route-neutral research; when product delivery is explicitly authorized, hand a bounded evidence package to All in Luna.

## The fixed ordinary-user path

```text
one-sentence research question or existing evidence package → one resource confirmation card → Coordinator → route dependency waves → result
```

The one card confirms research scope, delivery mode, speed/model, Coordinator, and any user-provided skills/plugins/MCP bindings. Research Routes does not default to multi-layer governance, frequent interruptions, or a real canary on every run; ordinary exploration does not require turning a terrain map into an implementation plan.

| Mode | Default path | Boundary |
| --- | --- | --- |
| `quick` | Coordinator + necessary route Owner(s) | Small route mapping; no Integration/Acceptance by default |
| `standard` | Coordinator + multiple independent route Owners | Use for parallel comparison; one Integration only for a shared evidence artifact or real conflict |
| `full` | Coordinator + route Owners + the risk-required path | Only for high-risk, large cross-contract, or scientific-authority work; an explicit evidence upgrade while the lean runtime materializes no Acceptance/CounterPilot lanes |

`fast`, `ultra-fast`, and `all-luna` remain available as resource policies; they change speed/model locking and do not add governance automatically. A complete third-party plan remains `parallel-only`, preserving its direction and completion standard.

## Three research entry points

1. **`$research-routes-plan`**: define the problem boundary, candidate routes, Claims/Evidence structure, and unknowns.
2. **`$research-routes-explore`**: compare routes, preserve positive/negative/contradictory evidence, identify failure regimes, and design a reversible probe.
3. **`$research-routes-run`**: run a probe only inside explicitly authorized research scope, record the result, and preserve rollback boundaries.

AI inference does not become experiment authorization, implementation order, HumanDecision, or canonical state automatically. Each Claim should point to Evidence, Evidence keeps its polarity, and a probe explicitly sets `reversible: true`.

## First-use evidence (on demand)

An ordinary research run does not require a real canary every time. When real host verification is needed, receipts distinguish `requested`, `resolved`, and `actual`; CI reports only `FIXTURE_PASS`, complete real evidence may report `REAL_PASS`, missing proof is `BLOCKED`/`UNVERIFIED`, and the Integration boundary is `mechanical-only`. See the [first-use protocol](https://github.com/zenx0x/allinluna/blob/main/docs/first-use-protocol.md) for the read-only contract.

## Handoff to All in Luna

When product implementation is ready, hand off the route/evidence package and list facts that remain unknown; do not treat the terrain map as an implementation plan. See the [All in Luna README](../../../README.en.md) for its one-card and dependency-wave path.

## Install and file locations

From the source repository, choose the repository root in Codex Plugins; its marketplace lists both distributions. In a standalone package, the actual plugin root is `plugins/research-routes/`, and the source-repository builder does not need to run again inside the package.

- Plugin entry points: `plugins/research-routes/skills/`, including `plugins/research-routes/skills/research-routes`
- Manifest: `plugins/research-routes/.codex-plugin/plugin.json`
- Shared contracts: `plugins/research-routes/shared/`
- Detailed All in Luna user flow: `plugins/allinluna/skills/allinluna-run/references/user-flow.md`

Apache License 2.0. See `LICENSE`.
