---
artifact: product_contract
version: 1
protocol: contract-freeze/v1
task: run-allinluna-v2-rc2-product-surface:task:T0
status: frozen
---

# All in Luna v2.0.0-rc.2 product contract

This document is the product-level contract for the RC2 hardening run. It
defines the behavior that the downstream lanes may implement and verify. It
does not authorize a stable release, a stable tag, a push, or any other
external mutation.

## Product outcome

All in Luna accepts a user goal, existing plan, active run, or Research Routes
packet through one public Skill, compiles it into typed run/task contracts,
and executes it through the following durable hierarchy:

```text
Conversation -> Global Coordinator -> Task Lane -> local WorkUnit -> host/tool
```

The RC2 candidate is qualified by typed contracts, immutable artifacts,
trusted host receipts, and lane handoffs. A plan, preview, or UI grouping is
not completion evidence.

## Frozen invariants

1. **One public entry.** `plugins/allinluna/skills/allinluna/SKILL.md` is the
   user entry point. The registry/launcher is internal discoverability only.
2. **Typed durable state.** Run, Task, Contract, WorkGraph, Context snapshot,
   artifact, receipt, and handoff identities remain distinct. SQLite Store
   state is authoritative; raw tool output belongs in the Artifact Store.
3. **Authority follows the hierarchy.** The Coordinator owns cross-Lane
   dependencies, contracts, resources, and root completion. A Lane owns only
   its local WorkGraph, local scheduler, context slice, worker receipts,
   synthesis, and handoff.
4. **Narrowing is monotonic.** A child WorkUnit may narrow scope, authority,
   ownership, and resources, never expand them. Cross-Lane work is a promotion
   request, not an implicit write.
5. **Project identity is explicit.** A top-level target uses a project
   resolution receipt containing `projectId + environment`, or an explicit
   `{"type":"projectless"}` target. A Task ID is never substituted for a
   project ID.
6. **Exact host relay.** A `HostAction` is relayed to its exact tool and
   capability. `ACTION_RELAY_REQUIRED` means no adapter is bound;
   `HOST_CAPABILITY_BLOCKED` is reserved for a discovered missing capability.
   Receipts must report observed `actual_tool`, `actual_capability`, and
   `action_contract_hash`; these fields are never inferred from request text.
7. **Resource evidence is layered.** `requested`, `resolved`, and `actual`
   remain separate. Missing host telemetry leaves `actual: null` and
   `actual_state: unresolved`; it is not a fabricated fallback and is not an
   automatic execution failure. An exact create-thread contract hash is frozen
   only after the model route resolves to a non-empty model.
8. **Permissions are just in time.** Credentials, publication, deployment,
   push, destructive work, and live external mutation require an action-bound
   permission decision. The default external-action policy for this run is
   `ask`.
9. **Research boundaries remain honest.** A Research Routes map preserves
   Claims, Evidence, unknowns, contradictions, failure regimes, HumanDecision,
   and experiment authorization. Research input does not become implementation
   authorization or canonical product state by implication.
10. **RC-only release posture.** RC2 qualification may recommend a candidate;
    it must not create or promote a stable release or tag.

## Required user-visible journeys

The implementation and evaluation lanes must preserve these outcomes:

- plain goal -> compiled delivery run -> inspectable status, actions,
  receipts, artifacts, and handoff;
- existing plan or active run -> read-only import/recovery without mutating the
  source plan or inventing evidence;
- Research Routes packet -> route-neutral execution context with explicit
  unknowns and authorization boundaries;
- project-aware top-level dispatch -> project resolution before exact host
  action construction;
- no host adapter -> durable exact relay action and a truthful relay-required
  result;
- interrupted or expired execution -> Store-backed recovery that retains
  immutable artifacts and recomputes ready work;
- local WorkUnit completion -> artifact-referenced `work-handoff/v1`, then
  artifact-referenced `lane-handoff/v1` to the Coordinator.

## Explicit non-goals for this RC2 contract

- no stable release, stable tag, force-push, rebase, or publication;
- no direct global Task or GlobalScheduler mutation from a Lane;
- no silent model/vendor fallback or inferred actual resource receipt;
- no fabricated completion from an action, plan, UI state, or pending receipt;
- no driver, code injection, credential harvesting, or unrelated external
  operation;
- no broad repository cleanup outside the owning lane paths.

## Frozen provenance

The source of this freeze is the lane-owned Runtime Store state:

- run: `run://run-allinluna-v2-rc2-product-surface`;
- task: `task://run-allinluna-v2-rc2-product-surface:task:T0`;
- contract: `contract://task/run-allinluna-v2-rc2-product-surface:contract:contract-allinluna-v2-rc2-product-surface-T0@1`;
- context: `context://lane/run-allinluna-v2-rc2-product-surface:task:T0@2`;
- WorkGraph: `runtime-db://work-graph/run-allinluna-v2-rc2-product-surface:task:T0`;
- runtime DB: `.allinluna\\rc2-global-coordinator-20260806.db`;
- workspace/branch: `D:\\AgentSkills\\allinluna`,
  `fix/v2-rc2-product-surface`;
- observed source HEAD: `87b6c817ca8800b73224438c3ab7eddf5126dda8`;
- repository state at bootstrap: dirty; protected paths remain untouched.

The persisted dispatch envelope for the current lane attempt is the runtime
authority. Attempt 2 uses `lane-attempt://lane-attempt-22d7cb2cef73bad6b19d`,
Context `context://lane/run-allinluna-v2-rc2-product-surface:task:T0@2`, and
`task_envelope_digest`
`b226dcc8491fa42b8800b921b5eec536cf89e0080c7669ec647637b33f154146`.
It validates against its Task and was used without rewriting the Store. The
immutable attempt-1 dispatch remains recorded with digest
`e4ac6df321c9d0a2796885bd5f798fb157d7ee803dd5d6f6cbd1d3e932896f9c`; it is
historical evidence, not the current lane bootstrap.
