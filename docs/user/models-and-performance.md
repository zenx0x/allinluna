# Models & performance

All in Luna does **not** require one specific model or provider.

For most users, the simplest rule is:

> **Do not configure anything until you have a reason to.**

If you do not request a model, the runtime keeps model selection open to the current host or deployment policy. The Core asks for capabilities such as planning, implementation, mechanical work, or independent verification rather than owning a global model allowlist.

## The simple mental model

Different work benefits from different kinds of compute:

```text
Planning        → stronger reasoning
Implementation  → balanced
Mechanical work → fast / efficient
Verification    → strong / independent
```

This does not mean you must use four different models. It means All in Luna can describe **what kind of capability is useful at each layer** without forcing every layer onto the same route.

## Four common ways to use it

### Balanced

Use this when you do not want to think about model routing.

- leave model selection open to the host/deployment;
- let planning, task execution, and verification inherit sensible defaults;
- override only a task that clearly needs something different.

This is the recommended starting point.

Example: [`balanced.example.yaml`](../../examples/resource-policies/balanced.example.yaml)

### Quality first

Use this for work where mistakes are expensive or reasoning quality dominates speed:

- architecture;
- large refactors;
- difficult debugging;
- research;
- risky migrations;
- final independent verification.

A deployment can map planning, synthesis, deep debugging, and verification capabilities to stronger routes while leaving routine work on a normal route.

Example: [`quality-first.example.yaml`](../../examples/resource-policies/quality-first.example.yaml)

### Efficient

Use this when you want to save time, tokens, or expensive model capacity.

The idea is not “use a weak model everywhere.” It is:

> **reserve strong reasoning for the decisions that actually need it.**

Typical pattern:

- strong reasoning for decomposition;
- strong reasoning for hard blockers;
- fast resources for repetitive edits and routine checks;
- strong or independent verification at the end.

Example: [`efficient.example.yaml`](../../examples/resource-policies/efficient.example.yaml)

### Single model

Use this when you explicitly want the same requested model across the run where the host can honor it.

This is useful when:

- you strongly prefer one model;
- your host exposes only one route;
- you want simpler cost/accounting behavior;
- cross-model consistency matters more than specialization.

Example: [`single-model.example.yaml`](../../examples/resource-policies/single-model.example.yaml)

## Capability classes

Advanced deployments can map semantic capabilities to concrete model routes.

The runtime currently distinguishes capability classes such as:

```text
control.relay
planning.semantic
lane.synthesis
work.mechanical
work.implementation
work.deep-debug
verify.independent
```

A portable policy can say:

```text
planning.semantic → use the deployment's strong planning route
work.mechanical   → use the deployment's fast route
verify.independent → use an independent verification route
```

The Core itself does not need to know whether those routes correspond to OpenAI, Anthropic, Google, a local model, or a future provider.

## Requested, resolved, actual

All in Luna keeps three ideas separate:

- **requested** — what the user, task, or policy asked for;
- **resolved** — what the deployment/host policy selected before execution;
- **actual** — what the host can prove actually ran.

If the host does not expose actual model/reasoning telemetry, `actual` stays unresolved. The runtime should not invent an execution identity just to make the record look complete.

For ordinary users, you usually do not need to think about these layers. They exist so advanced routing remains inspectable without making model configuration mandatory.

## Task and WorkUnit overrides

A run can have a general policy while one Task or WorkUnit requests something narrower.

For example:

```text
Run default        → balanced
Architecture Task  → stronger reasoning
Mechanical WorkUnit → fast route
Final verification → independent strong route
```

Child work can narrow or specialize the parent policy. It should not silently expand authority or override explicit user constraints.

## Route assurance

Model routing and model observation are different problems.

A user may simply want the runtime to request a route, or may require stronger evidence that the requested route was actually used. All in Luna supports multiple assurance levels for those cases.

The ordinary default is intentionally non-blocking when the host does not expose telemetry. Strict assurance should be an explicit choice, not a tax imposed on every run.

## About the example files

The files under [`examples/resource-policies/`](../../examples/resource-policies/) are **templates**, not model defaults shipped by the Core.

Files containing placeholders such as `${STRONG_MODEL}` or `${FAST_MODEL}` must be adapted to the routes available in your environment before use.

That distinction is intentional:

> **All in Luna defines the capability. Your environment decides the concrete model.**
