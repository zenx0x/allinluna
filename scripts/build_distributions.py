#!/usr/bin/env python3
"""Build the two co-installable All in Luna distributions deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "distributions" / "distribution-manifest.json"
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDED_FILES = {".DS_Store", "Thumbs.db"}


def is_release_file(path: Path) -> bool:
    return not (
        any(part in EXCLUDED_DIRS for part in path.parts)
        or path.name in EXCLUDED_FILES
        or path.suffix.lower() in {".pyc", ".pyo"}
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def git_value(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def source_provenance(root: Path) -> dict[str, str]:
    head = git_value(root, "rev-parse", "HEAD")
    return {
        "source_commit": head,
        "source_tree": git_value(root, "rev-parse", f"{head}^{{tree}}"),
        "source_parent": git_value(root, "rev-parse", f"{head}^"),
        "source_ref": git_value(root, "branch", "--show-current") or "HEAD",
    }


def expand_sources(root: Path, entries: list[str]) -> list[tuple[str, Path]]:
    expanded: list[tuple[str, Path]] = []
    for entry in entries:
        source = root / entry
        if not source.exists():
            raise FileNotFoundError(f"shared source does not exist: {entry}")
        if source.is_file():
            expanded.append((entry, source))
        else:
            for child in sorted(p for p in source.rglob("*") if p.is_file() and is_release_file(p)):
                expanded.append((child.relative_to(root).as_posix(), child))
    return expanded


def shared_inventory(root: Path, manifest: dict) -> dict[str, list[dict[str, str]]]:
    inventory: dict[str, list[dict[str, str]]] = {}
    for category, entries in manifest["shared_paths"].items():
        inventory[category] = [
            {"source": relative, "sha256": sha256(path)}
            for relative, path in expand_sources(root, entries)
        ]
    return inventory


def copy_tree(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                "*.pyc", "*.pyo", ".DS_Store", "Thumbs.db",
            ),
        )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def build_distribution(root: Path, output: Path, manifest: dict, spec: dict, provenance: dict) -> Path:
    artifact = output / spec["id"]
    if artifact.exists():
        shutil.rmtree(artifact)
    artifact.mkdir(parents=True)
    source_plugin = root / manifest["source_plugin"]
    copy_tree(source_plugin / "skills", artifact / "skills")
    overlay_plugin = root / "plugins" / spec["plugin_name"]
    if (overlay_plugin / "skills").is_dir():
        copy_tree(overlay_plugin / "skills", artifact / "skills")
    copy_tree(root / "tests", artifact / "tests")
    copy_tree(root / "evals", artifact / "evals")
    copy_tree(root / "LICENSE", artifact / "LICENSE")

    overlay = root / spec["overlay"]
    if not overlay.is_dir():
        raise FileNotFoundError(f"overlay does not exist: {spec['overlay']}")
    overlay_target = artifact / "overlay"
    for source in sorted(p for p in overlay.rglob("*") if p.is_file() and is_release_file(p)):
        relative = source.relative_to(overlay)
        if relative.parts[0] == "skills":
            continue
        if relative.name in {"README.md", "README.en.md"}:
            copy_tree(source, artifact / relative.name)
        else:
            copy_tree(source, overlay_target / relative)

    metadata_root = root / "plugins" / spec["plugin_name"]
    metadata_path = metadata_root / ".codex-plugin" / "plugin.json"
    source_plugin_json = read_json(metadata_path if metadata_path.is_file() else source_plugin / ".codex-plugin" / "plugin.json")
    plugin = {
        **source_plugin_json,
        "name": spec["plugin_name"],
        "description": f"{spec['display_name']}: {read_json(overlay / 'brand.json')['purpose']}",
        "skills": "./skills/",
        "interface": {
            **source_plugin_json.get("interface", {}),
            "displayName": spec["display_name"],
            "shortDescription": read_json(overlay / "brand.json")["tagline"],
        },
    }
    plugin_dir = artifact / ".codex-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(plugin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    marketplace_dir = artifact / ".agents" / "plugins"
    marketplace_dir.mkdir(parents=True, exist_ok=True)
    marketplace = {
        "name": spec["plugin_name"],
        "interface": {"displayName": spec["display_name"]},
        "plugins": [
            {
                "name": plugin["name"],
                "source": {"source": "local", "path": "."},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": plugin.get("interface", {}).get("category", "Productivity"),
            }
        ],
    }
    (marketplace_dir / "marketplace.json").write_text(
        json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    inventory = shared_inventory(root, manifest)
    for category, entries in manifest["shared_paths"].items():
        for relative, path in expand_sources(root, entries):
            if relative.startswith("plugins/allinluna/skills/") or relative in {"tests", "evals"} or relative.startswith("tests/") or relative.startswith("evals/"):
                continue
            copy_tree(path, artifact / "shared" / category / Path(relative).name)
    shared_files = [item for items in inventory.values() for item in items]
    (artifact / "shared-files.json").write_text(
        json.dumps({"schema_version": "1.0", "files": shared_files}, indent=2) + "\n",
        encoding="utf-8",
    )
    (artifact / ".source-provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    artifact_manifest = {
        "schema_version": "1.0",
        "distribution_id": spec["id"],
        "plugin_name": spec["plugin_name"],
        "display_name": spec["display_name"],
        "shared_categories": sorted(inventory),
        "overlay": spec["overlay"],
        "provenance": provenance,
    }
    (artifact / "distribution-manifest.json").write_text(
        json.dumps(artifact_manifest, indent=2) + "\n", encoding="utf-8"
    )
    return artifact


def build(root: Path = ROOT, output: Path | None = None) -> list[Path]:
    manifest = read_json(root / "distributions" / "distribution-manifest.json")
    output = output or (root / "dist")
    output.mkdir(parents=True, exist_ok=True)
    provenance = source_provenance(root)
    return [build_distribution(root, output, manifest, spec, provenance) for spec in manifest["distributions"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    artifacts = build(ROOT, args.output.resolve())
    print(json.dumps({"built": [str(path) for path in artifacts]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
