---
name: research-routes-run
description: Execute an approved Research Routes context across software, research-exploration, or hybrid journeys with concurrent route owners, shared control-plane resources, CounterPilot read-only boundaries, explicit evidence and provenance, lifecycle recovery, and HumanDecision seams. Use when implementation or experiments must proceed without treating AI inference as fact or silently changing canonical state.
---

# Research Routes Run

Execute the approved route graph through the shared control plane and preserve route identity.

- Load the context and validate it before dispatch.
- Keep independent route owners concurrent where dependencies permit; share only the shared backbone and declared resources.
- Run CounterPilot as read-only challenge work. Record its boundary and findings; never let it mutate nodes or promote a route.
- Require provenance for evidence and preserve all polarity, including failure, conflict, boundary, and unknown.
- Treat `implementation` as distinct from `canonical`. A HumanDecision is required before canonical promotion or setting current continuation.
- Use lifecycle events to rewind or recover; never rewrite or delete prior route history.
- Reject missing, cross-context, or ambiguous IDs rather than defaulting to GraphPE, a repository, or a preferred route.

Use the shared control-plane, resource, recovery, and router contracts under `shared/` together with the existing All in Luna execution machinery. This overlay changes the semantic vocabulary and entry points, not the shared orchestration behavior.
