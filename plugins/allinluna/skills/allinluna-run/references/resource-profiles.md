# Resource profiles

The authoritative defaults live in `assets/resource-profiles.json`. Profiles allocate models and scheduling; they never reduce scope or completion criteria.

| Profile | Desired concurrency | Typical policy |
| --- | ---: | --- |
| `economy` | 4 | Luna-first, explicit escalation |
| `balanced` | 8 | general mixed-model default |
| `premium` | 12 | strongest decision and acceptance roles |
| `speed` | 12 | latency-oriented scheduling |
| `fast` | 24 | high-throughput hierarchical scheduling |
| `ultra-fast` | 48 | maximum mixed-model throughput |
| `all-luna` | 8 | hard Luna family lock |
| `mad-luna` | 24 | Luna max swarm plus duplicate high-risk challenge |
| `custom` | 1–64 | user-defined roles and concurrency |

Actual concurrency is the minimum imposed by the host, machine, ready DAG width, ownership safety, and active budget. At desired concurrency 16 or above, ask once whether a high-quality model should review decomposition, dependencies, ownership, and conflict risk. Record `accepted` plus the model, or `declined`; do not repeatedly ask.

Velocity modifiers compose with model profiles. `all-luna + fast` or `all-luna + ultra-fast` preserves the Luna hard lock while applying the velocity policy. Never switch to a mixed-model profile silently.

## Runtime resolution

Resolve each logical role against the delegation-specific host catalog. Top-level and subagent catalogs may expose different models. Record requested, resolved, and actual model/reasoning/delegation separately. Enforce hard locks recursively for owner subagents. If telemetry is absent, record `unavailable` rather than inventing usage or cost.

For high-concurrency work, resource profiles also resolve primary Coordinator and CounterPilot roles. Child Coordinators inherit the primary Coordinator policy unless the user explicitly provides a different bounded policy.
