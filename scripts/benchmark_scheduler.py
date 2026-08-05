"""Real SQLite scheduler scale benchmark for Phase 2 acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from time import perf_counter
import tracemalloc
from datetime import datetime, timezone
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.scheduler.global_scheduler import GlobalScheduler
from allinluna_runtime.scheduler.local_scheduler import LocalScheduler
from allinluna_runtime.store import Store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _seed_tasks(store: Store, run_id: str, count: int) -> None:
    store.create_run(run_id, f"scheduler benchmark {count}")
    now = _now()
    contracts = [
        (f"contract-{run_id}-{i}", 1, f"task-{run_id}-{i}", "benchmark", "[]", "[]", '["scheduled"]', "{}", "{}", "{}", now)
        for i in range(count)
    ]
    tasks = [
        (f"task-{run_id}-{i}", run_id, "benchmark", "ready", i % 5, 1, f"contract-{run_id}-{i}", 1, now, now, f"task-{i}", "{}")
        for i in range(count)
    ]
    with store.transaction():
        store.connection.executemany(
            "INSERT INTO contracts(id,version,task_id,outcome,imports_json,exports_json,done_when_json,ownership_json,permissions_json,context_policy_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            contracts,
        )
        store.connection.executemany(
            "INSERT INTO tasks(id,run_id,outcome,state,priority,required,contract_id,contract_version,created_at,updated_at,local_id,resource_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            tasks,
        )


def run(database: Path, sizes: list[int], lane_units: int) -> dict[str, object]:
    with Store(database) as store:
        for size in sizes:
            _seed_tasks(store, f"run-{size}", size)
        lane_task = f"task-run-{sizes[0]}-0"
        now = _now()
        with store.transaction():
            store.connection.executemany(
                "INSERT INTO work_units(id,task_id,objective,state,ownership_json,return_contract,created_at,updated_at,local_id,resource_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(f"bench-unit-{i}", lane_task, "benchmark", "ready", "{}", "work-handoff/v1", now, now, f"unit-{i}", "{}") for i in range(lane_units)],
            )

        results: dict[str, object] = {}
        for size in sizes:
            selects = 0

            def trace(statement: str) -> None:
                nonlocal selects
                if statement.lstrip().upper().startswith("SELECT"):
                    selects += 1

            store.connection.set_trace_callback(trace)
            tracemalloc.start()
            started = perf_counter()
            ready = GlobalScheduler(store).ready_tasks(f"run-{size}")
            elapsed = perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            store.connection.set_trace_callback(None)
            results[str(size)] = {
                "ready": len(ready), "wall_seconds": elapsed,
                "select_queries": selects, "peak_memory_bytes": peak,
            }

        local_selects = 0

        def trace_local(statement: str) -> None:
            nonlocal local_selects
            if statement.lstrip().upper().startswith("SELECT"):
                local_selects += 1

        store.connection.set_trace_callback(trace_local)
        tracemalloc.start()
        started = perf_counter()
        local_ready = LocalScheduler(store, lane_task).ready_units()
        local_elapsed = perf_counter() - started
        _, local_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        store.connection.set_trace_callback(None)
        results["lane"] = {
            "work_units": len(local_ready), "wall_seconds": local_elapsed,
            "select_queries": local_selects, "peak_memory_bytes": local_peak,
        }
    return {"database_bytes": database.stat().st_size, "global": results, "database": str(database)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--sizes", default="100,1000,10000")
    parser.add_argument("--lane-units", type=int, default=1000)
    args = parser.parse_args()
    sizes = [int(item) for item in args.sizes.split(",") if item]
    if args.database:
        result = run(args.database, sizes, args.lane_units)
    else:
        with tempfile.TemporaryDirectory(prefix="allinluna-benchmark-") as folder:
            result = run(Path(folder) / "runtime.db", sizes, args.lane_units)
            result["database"] = "temporary"
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
