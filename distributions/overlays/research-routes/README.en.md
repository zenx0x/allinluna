# Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

Research Routes preserves route-neutral Claims, Evidence, unknowns, contradictions, failure regimes, and reversible probes. It remains an independent plugin and hands an explicitly authorized evidence package to the single All in Luna Skill for delivery.

## Runtime boundary

```text
question or evidence package -> route map -> claims/evidence -> reversible probe -> explicit handoff
```

The package is built from the canonical `allinluna_runtime` source. Receipts distinguish `requested`, `resolved`, and `actual`; fixture checks report `FIXTURE_PASS`, real proof may report `REAL_PASS`, and missing proof is `BLOCKED`/`UNVERIFIED`. Mechanical integration is `mechanical-only`.

## Install and file locations

- Plugin skills: `plugins/research-routes/skills/`
- Shared public Skill: `plugins/research-routes/skills/allinluna/`
- Canonical runtime: `plugins/research-routes/runtime/allinluna_runtime/`
- Plugin manifest: `plugins/research-routes/.codex-plugin/plugin.json`

Apache License 2.0. See `LICENSE`.
