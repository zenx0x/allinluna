# Quickstart

All in Luna has one ordinary user entry: the All in Luna Skill at
`plugins/allinluna/skills/allinluna/SKILL.md`. Give it a goal, an existing plan,
an active run, or a Research Routes packet. You do not need to construct a
TaskGraph or choose a scheduler first.

## Use the plugin

In Codex Plugins, choose `plugins/allinluna/`. The Skill compiles the input into
a typed run and selects a Workflow Pack. `delivery` is the default for a plain
software goal; request `gsd` when you explicitly want its
clarify-to-integrate workflow.

## Use the CLI

From the repository root, start a run and inspect its durable state:

```text
allinluna start --goal "Add a tested health-check endpoint"
allinluna status RUN_ID
allinluna next-actions RUN_ID
allinluna drive RUN_ID
```

`start` persists the compiled graph and exposes ready actions. `drive` continues
the Coordinator loop. Use `pause`, `resume`, `retry`, `cancel`, or `reconcile`
when the durable run state requires it.

For a lane that has already been bootstrapped with its runtime database:

```text
allinluna lane start RUN_ID TASK_ID
allinluna lane tick RUN_ID TASK_ID
allinluna lane handoff RUN_ID TASK_ID
```

A lane reopens the same Store and may operate only within its local WorkGraph.

## What completion means

Completion is supported by typed state, changed-path evidence, immutable
artifacts, receipts, and handoffs. A preview, pending action, UI state, or
self-authored claim is not completion evidence. If a host adapter is not bound,
the truthful result is `ACTION_RELAY_REQUIRED` with the exact action preserved
for the host; `HOST_CAPABILITY_BLOCKED` is reserved for a capability confirmed
missing by discovery.

See [inputs and journeys](input-and-journeys.md) for the four accepted input
shapes and [plain-goal example](../examples/plain-goal.md) for a complete small
example.
