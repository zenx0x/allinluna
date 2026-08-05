# Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

Research Routes preserves route-neutral Claims, Evidence, unknowns, contradictions, failure regimes, and reversible probes. It remains an independent plugin and hands an authorized evidence package to the single All in Luna Skill for delivery.

## Runtime boundary

```text
question or evidence package -> route map -> claims/evidence -> reversible probe -> explicit handoff
```

The package is built from the canonical `allinluna_runtime` source. Conformance traces distinguish `requested`, `resolved`, and `actual` resource values. A diagnostics report is `PASS` when `identity`, `create`, `read`, `wait`, `cancel`, and `idempotency` checks are complete; missing/blocked traces are `BLOCKED`.

## Install and file locations

- Plugin skills: `plugins/research-routes/skills/`
- Shared public Skill: `plugins/research-routes/skills/allinluna/`
- Canonical runtime: `plugins/research-routes/runtime/allinluna_runtime/`
- Plugin manifest: `plugins/research-routes/.codex-plugin/plugin.json`

Apache License 2.0. See `LICENSE`.
