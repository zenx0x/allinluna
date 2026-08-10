# Research Routes 0.3.0-rc.2

[简体中文（默认）](README.md) | [English](README.en.md)

Research Routes preserves route-neutral Claims, Evidence, unknowns, contradictions, failure regimes, mature-method comparators, and reversible probes. Its `research-pack/v1` runtime also records failure polarity, what did not fail, rewind proposals, lessons, reopened problems, canonical downgrades, and Human route authorizations as separate append-only records. It remains an independent plugin and hands a bounded, explicitly authorized evidence package to the single All in Luna Skill for delivery.

## Runtime boundary

```text
question or evidence package -> route map -> claims/evidence -> failure/recovery records -> reversible probe -> HumanDecision seam -> explicit handoff
```

The research Pack runtime is under `plugins/research-routes/runtime/research_routes_runtime/` and is version `0.3.0-rc.2`. It uses only generic Core artifact, snapshot, decision, and promotion boundaries. The terrain map never selects a route or authorizes experiments, implementation, or canonical state. A route authorization must name a confirmed HumanDecision; canonical promotion requires a separate `canonical-promotion` decision. Research Routes depends on the co-installed All in Luna plugin through the private `research-routes-bridge/v1` declared in its plugin manifest; it does not copy the All in Luna public Skill or runtime. Conformance traces distinguish `requested`, `resolved`, and `actual` resource values and use schema v8, route assurance, and exact relay terminology. A diagnostics report is `PASS` when `identity`, `create`, `read`, `wait`, `cancel`, and `idempotency` checks are complete; missing/blocked traces are `BLOCKED`.

## Install and file locations

- Plugin skills: `plugins/research-routes/skills/`
- Research Routes runtime: `plugins/research-routes/runtime/research_routes_runtime/`
- All in Luna dependency and private bridge: `plugins/research-routes/.codex-plugin/plugin.json`
- Plugin manifest: `plugins/research-routes/.codex-plugin/plugin.json`

Apache License 2.0. See `LICENSE`.
