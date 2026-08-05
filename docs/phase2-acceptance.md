# Phase 2 acceptance evidence

Phase 2 repository closure uses the public `allinluna` compiler/CLI, strict
Store-backed receipts, a single HandoffProcessor, and canonical Core state,
reference, and protocol modules. Host-observed model evidence remains external:
an acceptance route is unresolved until Codex Desktop supplies the canonical
`resource_receipt` fields.

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

## External host acceptance

The authoritative resource source is the App Server event stream exported by
the Codex Desktop host. The outer receipt remains
`source=codex_app` / `actual_tool=codex_app__create_thread`; its nested route
evidence contains `thread/start`, zero or more `model/rerouted` events, and
matching `turn/started` / `turn/completed` notifications. A response from a
separately launched `codex.exe app-server` belongs to another host session and
is rejected as Desktop acceptance evidence.

Desktop can create Luna medium, Luna xhigh, Luna max, and Codex Spark tasks.
No route is classified as unsupported. The remaining external blocker is only
receipt visibility: the currently exposed Desktop create/read wrapper does not
return the raw model, reasoning, reroute, and turn lifecycle evidence needed to
assemble the four canonical receipts.
