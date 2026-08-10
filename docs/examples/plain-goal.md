# Example: a plain goal

This example shows the public Python entry point with a goal large enough to benefit from All in Luna.

It compiles the request into typed runtime state; it does **not** pretend that compilation alone completed the work.

```python
from allinluna_runtime.packs import SinglePublicSkillAPI

compiled = SinglePublicSkillAPI().compile({
    "goal": "Finish the authentication refactor across backend, frontend, migration, and tests",
    "done_when": [
        "the authentication backend changes are implemented",
        "the frontend authentication flow is updated",
        "required migration work is complete",
        "targeted and integration checks pass",
        "changed paths and evidence are available",
    ],
})

print(compiled.task_graph.to_dict())
```

The exact TaskGraph depends on the goal, repository surface, Pack, and any semantic planning available in the current environment. The important point is that the public entry accepts the outcome you want rather than requiring you to hand-author a scheduler graph first.

The equivalent CLI entry is:

```text
allinluna start --goal "Finish the authentication refactor across backend, frontend, migration, and tests"
```

Then inspect the durable run rather than assuming success:

```text
allinluna status RUN_ID
allinluna next-actions RUN_ID
allinluna drive RUN_ID
```

A realistic run may expose independent top-level work such as backend and frontend changes while keeping migration or integration work waiting on the contracts they actually need.

If the runtime emits a host action, relay that exact resolved action to the named tool. Do not silently replace a resolved physical HostAction with another tool. In a host-less environment, preserve the action and report the corresponding relay requirement; report a capability as blocked only when capability discovery supports that claim.

Lane-local work follows a separate rule: local worker intent may resolve to a real host-native worker when one exists, or to durable Lane-direct work when the selected execution policy allows it. A direct result still has to return through the runtime and satisfy evidence checks before the WorkUnit is complete.

The final evidence path is conceptually:

```text
Goal
 -> Top-level Task(s)
 -> WorkUnit(s)
 -> checks / immutable artifacts
 -> work-handoff/v1
 -> lane-handoff/v1
 -> Coordinator completion decision
```

See [Quickstart](../user/quickstart.md) for ordinary use and [Models & performance](../user/models-and-performance.md) for optional resource routing.
