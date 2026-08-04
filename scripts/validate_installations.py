#!/usr/bin/env python3
"""Validate that both built plugins can be installed without overwriting each other."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
import shutil

from build_distributions import ROOT, build, read_json


def validate(root: Path = ROOT, dist: Path | None = None) -> list[str]:
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="allinluna-install-") as temp:
        build_dir = Path(temp) / "build"
        artifacts = build(root, build_dir) if dist is None else [dist / "all-in-luna", dist / "research-routes"]
        install_root = Path(temp) / "plugins"
        snapshots: dict[str, bytes] = {}
        for artifact in artifacts:
            plugin_path = artifact / ".codex-plugin/plugin.json"
            if not plugin_path.is_file():
                errors.append(f"missing plugin manifest in {artifact}")
                continue
            plugin = read_json(plugin_path)
            name = plugin.get("name")
            if not name:
                errors.append(f"plugin name missing in {artifact}")
                continue
            destination = install_root / name
            shutil.copytree(artifact, destination)
            snapshots[name] = (destination / ".codex-plugin/plugin.json").read_bytes()
        names = set(snapshots)
        if names != {"allinluna", "research-routes"}:
            errors.append(f"co-installation names are {sorted(names)}")
        if snapshots.get("allinluna") == snapshots.get("research-routes"):
            errors.append("co-installed plugin manifests must remain distinct")
        for name in names:
            if not (install_root / name / "skills").is_dir():
                errors.append(f"{name} has no installable skills directory")
            if not (install_root / name / "shared-files.json").is_file():
                errors.append(f"{name} has no shared parity manifest")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    errors = validate(args.root.resolve(), args.dist.resolve() if args.dist else None)
    print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
