# Resource profiles

The authoritative defaults live in `assets/resource-profiles.json`. Profiles are allocation policies, not guarantees that a named model exists.

## Runtime resolution

1. Load the profile.
2. Apply user overrides.
3. Query or inspect the host's actual model/reasoning/delegation capabilities.
4. Resolve logical tiers or model families.
5. Record requested and actual values per task.
6. Enforce hard locks before dispatch and during validation.

Use `scripts/resolve_profile.py` to produce a deterministic merged policy. Supply `--delegation top-level-task|subagent|sequential` with a delegation-scoped catalog. It deliberately leaves unresolved logical tiers visible when no runtime catalog is supplied.

On Codex App, build the top-level catalog from the `codex_app__create_thread` tool declaration, not from subagent model overrides. The two surfaces may expose different model families and reasoning levels.

## Profile comparison

| Profile | Default concurrency | Model policy | High-risk review |
| --- | ---: | --- | --- |
| `premium` | 4 | mixed logical tiers | independent frontier |
| `balanced` | 3 | mixed logical tiers | independent when warranted |
| `economy` | 2 | Luna-first | targeted, ask before escalation |
| `speed` | 6, host-capped | fastest suitable mixed tiers | milestone-only |
| `all-luna` | 4 | hard Luna lock | Luna high |
| `mad-luna` | 8, host-capped | hard Luna lock | independent Luna max |
| `custom` | user-defined | user-defined | user-defined |

Effective concurrency is always capped by the host, dependencies, writable ownership, and budget.

Every built-in profile defaults substantive root-level owner lanes to user-visible top-level Codex tasks. Each profile keeps root-level `subagent` then `sequential` as a possible fallback order, but fallback requires explicit user approval. Missing top-level capability never silently changes topology. Once assigned, a top-level owner may use bounded internal subagents under the same ownership and model policy.

## Mad Luna

`mad-luna` requests:

- the Luna model family for coordinator, planner, implementers, integration, and acceptance;
- the highest reasoning effort exposed for each role;
- maximum safe independent parallelism;
- an independent verifier for high-risk milestones;
- no automatic non-Luna escape hatch.

If Luna or maximum reasoning is unavailable, record the exact mismatch. Follow `unavailable_action` (`pause` by default); do not relabel another model as Luna.

## Budget

Budgets can be expressed as hard/soft limits for tokens, credits, elapsed time, or currency. Only enforce a metric the host exposes. Keep unobservable actuals as `unavailable`. A soft limit prompts reassessment; a hard limit pauses new dispatch without marking incomplete work complete.
