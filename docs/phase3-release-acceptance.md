# Phase 3 release acceptance — v2.0.0-rc.1

This is the bounded release-acceptance record for the RC. It validates the
release hardening surface only; it does not redesign Core, TaskGraph, Context
Kernel, GSD, or Research Routes.

## Release identity

- All in Luna: `2.0.0-rc.1`
- Research Routes: `0.3.0-rc.1`
- Runtime database schema: `8`
- Required branch: `refactor/vnext-hierarchical-runtime`
- Baseline: `fc1b6dc`
- Release-hardening commit: recorded in the local Git handoff for this RC
- Stable tag/publication: intentionally not created

## Acceptance claims

- [ ] A repository-backed Task emits project resolution before create-thread
      when no `projectId` receipt is present.
- [ ] A project-resolution receipt supplies `projectId` and worktree
      `environment`; the create target contains no `task_id` project identity.
- [ ] A projectless Task emits an explicit `projectless` target.
- [ ] The final exact create action contains `target`, `prompt`, `model`, and
      `title`, and its `action_contract_hash` is computed after route
      resolution.
- [ ] An unresolved model emits no executable create action.
- [ ] No bound HostAdapter returns `ACTION_RELAY_REQUIRED`; it does not block
      the Task or call a substitute.
- [ ] Only confirmed absence of the exact host capability returns
      `HOST_CAPABILITY_BLOCKED`.
- [ ] An external top-level receipt missing `actual_tool` (including the
      erroneous `spawn_agent` case) is rejected as `HOST_PROTOCOL_VIOLATION`.
- [ ] A trusted direct HostAdapter can sign explicit observed fields.
- [ ] Route assurance remains `observe_if_exposed` unless the request selects
      a stricter policy; missing telemetry does not become fabricated actual.

## Required commands

Run from the repository root, serially:

```text
python -m pytest tests -q
python scripts/validate_vnext_tests.py --run --performance --json
python scripts/validate_repository.py
python scripts/validate_core_slim.py
python scripts/validate_distributions.py
python scripts/validate_installations.py
```

## Minimal real Desktop canary

The canary is an exact relay test, not a simulated host result:

1. Start with the request and command in `docs/phase3-desktop-canary/`.
2. Confirm the emitted action is `codex_app__create_thread` and that its
   arguments include the complete `target`, `prompt`, `model`, `title`, and
   optional `thinking` values. The model must be Luna-class or
   `gpt-5.3-codex-spark`; use max only for a critical canary.
3. Invoke that exact Desktop tool once with those exact arguments. Do not
   replace it with a local/subagent/current-thread action.
4. Export the real Desktop receipt. Fill `actual_tool`, `actual_capability`,
   and `action_contract_hash` explicitly from the same invocation. Keep a
   missing model-route observation unresolved rather than inventing actual.
5. Ingest the receipt and run `drive RUN_ID` until the receipt/task state is
   visible. Preserve the DB, action, raw receipt, and Desktop export together.

The canary package contains templates only. No thread id, actual route, or
receipt is claimed by this document.
