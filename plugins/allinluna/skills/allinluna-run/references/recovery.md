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

1. Load and validate state and append-only events, including Sponsor, primary Coordinator, CounterPilot, child Coordinator IDs/cursors, and open challenges.
2. Use `reconcile_threads.py` with a fresh normalized task snapshot to recover cursors,
   host state, completion reports, and tasks needing evidence collection.
3. Use `verify_task_evidence.py` to verify recorded repository/worktree/commit evidence against reality.
4. Reconcile control-plane tasks first, then identify stale `running` owner tasks whose host task no longer exists.
5. Preserve their worktree and commit evidence.
6. Move them to `blocked` with an exact reason, then to `ready` only after deciding how to resume.
7. Never redispatch a completed task unless the plan was explicitly revised.
8. Add newly requested scope with `revise_active_plan.py`; use stable new task IDs and
   append-only revision files instead of rewriting historical IDs or restarting the run.

`sponsor_tick.py` monitors the independent primary Coordinator and CounterPilot without taking over implementation. `coordinator_tick.py --coordinator-id ...` resumes the primary or a child shard. `control_run.py` handles explicit pause, resume, retry, and concurrency changes without
replanning. After every recovery action, run `coordinator_tick.py` again so unrelated ready
lanes continue immediately.

For high/critical runs, unresolved CounterPilot challenges block final completion but do not stop unrelated implementation. Resolve them as accepted, rejected with evidence, or explicitly deferred with authority; do not erase them during recovery.

## External approvals

An approval may be bounded to one plan, one exact mutation, or one time. On recovery, do not assume an old approval covers a changed drift plan or a new live action.

## Event log

Every state mutation appends an event with timestamp, actor, entity, previous state, next state, reason, and optional evidence. `run-state.json` is the current projection; `events.jsonl` is the audit trail. Write the state atomically before reporting success.
