---
name: research-routes-plan
description: Plan software, research-exploration, or hybrid work from a repository, question, problem, frame, or arbitrary starting point. Use when Codex needs a route-neutral map, shared backbone, concurrent routes and branches, explicit claims and hypotheses, provenance, evidence polarity, lifecycle recovery, or a plan that keeps candidate inference separate from HumanDecision and canonical state.
---

# Research Routes Plan

Create a route-neutral semantic context before choosing an implementation or experiment route.

1. Classify the context as `software`, `research-exploration`, or `hybrid` and preserve the exact starting point.
2. Create a question/problem/frame/shared-backbone record. Do not invent a claim when the input only supplies a question.
3. Add one or more concurrent routes and branches. A route is a navigable hypothesis space, not a commitment.
4. Represent claims, hypotheses, probes, experiments, evidence, observations, unknowns, decisions, implementation, canonical state, continuation, and provenance as distinct nodes.
5. Label every evidence item with one of `support`, `counter`, `null`, `boundary`, `conflict`, `failure`, `context`, `mixed`, or `unknown`; label every relation with `source-stated`, `deterministic-derived`, `candidate-inferred`, or `human-confirmed`.
6. Preserve lifecycle events: `Create`, `Fork`, `Park`, `Reopen`, `Revive`, `Rewind`, `Reject`, `Supersede`, `Historical Context`, and `Unresolved`. Forks receive new IDs and rewinds preserve history.
7. Mark CounterPilot work as a read-only boundary. It may challenge scope or assumptions but cannot mutate route state.
8. Never present AI inference as fact. Canonical promotion and current continuation require an explicit HumanDecision with actor and provenance.

Use the shared schema at `shared/schema/research-routes.schema.json`, semantic runtime in `shared/core/model.py`, and read-only validator `shared/router/validate_context.py`. The companion reference explains the node and lifecycle vocabulary.
