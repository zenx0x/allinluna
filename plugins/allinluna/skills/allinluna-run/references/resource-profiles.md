# Resource profiles

The authoritative model and scheduling defaults live in
`assets/resource-profiles.json`. Profiles allocate capability and speed; they
never reduce the requested scope or completion criteria.

## User-facing delivery modes

Delivery mode and resource profile are separate controls:

| Delivery mode | Default topology | Use |
| --- | --- | --- |
| `quick` | Coordinator + necessary Owner(s) | Small, clear, bounded work; no Integration/Acceptance by default |
| `standard` | Coordinator + independent Owners | Dependency waves that benefit from parallelism; one Integration only when a shared result needs it |
| `full` | Coordinator + Owners + risk-required evidence | Only high-risk, large cross-contract, or scientific-authority work; an explicit upgrade without materializing Acceptance/CounterPilot lanes |

## Retained velocity and model profiles

| Profile | Desired concurrency | Typical policy |
| --- | ---: | --- |
| `economy` | 4 | Luna-first, explicit escalation |
| `balanced` | 8 | general mixed-model default |
| `premium` | 12 | strongest decision and acceptance roles |
| `speed` | 12 | latency-oriented scheduling |
| `fast` | 24 | high-throughput hierarchical scheduling |
| `ultra-fast` | 48 | maximum mixed-model throughput |
| `all-luna` | 8 | hard Luna-family lock |
| `mad-luna` | 24 | Luna max swarm plus duplicate high-risk challenge |
| `custom` | 1–64 | user-defined roles and concurrency |

`fast` and `ultra-fast` are retained velocity profiles. `all-luna` and
`mad-luna` retain their Luna-family locks. A modifier may compose with a
delivery mode, such as `standard + fast` or `full + all-luna`; it does not
silently add governance or change the completion standard.

Effective concurrency is the minimum imposed by the host, machine, ready DAG
width, exclusive ownership, and active budget. At desired concurrency 16 or
above, ask once whether a high-quality model should review decomposition,
dependencies, ownership, and shard boundaries; record accepted/declined and
do not ask again during Run.

## Runtime evidence

Resolve each logical role against the delegation-specific host catalog. Record
requested, resolved, and actual model, reasoning, delegation, and capability
separately. If telemetry is absent, record `unavailable`; never infer actual
usage or cost from a requested profile. Enforce a hard Luna lock recursively
for bounded Owner subagents.
