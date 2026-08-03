# Planning contract

Use this contract for every plan, regardless of project size.

## Required truths

The plan must preserve:

- the user's objective and full completion standard;
- explicit inclusions and exclusions;
- repository instructions and protected/dirty paths;
- authority boundaries for destructive, credentialed, live, or external actions;
- unknowns and assumptions without presenting them as verified facts;
- implementation, integration, acceptance, and publication as distinct states.

## Authorization states

Record independent booleans for:

- implementation writes;
- Git branch/worktree/commit operations;
- Goal creation;
- user-owned top-level task creation;
- destructive filesystem or Git operations;
- live external mutation;
- publication or deployment.

Permission for one state never implies another. In particular, a request to plan does not authorize implementation, a request to implement does not authorize publication, and large scope does not authorize Goal creation.

## Completeness

A task's deliverables must represent the requested capability, not a demonstration substitute. A first vertical slice is useful when it proves the architecture across layers, but it is only a progress checkpoint.

Avoid indefinite terms such as “finish later,” “other cases,” or “as needed.” Name the owned behavior, failure paths, recovery, tests, and artifacts. When exact enumeration is impossible, state a measurable boundary.

## Questions

Ask the user only when:

- two plausible answers produce materially different architectures;
- a required external credential or live mutation lacks authority;
- a destructive action cannot be made reversible;
- scientific or legal authority cannot be derived from the sources in scope.

Otherwise record a labeled assumption and proceed.

## Plan quality test

A different capable agent should be able to execute each task using only:

- the plan;
- the named repository and base revision;
- the task brief and owned paths;
- the sources explicitly referenced by the task.

If it requires hidden chat context, the plan is incomplete.
