# Recovery and state transitions

## Run states

```text
planned → running → paused | blocked | completed | failed
paused → running | blocked | failed
blocked → running | failed
```

`completed` and `failed` are terminal. A run is completed only when validation confirms every required task is completed or explicitly approved as skipped.

## Task states

```text
pending → ready → running → completed
                    ↘ blocked → ready | running | failed
ready → cancelled
pending → skipped
```

Only dependency-satisfied tasks may become `ready` or `running`. A skipped required task needs explicit user approval recorded in the event metadata.

## Recovery procedure

1. Load and validate state and append-only events.
2. Verify recorded repository/worktree/commit evidence against reality.
3. Identify stale `running` tasks whose host task no longer exists.
4. Preserve their worktree and commit evidence.
5. Move them to `blocked` with an exact reason, then to `ready` only after deciding how to resume.
6. Never redispatch a completed task unless the plan was explicitly revised.
7. Reconcile new tasks through stable new IDs rather than rewriting historical IDs.

## External approvals

An approval may be bounded to one plan, one exact mutation, or one time. On recovery, do not assume an old approval covers a changed drift plan or a new live action.

## Event log

Every state mutation appends an event with timestamp, actor, entity, previous state, next state, reason, and optional evidence. `run-state.json` is the current projection; `events.jsonl` is the audit trail. Write the state atomically before reporting success.
