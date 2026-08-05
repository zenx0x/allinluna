"""10k Signal/Artifact Store benchmark with DB and payload size evidence."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from time import perf_counter
import tracemalloc
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "plugins" / "allinluna" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from allinluna_runtime.artifacts import ArtifactStore
from allinluna_runtime.store import Store


def run(database: Path, *, signals: int = 10_000, artifacts: int = 10_000) -> dict[str, object]:
    payload_root = database.parent / "artifacts"
    with Store(database) as store:
        store.create_run("run-store-scale", "10k Store scale")
        artifact_store = ArtifactStore(store, root=payload_root)
        tracemalloc.start()
        started = perf_counter()
        with store.transaction():
            for index in range(signals):
                store.append_signal("run-store-scale", "PROGRESS_PULSE", {"index": index})
        signal_seconds = perf_counter() - started
        started = perf_counter()
        with store.transaction():
            for index in range(artifacts):
                artifact_store.put(f"artifact-{index}".encode(), kind="summary", produced_by="benchmark")
        artifact_seconds = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        signal_count = len(store.read_signals("run-store-scale", limit=signals + 1))
        artifact_count = len(store.inspect_artifacts())
    payload_bytes = sum(path.stat().st_size for path in payload_root.iterdir())
    return {
        "signals": {"count": signal_count, "wall_seconds": signal_seconds},
        "artifacts": {"count": artifact_count, "wall_seconds": artifact_seconds, "payload_bytes": payload_bytes},
        "database_bytes": database.stat().st_size,
        "peak_memory_bytes": peak,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--signals", type=int, default=10_000)
    parser.add_argument("--artifacts", type=int, default=10_000)
    args = parser.parse_args()
    if args.database:
        result = run(args.database, signals=args.signals, artifacts=args.artifacts)
    else:
        with tempfile.TemporaryDirectory(prefix="allinluna-store-scale-") as folder:
            result = run(Path(folder) / "runtime.db", signals=args.signals, artifacts=args.artifacts)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["signals"]["count"] == args.signals and result["artifacts"]["count"] == args.artifacts else 1


if __name__ == "__main__":
    raise SystemExit(main())
