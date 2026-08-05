# Semantic model

The shared context contains a question/problem/frame, a shared backbone, concurrent route and branch records, typed semantic nodes, provenance, HumanDecision records, and append-only lifecycle events. A route can be parked, reopened, revived, rewound, rejected, or superseded without deleting its historical context.

The Pack adds typed recovery records without changing Core: `FailureRecord` keeps polarity plus `what_failed` and `what_did_not_fail`; `RewindProposal` points at a prior node and must preserve history; `Lesson` carries applicability and non-generalization limits; `ReopenedProblem` points back to unknowns and failures; `CanonicalDowngrade` lowers current state while retaining historical references; and `RouteAuthorization` names a confirmed HumanDecision.

Evidence polarity describes what an observation does, not whether a route is approved. `support`, `counter`, `null`, `boundary`, `conflict`, `failure`, `context`, `mixed`, and `unknown` are first-class outcomes. `candidate-inferred` is a useful candidate relation but never an authoritative fact. `human-confirmed` records an explicit decision; it does not erase contradictory evidence.
