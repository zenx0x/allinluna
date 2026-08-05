# Changelog

## 2.0.0-rc.1 — v2 release hardening

This release candidate hardens the vNext runtime without redesigning Core,
TaskGraph, Context Kernel, GSD, or Research Routes.

- Top-level project targets now come from an explicit project-resolution
  receipt. Projectless Tasks use an explicit projectless target, and unresolved
  project identity emits `codex_app__list_projects` first.
- Exact `codex_app__create_thread` actions contain the host-required
  `target`, `prompt`, `model`, and `title` arguments. Host route resolution
  completes before `action_contract_hash` is frozen; an unresolved model emits
  a non-executable route-resolution action.
- No-host dispatch returns `ACTION_RELAY_REQUIRED`. `HOST_CAPABILITY_BLOCKED`
  is reserved for capability discovery that confirms the exact tool is absent.
- External top-level receipts must explicitly provide `actual_tool`,
  `actual_capability`, and `action_contract_hash`. Only a trusted runtime
  HostAdapter direct call may sign those fields.
- Runtime documentation and acceptance materials now use database schema v8,
  `drive`/`lane` CLI surfaces, exact relay, and route assurance terminology.
- All in Luna is `2.0.0-rc.1`; Research Routes is `0.3.0-rc.1`.

This is an RC only. It is not a stable tag and is not published by this
repository state.
