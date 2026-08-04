#!/usr/bin/env python3
"""Check that the repository has one canonical runtime source tree.

Distribution builds copy this tree into temporary artifacts; this command is a
read-only consistency check and never writes a second runtime into the source.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "plugins" / "allinluna" / "runtime" / "allinluna_runtime"


def sync(write: bool = False) -> dict[str, object]:
    del write
    errors: list[str] = []
    if not CANONICAL.is_dir():
        errors.append("canonical runtime is missing")
    duplicate_paths = [ROOT / "shared", ROOT / "plugins" / "allinluna" / "runtime" / "shared"]
    errors.extend(f"forbidden duplicate: {path.relative_to(ROOT)}" for path in duplicate_paths if path.exists())
    return {"ok": not errors, "write": False, "errors": errors, "canonical": str(CANONICAL.relative_to(ROOT))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="retained for compatibility; no source writes are performed")
    args = parser.parse_args()
    result = sync(args.write)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
