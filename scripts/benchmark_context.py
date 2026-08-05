"""Real Context Kernel COW-chain benchmark for Phase 2 acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from time import perf_counter
import tracemalloc
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.context import ContextKernel


def run(database: Path, depths: list[int]) -> dict[str, object]:
    kernel = ContextKernel(database)
    results: dict[str, object] = {}
    try:
        for depth in depths:
            current = kernel.build(scope="task", scope_id=f"depth-{depth}", content={"known_facts": ["root"]})
            for index in range(depth):
                current = kernel.derive(current, {"active_work": [f"step-{index}"]})
            selects = 0

            def trace(statement: str) -> None:
                nonlocal selects
                if statement.lstrip().upper().startswith("SELECT"):
                    selects += 1

            kernel.connection.set_trace_callback(trace)
            tracemalloc.start()
            started = perf_counter()
            content = kernel.reconstruct_content(current.snapshot_ref)
            cold_seconds = perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            cold_selects = selects
            started = perf_counter()
            assert kernel.reconstruct_content(current.snapshot_ref) == content
            hot_seconds = perf_counter() - started
            kernel.connection.set_trace_callback(None)
            results[str(depth)] = {
                "snapshot_depth": depth + 1,
                "cold_wall_seconds": cold_seconds,
                "hot_wall_seconds": hot_seconds,
                "cold_select_queries": cold_selects,
                "hot_select_queries": selects - cold_selects,
                "peak_memory_bytes": peak,
                "token_estimate": current.token_estimate,
            }
        return {
            "database": str(database), "database_bytes": database.stat().st_size,
            "depths": results, "metrics": kernel.metrics(),
        }
    finally:
        kernel.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--depths", default="10,100,1000")
    args = parser.parse_args()
    depths = [int(item) for item in args.depths.split(",") if item]
    if args.database:
        result = run(args.database, depths)
    else:
        with tempfile.TemporaryDirectory(prefix="allinluna-context-benchmark-") as folder:
            result = run(Path(folder) / "runtime.db", depths)
            result["database"] = "temporary"
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
