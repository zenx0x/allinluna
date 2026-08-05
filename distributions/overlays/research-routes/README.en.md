# Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

Research Routes preserves route-neutral Claims, Evidence, unknowns, contradictions, failure regimes, mature-method comparators, and reversible probes. Its `research-pack/v1` runtime also records failure polarity, what did not fail, rewind proposals, lessons, reopened problems, canonical downgrades, and Human route authorizations as separate append-only records. It remains an independent plugin and hands a bounded, explicitly authorized evidence package to the single All in Luna Skill for delivery.

## Runtime boundary

```text
question or evidence package -> route map -> claims/evidence -> failure/recovery records -> reversible probe -> HumanDecision seam -> explicit handoff
```

The research Pack runtime is under `plugins/research-routes/runtime/research_routes_runtime/` and uses only generic Core artifact, snapshot, decision, and promotion boundaries. The terrain map never selects a route or authorizes experiments, implementation, or canonical state. A route authorization must name a confirmed HumanDecision; canonical promotion requires a separate `canonical-promotion` decision. Conformance traces distinguish `requested`, `resolved`, and `actual` resource values. A diagnostics report is `PASS` when `identity`, `create`, `read`, `wait`, `cancel`, and `idempotency` checks are complete; missing/blocked traces are `BLOCKED`.

## Install and file locations

- Plugin skills: `plugins/research-routes/skills/`
- Shared public Skill: `plugins/research-routes/skills/allinluna/`
- Canonical runtime: `plugins/research-routes/runtime/allinluna_runtime/`
- Plugin manifest: `plugins/research-routes/.codex-plugin/plugin.json`

Apache License 2.0. See `LICENSE`.
