# Execution contract

The Sponsor discusses direction and authority; route owners implement or investigate. The Coordinator owns dependencies and recovery. CounterPilot is independent, read-only, and evidence-bearing. Integration may combine route outputs, but it cannot convert candidate inference into canonical state. Only a recorded HumanDecision can do that, with provenance and the selected node ID.

The Pack runtime is append-only: failures, rewinds, lessons, reopened problems,
and canonical downgrades become durable records rather than rewritten route
state. A confirmed route authorization is scoped to the requested route
operation. Implementation and canonical promotion remain separate boundaries;
canonical promotion requires a dedicated HumanDecision scope.
