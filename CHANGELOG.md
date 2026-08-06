# Changelog

## 2.0.0-rc.2 / 0.3.0-rc.2 — product hardening

- Distribution validation now derives candidate versions and namespaced tags
  from the release manifest instead of pinning the implementation to RC1.
- Built artifacts verify every canonical runtime, Skill, test, and eval file
  against the source inventory, reject source or overlay symlinks, and enforce
  the declared overlay allowlist.
- Standalone marketplace manifests and isolated co-installation checks retain
  distinct plugin identities and release metadata without authorizing stable
  publication.
- The public README and Skill now describe the user journey first and keep
  resource selection vendor-neutral with explicit precedence.
- Resource policy preserves layered route sources, strict invalid-policy
  handling, route-aware host identity, hard locks, and resource inheritance
  across Store recovery.
- Verification planning and command trust keep model/legacy/external commands
  non-executable by default and require provenance, sandbox, and JIT approval.

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
