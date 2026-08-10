#!/usr/bin/env python3
"""Validate co-installation without duplicate public Skills or runtimes."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from build_distributions import ROOT, build, load_version_authority, read_json, resolved_specs


def validate(root: Path = ROOT, dist: Path | None = None) -> list[str]:
    errors: list[str] = []
    manifest = read_json(root / "distributions" / "distribution-manifest.json")
    try:
        version_path, authority = load_version_authority(root, manifest)
        specs = resolved_specs(root, manifest)
    except Exception as exc:
        return [f"invalid version/distribution authority: {exc}"]
    expected_versions = {spec["plugin_name"]: spec["version"] for spec in specs}

    with tempfile.TemporaryDirectory(prefix="allinluna-install-") as temp:
        build_dir = Path(temp) / "build"
        artifacts = (
            build(root, build_dir, profiles=["end-user"])
            if dist is None
            else [dist / "all-in-luna", dist / "research-routes"]
        )
        install_root = Path(temp) / "plugins"
        installed_roots: dict[str, Path] = {}
        snapshots: dict[str, bytes] = {}
        for artifact in artifacts:
            marketplace_path = artifact / ".agents/plugins/marketplace.json"
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
            plugin_path = (artifact / source_path[2:]).resolve() / ".codex-plugin/plugin.json"
            if not plugin_path.is_file() or artifact.resolve() not in plugin_path.parents:
                errors.append(f"marketplace plugin path escapes or is missing in {artifact}")
                continue
            plugin = read_json(plugin_path)
            name = plugin.get("name")
            if name not in expected_versions:
                errors.append(f"unexpected plugin identity in {artifact}: {name}")
                continue
            if plugin.get("version") != expected_versions[name]:
                errors.append(f"{name} plugin version differs from release/versions.json")
            destination = install_root / name
            shutil.copytree(artifact, destination)
            installed_plugin_root = (destination / source_path[2:]).resolve()
            if (
                destination.resolve() not in installed_plugin_root.parents
                and installed_plugin_root != destination.resolve()
            ):
                errors.append(f"installed marketplace path escapes {destination}")
                continue
            installed_roots[name] = installed_plugin_root
            snapshots[name] = (installed_plugin_root / ".codex-plugin/plugin.json").read_bytes()

        names = set(installed_roots)
        if names != {"allinluna", "research-routes"}:
            errors.append(f"co-installation names are {sorted(names)}")
            return errors
        if snapshots["allinluna"] == snapshots["research-routes"]:
            errors.append("co-installed plugin manifests must remain distinct")

        luna_root = installed_roots["allinluna"]
        routes_root = installed_roots["research-routes"]
        if not (luna_root / "skills/allinluna/SKILL.md").is_file():
            errors.append("All in Luna public Skill is missing")
        if not (luna_root / "runtime/allinluna_runtime").is_dir():
            errors.append("All in Luna runtime is missing")
        if not (routes_root / "skills/research-routes/SKILL.md").is_file():
            errors.append("Research Routes public Skill is missing")
        if not (routes_root / "runtime/research_routes_runtime").is_dir():
            errors.append("Research Routes runtime is missing")
        for forbidden in (
            routes_root / "skills/allinluna",
            routes_root / "runtime/allinluna_runtime",
        ):
            if forbidden.exists():
                errors.append(f"Research Routes duplicates All in Luna at {forbidden}")

        luna_skill = (luna_root / "skills/allinluna/SKILL.md").read_text(encoding="utf-8")
        if not all(
            needle in luna_skill
            for needle in ("not hard", "locked", "resource_receipt.requested")
        ):
            errors.append(
                "installed All in Luna Skill lacks configurable-resource receipt guidance"
            )
        routes_plugin = read_json(routes_root / ".codex-plugin/plugin.json")
        dependencies = routes_plugin.get("dependencies", [])
        if len(dependencies) != 1:
            errors.append("Research Routes has no singular All in Luna dependency")
        else:
            dependency = dependencies[0]
            bridge = dependency.get("bridge", {})
            if dependency.get("plugin") != "allinluna":
                errors.append(
                    "Research Routes dependency does not resolve to installed All in Luna"
                )
            if dependency.get("version") != expected_versions["allinluna"]:
                errors.append("Research Routes dependency version is stale")
            if dependency.get("required") is not True:
                errors.append("Research Routes All in Luna dependency must be required")
            if (
                bridge.get("protocol") != "research-routes-bridge/v1"
                or bridge.get("visibility") != "private"
            ):
                errors.append("Research Routes private bridge metadata is invalid")

        for name, plugin_root in installed_roots.items():
            artifact_root = install_root / name
            if (plugin_root / "tests").exists() or (plugin_root / "evals").exists():
                errors.append(f"{name} end-user installation contains tests or evals")
            if not (artifact_root / "canonical-files.json").is_file():
                errors.append(f"{name} has no canonical source manifest")
            artifact_manifest_path = artifact_root / "distribution-manifest.json"
            if not artifact_manifest_path.is_file():
                errors.append(f"{name} has no distribution release manifest")
                continue
            artifact_manifest = read_json(artifact_manifest_path)
            if artifact_manifest.get("artifact_profile") != "end-user":
                errors.append(f"{name} installation is not an end-user artifact")
            if artifact_manifest.get("public_artifact") is not True:
                errors.append(f"{name} end-user artifact is not public")
            if artifact_manifest.get("includes_debug_sources") is not False:
                errors.append(f"{name} end-user artifact contains debug sources")
            if artifact_manifest.get("version") != expected_versions[name]:
                errors.append(f"{name} distribution manifest version is stale")
            if artifact_manifest.get("release_status") != authority["release_status"]:
                errors.append(f"{name} distribution is not marked release-candidate")
            if (
                artifact_manifest.get("tag_owner") != "T6"
                or artifact_manifest.get("tag_timing") != "after merged main"
            ):
                errors.append(f"{name} distribution tag boundary is invalid")
            if artifact_manifest.get("tag_creation_authorized") is not False:
                errors.append(f"{name} artifact improperly authorizes tag creation")
            installed_versions = artifact_root / "release/versions.json"
            if (
                not installed_versions.is_file()
                or installed_versions.read_bytes() != version_path.read_bytes()
            ):
                errors.append(f"{name} installed version authority is missing or stale")
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
