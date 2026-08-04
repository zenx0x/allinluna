#!/usr/bin/env python3
"""CLI wrapper for the repository-shared launch confirmation builder."""
from __future__ import annotations
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[3] / "runtime"
sys.path.insert(0, str(RUNTIME))
from shared.launch import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
