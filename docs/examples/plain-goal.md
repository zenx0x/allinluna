# Example: a plain goal

This example shows the public Python entry point. It compiles a sentence into
a typed graph; it does not pretend that compilation alone completed the work.

```python
from allinluna_runtime.packs import SinglePublicSkillAPI

compiled = SinglePublicSkillAPI().compile({
    "goal": "Add a tested health-check endpoint",
    "done_when": [
        "the endpoint is implemented",
        "targeted tests pass",
        "changed paths and evidence are available",
    ],
})

print(compiled.task_graph.to_dict())
```

The equivalent CLI entry is:

```text
allinluna start --goal "Add a tested health-check endpoint"
```

Then inspect the returned run rather than assuming success:

```text
allinluna status RUN_ID
allinluna next-actions RUN_ID
```

If the runtime emits a host action, relay that exact action to the named tool.
Do not replace it with another tool or add missing resource values. In a
host-less environment, preserve the action and report
`ACTION_RELAY_REQUIRED`; in a host-capability failure, report
`HOST_CAPABILITY_BLOCKED` only when capability discovery supports that claim.

The final evidence path is:

```text
TaskGraph -> WorkUnit -> checks -> immutable artifacts -> work-handoff/v1
         -> lane-handoff/v1 -> Coordinator completion decision
```
