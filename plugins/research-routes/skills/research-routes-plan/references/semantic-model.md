# Semantic model

The shared context contains a question/problem/frame, a shared backbone, concurrent route and branch records, typed semantic nodes, provenance, HumanDecision records, and append-only lifecycle events. A route can be parked, reopened, revived, rewound, rejected, or superseded without deleting its historical context.

Evidence polarity describes what an observation does, not whether a route is approved. `failure`, `boundary`, `conflict`, and `unknown` are first-class outcomes. `candidate-inferred` is a useful candidate relation but never an authoritative fact. `human-confirmed` records an explicit decision; it does not erase contradictory evidence.
