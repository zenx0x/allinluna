#!/usr/bin/env python3
"""Resolve ordered capability bindings against a runtime capability catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from capability_router import CapabilityRouter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bindings", type=Path)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        bindings = json.loads(args.bindings.read_text(encoding="utf-8"))
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        registry = catalog.get("capabilities", [])
        result = CapabilityRouter(registry).resolve(
            bindings,
            availability=catalog.get("availability", {}),
            permissions=catalog.get("live_permissions", {}),
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
