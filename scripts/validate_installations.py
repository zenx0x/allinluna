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
            marketplace_path = artifact / ".agents" / "plugins" / "marketplace.json"
            if not marketplace_path.is_file():
                errors.append(f"missing marketplace manifest in {artifact}")
                continue
            marketplace = read_json(marketplace_path)
            entries = marketplace.get("plugins", [])
            if len(entries) != 1:
                errors.append(f"marketplace must have one plugin entry in {artifact}")
                continue
            source_path = entries[0].get("source", {}).get("path", "")
            if not source_path.startswith("./"):
                errors.append(f"marketplace source path must be relative: {source_path}")
                continue
            plugin_path = (artifact / source_path[2:]).resolve() / ".codex-plugin" / "plugin.json"
            if not plugin_path.is_file() or artifact.resolve() not in plugin_path.parents:
                errors.append(f"marketplace plugin path escapes or is missing in {artifact}")
                continue
            plugin = read_json(plugin_path)
            name = plugin.get("name")
            if not name:
                errors.append(f"plugin name missing in {artifact}")
                continue
            destination = install_root / name
            shutil.copytree(artifact, destination)
            installed_plugin_root = (destination / source_path[2:]).resolve()
            if destination.resolve() not in installed_plugin_root.parents and installed_plugin_root != destination.resolve():
                errors.append(f"installed marketplace path escapes {destination}")
                continue
            snapshots[name] = (installed_plugin_root / ".codex-plugin/plugin.json").read_bytes()
        names = set(snapshots)
        if names != {"allinluna", "research-routes"}:
            errors.append(f"co-installation names are {sorted(names)}")
        if snapshots.get("allinluna") == snapshots.get("research-routes"):
            errors.append("co-installed plugin manifests must remain distinct")
        for name in names:
            marketplace = read_json(install_root / name / ".agents/plugins/marketplace.json")
            source_path = marketplace["plugins"][0]["source"]["path"]
            installed_plugin_root = install_root / name / source_path[2:]
            if not (installed_plugin_root / "skills").is_dir():
                errors.append(f"{name} has no installable skills directory")
            if not (install_root / name / "canonical-files.json").is_file():
                errors.append(f"{name} has no canonical source manifest")
            if not (installed_plugin_root / "runtime" / "allinluna_runtime").is_dir():
                errors.append(f"{name} has no canonical runtime")
            if (installed_plugin_root / "shared").exists() or (installed_plugin_root / "runtime" / "shared").exists():
                errors.append(f"{name} contains a forbidden duplicate shared runtime")
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
