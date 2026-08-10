# Quickstart

All in Luna has one ordinary user entry: the All in Luna Skill at
`plugins/allinluna/skills/allinluna/SKILL.md`.

You can give it a goal, an existing plan, an active run, or a Research Routes packet. You do **not** need to design a TaskGraph, choose a scheduler, or configure a model first.

## The easiest way: just say what you want done

In Codex Plugins, choose `plugins/allinluna/`, then say something like:

```text
Use All in Luna to finish the authentication refactor.
Keep independent parts moving in parallel where possible.
```

Or simply:

```text
用 All in Luna 完整完成这个项目。
能独立推进的部分尽量并行。
```

All in Luna can turn the goal into independent top-level Tasks, start work whose dependencies are ready, and keep local implementation detail inside the Task that owns it.

For an ordinary software goal, the default Workflow Pack is `delivery`. Request `gsd` explicitly when you want the clarify → specify → decompose → implement → verify → integrate workflow inside a Task.

## What this looks like

A request such as:

```text
Refactor authentication across backend, frontend, migration, and tests.
```

may become:

```text
Auth backend        ● running
Frontend auth flow  ● running
Migration           ○ waiting for auth contract
Integration         ○ waiting for backend + frontend
```

Independent work can move independently. A Task can still use its own subagents, tools, Skills, or MCPs for local work.

## CLI: explicit run control

From the repository root:

```text
python -m pip install -e .

allinluna start --goal "Finish the authentication refactor"
allinluna status RUN_ID
allinluna next-actions RUN_ID
allinluna drive RUN_ID
```

`start` persists the compiled run and exposes ready actions. `status` shows durable state. `next-actions` exposes work that requires the host or current Task to act. `drive` continues the Coordinator loop.

For long-running work, use the durable lifecycle commands when needed:

```text
allinluna pause RUN_ID
allinluna resume RUN_ID
allinluna retry RUN_ID
allinluna reconcile RUN_ID
allinluna cancel RUN_ID
```

## Lane-local work

Most users do not need to operate Lane commands manually. They exist for host integrations, debugging, and explicit runtime control.

A bootstrapped Lane can use commands such as:

```text
allinluna lane start RUN_ID TASK_ID
allinluna lane tick RUN_ID TASK_ID
allinluna lane next-actions RUN_ID TASK_ID
allinluna lane ingest-direct-result RUN_ID TASK_ID RESULT.json
allinluna lane handoff RUN_ID TASK_ID
```

A Lane reopens the same durable Store and may operate only within its local WorkGraph and authority.

If the host exposes a native local-worker capability, the Lane may use it. Otherwise, the default `native_preferred` path can expose durable Lane-direct work to the current Top-level Task instead of pretending a child worker exists.

## Models

You do not have to choose a model first.

All in Luna is vendor-neutral and can leave concrete routing to the current host or deployment policy. Advanced users can route planning, implementation, mechanical work, deep debugging, and independent verification differently.

See [Models & performance](models-and-performance.md).

## What completion means

A preview, pending action, UI state, or self-authored claim is not completion evidence.

Completion can be supported by typed run state, changed-path evidence, immutable artifacts, receipts, checks, and handoffs. A Task is accepted as complete only when the relevant contract and verification conditions are satisfied.

If a host adapter is not bound, the runtime should preserve the action and report the corresponding relay requirement rather than inventing an execution. A capability should be reported as missing only when discovery actually establishes that it is unavailable.

## Next

- [Inputs and journeys](input-and-journeys.md)
- [Models & performance](models-and-performance.md)
- [Plain-goal example](../examples/plain-goal.md)
- [Troubleshooting](../troubleshooting/common-issues.md)
