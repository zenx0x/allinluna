# Phase 2 acceptance evidence

Phase 2 repository closure uses the public `allinluna` compiler/CLI, strict
Store-backed receipts, a single HandoffProcessor, and canonical Core state,
reference, and protocol modules. Host resource-route telemetry is optional
diagnostics. Its absence leaves the actual resource state unresolved, but does
not prevent normal execution, handoff, or root-result completion.

## Core Slim boundaries

`TaskGraph` is the sole compiled-graph structure and validation authority; the
legacy Pack spelling `CompiledRunGraph` is an alias to that same type. The
runtime root has an explicit export surface. SQLite lifecycle and transactions
remain in `store.py`; repositories, resource claims, dispatch/receipt storage,
cross-entity services, observability, and scheduler read models live in their
respective Store-domain modules. `ResourceObservation` is the sole receipt
model and normalizes actual to unresolved without explicit host evidence.

Evidence collection is independent from Lane self-reporting. Checks run only
through the bounded command runner: timeout and execution errors are persisted
as negative evidence. Raw Python check callables are rejected rather than
allowed to ignore a cooperative timeout and hang the runtime.

## Scale report

Measured on Windows with Python 3.11 and a real temporary SQLite database:

| Workload | Result | Wall time | Peak memory / database |
|---|---:|---:|---:|
| 10,000 Signals | 10,000 persisted | 0.515 s | combined peak 63,836 B |
| 10,000 Artifacts | 10,000 metadata + payloads | 8.070 s | DB 6,381,568 B |
| Global scheduler, 100 Tasks | 100 ready | 0.051 s | 579,932 B |
| Global scheduler, 1,000 Tasks | 1,000 ready | 0.030 s | 1,455,171 B |
| Global scheduler, 10,000 Tasks | 10,000 ready | 0.292 s | 13,051,972 B |
| Local scheduler, 1,000 WorkUnits | 1,000 ready | 0.031 s | 1,401,831 B |

The global readiness query count is constant at seven SELECTs for 100, 1,000,
and 10,000 Tasks. The Lane report uses fifteen SELECTs for 1,000 WorkUnits.
Context reconstruction at depths 10/100/1000 uses two cold SELECTs, with a
maximum measured cold time of 0.041 s and a 1,001-snapshot chain.

Reproduce with:

```text
python scripts/benchmark_store_scale.py
python scripts/benchmark_scheduler.py
python scripts/benchmark_context.py
python scripts/validate_core_slim.py
```

## Host resource diagnostics

The canonical runtime keeps requested, resolved, and actual resource values
separate. The host adapter records actual only when it receives explicit model
and reasoning evidence with a valid source and timestamp; otherwise it persists
`actual: null` and `actual_state: unresolved`. Optional route diagnostics are
adapter-scoped and may validate host-specific event streams, but are never a
Core dependency or an acceptance prerequisite.

An unresolved actual resource state is not a blocker. A task completes through
its contract and verified handoff/result path, and the Coordinator may complete
the root run once its required Tasks are complete. The persisted resource state
remains inspectable without fabricating actual model or reasoning values.
