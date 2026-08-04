#!/usr/bin/env python3
"""Synchronize the canonical shared runtime into each independently installable plugin."""
from __future__ import annotations

import argparse
import filecmp
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "shared"
DISTROS = (ROOT / "plugins" / "allinluna",)


def sync(write: bool = False) -> dict[str, object]:
    files = sorted(path for path in SOURCE.iterdir() if path.is_file() and path.suffix in {".py", ".json"})
    mismatches: list[str] = []
    for distro in DISTROS:
        target = distro / "runtime" / "shared"
        target.mkdir(parents=True, exist_ok=True)
        for source in files:
            destination = target / source.name
            if write:
                shutil.copy2(source, destination)
            elif not destination.is_file() or not filecmp.cmp(source, destination, shallow=False):
                mismatches.append(str(destination.relative_to(ROOT)))
    return {"ok": not mismatches, "write": write, "mismatches": mismatches,
            "distributions": [str(path.relative_to(ROOT)) for path in DISTROS]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    import json
    print(json.dumps(sync(args.write), indent=2))
    return 0 if sync(False)["ok"] or args.write else 1


if __name__ == "__main__":
    raise SystemExit(main())
