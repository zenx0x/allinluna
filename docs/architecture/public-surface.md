# Public surface and evidence boundaries

This is the user-facing architecture guide. The normative RC2 contract remains
in the frozen `docs/architecture/v2-rc2/` subtree; this page explains how a user
encounters that contract without exposing internal scheduler details as a
product entry point.

## One public entry

```text
goal / plan / active run / Research Routes packet
                         |
                         v
plugins/allinluna/skills/allinluna/SKILL.md
                         |
                         v
typed RunIntent -> TaskGraph -> Coordinator -> Task Lane -> WorkUnit
```

The registry and launcher may make the Skill discoverable, but ordinary users
start from their goal or journey. Internal schemas are implementation details
unless an expert is inspecting evidence.

## Authority and narrowing

The Coordinator owns cross-Lane dependencies, contracts, resources, and root
completion. A Lane owns only its local WorkGraph, local scheduling, context
slice, worker receipts, synthesis, and handoff. A child WorkUnit may narrow its
parent's scope, authority, ownership, and resources, never widen them.

## Evidence is a separate path

```text
requested route -> resolved route -> actual host observation
declared export -> immutable artifact -> verified receipt/evidence
current state -> historical event -> completion decision
```

The runtime keeps these values separate so that a pending action, a preview, or
missing telemetry cannot become a fabricated success. A lane handoff is
artifact-referenced and contract-versioned; it does not self-sign evidence it
did not collect.

## Research remains route-neutral

Research Routes inputs preserve Claims, Evidence, unknowns, contradictions,
failure regimes, HumanDecision, and experiment authorization. They provide
context for a decision; they do not silently authorize implementation or
promote a claim into canonical product state.

For the exact frozen invariants and acceptance ownership, see:

- `docs/architecture/v2-rc2/product-contract.md`
- `docs/architecture/v2-rc2/interface-contracts.md`
- `docs/architecture/v2-rc2/acceptance-matrix.md`
