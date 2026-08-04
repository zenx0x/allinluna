#!/usr/bin/env python3
"""Resolve scoped workflow presets and per-run overrides."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from workflow_presets import normalize_preset, resolve_preset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preset", type=Path)
    parser.add_argument("--override", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        source = json.loads(args.preset.read_text(encoding="utf-8"))
        override = json.loads(args.override.read_text(encoding="utf-8")) if args.override else None
        result = normalize_preset(resolve_preset(source, overrides=override))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    print(json.dumps({"valid": True, "preset": result}, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
