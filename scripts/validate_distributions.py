#!/usr/bin/env python3
"""Validate version authority and end-user/source-debug artifact boundaries."""

from __future__ import annotations

import argparse
import json
import tempfile
import tomllib
from pathlib import Path

from build_distributions import (
    ROOT,
    artifact_name,
    artifact_path_for_source,
    artifact_profiles,
    build,
    load_version_authority,
    plugin_root_for,
    read_json,
    resolved_specs,
    sha256,
    source_provenance,
)


SOURCE_ONLY_README_PATHS = (
    "plugins/research-routes/skills/allinluna/",
    "plugins/research-routes/runtime/allinluna_runtime/",
    "runtime/shared",
)


def _dependency_errors(plugin: dict, allinluna_version: str) -> list[str]:
    dependencies = plugin.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) != 1:
        return ["research-routes must declare exactly one All in Luna dependency"]
    dependency = dependencies[0]
    bridge = dependency.get("bridge", {}) if isinstance(dependency, dict) else {}
    expected = {
        "plugin": "allinluna",
        "version": allinluna_version,
        "required": True,
    }
    errors = [
        f"research-routes dependency {field} must be {value!r}"
        for field, value in expected.items()
        if dependency.get(field) != value
    ]
    if bridge.get("protocol") != "research-routes-bridge/v1":
        errors.append("research-routes dependency bridge protocol is invalid")
    if bridge.get("entrypoint") != "allinluna_runtime.packs:SinglePublicSkillAPI":
        errors.append("research-routes dependency bridge entrypoint is invalid")
    if bridge.get("visibility") != "private":
        errors.append("research-routes dependency bridge must be private")
    return errors


def validate(root: Path = ROOT, dist: Path | None = None) -> list[str]:
    manifest = read_json(root / "distributions" / "distribution-manifest.json")
    errors: list[str] = []
    if manifest.get("schema_version") != "2.0":
        errors.append("distribution manifest schema_version must be 2.0")
    try:
        version_path, authority = load_version_authority(root, manifest)
        specs = resolved_specs(root, manifest)
        profiles = artifact_profiles(manifest)
    except Exception as exc:
        return [f"invalid version/distribution authority: {exc}"]

    if len(specs) != 2 or {spec.get("id") for spec in specs} != {
        "all-in-luna", "research-routes"
    }:
        errors.append("manifest must define exactly all-in-luna and research-routes")
    raw_specs = manifest.get("distributions", [])
    for spec in raw_specs:
        if "version" in spec or "rc_tag" in spec:
            errors.append(f"{spec.get('id')} duplicates release/versions.json authority")
    if manifest.get("debug_source_paths") != ["tests", "evals"]:
        errors.append("source-debug profile must use tests and evals as debug-only paths")

    products = authority["products"]
    allinluna_version = products["allinluna"]["version"]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if pyproject.get("project", {}).get("version") != allinluna_version:
        errors.append("pyproject version differs from release/versions.json")
    for spec in specs:
        source_plugin = read_json(root / spec["source_plugin"] / ".codex-plugin" / "plugin.json")
        if source_plugin.get("version") != spec["version"]:
            errors.append(f"{spec['id']} source plugin version differs from release/versions.json")
        if spec["id"] == "research-routes":
            errors.extend(_dependency_errors(source_plugin, allinluna_version))

    try:
        expected_provenance = source_provenance(root)
    except Exception as exc:  # pragma: no cover - useful CLI diagnostic
        errors.append(f"cannot resolve source provenance: {exc}")
        expected_provenance = {}

    with tempfile.TemporaryDirectory(prefix="allinluna-distributions-") as temp:
        if dist is None:
            built = build(root, Path(temp))
            artifacts = {path.name: path for path in built}
        else:
            artifacts = {
                artifact_name(spec, profile): dist / artifact_name(spec, profile)
                for profile in profiles
                for spec in specs
            }
        expected_names = {
            artifact_name(spec, profile) for profile in profiles for spec in specs
        }
        if set(artifacts) != expected_names:
            errors.append(f"artifact names differ: {sorted(artifacts)}")

        inventories: dict[tuple[str, str], set[str]] = {}
        for profile in profiles:
            for spec in specs:
                artifact_id = artifact_name(spec, profile)
                artifact = artifacts[artifact_id]
                plugin_root = plugin_root_for(artifact, spec)
                required = [
                    plugin_root / ".codex-plugin/plugin.json",
                    artifact / ".agents/plugins/marketplace.json",
                    artifact / "distribution-manifest.json",
                    artifact / ".source-provenance.json",
                    artifact / "canonical-files.json",
                    artifact / "release/versions.json",
                    artifact / "LICENSE",
                ]
                if spec["id"] == "all-in-luna":
                    required.extend([
                        artifact / "README.md",
                        artifact / "README.en.md",
                        artifact / "docs/user/quickstart.md",
                        plugin_root / "runtime/allinluna_runtime/__init__.py",
                        plugin_root / "skills/allinluna/SKILL.md",
                    ])
                else:
                    required.extend([
                        artifact / "README.md",
                        artifact / "README.en.md",
                        plugin_root / "runtime/research_routes_runtime/__init__.py",
                        plugin_root
                        / "runtime/research_routes_runtime/schemas/research-pack.schema.json",
                        plugin_root / "skills/research-routes/SKILL.md",
                    ])
                for path in required:
                    if not path.is_file():
                        errors.append(f"{artifact_id} missing {path.relative_to(artifact)}")
                if any(not path.is_file() for path in required):
                    continue

                if (artifact / "release/versions.json").read_bytes() != version_path.read_bytes():
                    errors.append(f"{artifact_id} carries a stale version authority")
                plugin = read_json(plugin_root / ".codex-plugin/plugin.json")
                if plugin.get("name") != spec["plugin_name"]:
                    errors.append(f"{artifact_id} plugin name mismatch")
                if plugin.get("version") != spec["version"]:
                    errors.append(f"{artifact_id} plugin version differs from release authority")
                prompts = plugin.get("interface", {}).get("defaultPrompt")
                if not isinstance(prompts, list) or len(prompts) != 3:
                    errors.append(f"{artifact_id} must expose exactly three default prompts")
                expected_skills = (
                    "./skills/allinluna" if spec["id"] == "all-in-luna" else "./skills/"
                )
                if plugin.get("skills") != expected_skills:
                    errors.append(f"{artifact_id} skill entrypoint is invalid")
                if spec["id"] == "research-routes":
                    if (
                        plugin.get("runtime", {}).get("source")
                        != "./runtime/research_routes_runtime"
                    ):
                        errors.append(
                            f"{artifact_id} Research Routes runtime entrypoint is invalid"
                        )
                    errors.extend(
                        f"{artifact_id}: {error}"
                        for error in _dependency_errors(plugin, allinluna_version)
                    )
                    forbidden = [
                        plugin_root / "skills/allinluna",
                        plugin_root / "runtime/allinluna_runtime",
                    ]
                    for path in forbidden:
                        if path.exists():
                            errors.append(
                                f"{artifact_id} duplicates public All in Luna path "
                                f"{path.relative_to(artifact)}"
                            )

                marketplace = read_json(artifact / ".agents/plugins/marketplace.json")
                entries = marketplace.get("plugins", [])
                expected_source = (
                    "./plugins/research-routes"
                    if spec["id"] == "research-routes"
                    else "./."
                )
                if marketplace.get("name") != spec["plugin_name"] or len(entries) != 1:
                    errors.append(f"{artifact_id} standalone marketplace identity is invalid")
                elif entries[0].get("source", {}).get("path") != expected_source:
                    errors.append(f"{artifact_id} standalone marketplace source path is invalid")

                provenance = read_json(artifact / ".source-provenance.json")
                if provenance != expected_provenance:
                    errors.append(f"{artifact_id} source provenance does not match current HEAD")
                artifact_manifest = read_json(artifact / "distribution-manifest.json")
                expected_fields = {
                    "distribution_id": spec["id"],
                    "artifact_profile": profile["id"],
                    "public_artifact": profile.get("public") is True,
                    "includes_debug_sources": profile.get("include_debug_sources") is True,
                    "plugin_name": spec["plugin_name"],
                    "version": spec["version"],
                    "release_status": authority["release_status"],
                    "rc_tag": spec["rc_tag"],
                    "tag_owner": authority["tag_policy"]["owner"],
                    "tag_timing": authority["tag_policy"]["timing"],
                    "tag_creation_authorized": False,
                }
                for field, expected in expected_fields.items():
                    if artifact_manifest.get(field) != expected:
                        errors.append(f"{artifact_id} artifact {field} is stale or mismatched")
                version_ref = artifact_manifest.get("version_authority", {})
                if version_ref != {
                    "path": "release/versions.json",
                    "product": spec["version_key"],
                    "sha256": sha256(version_path),
                }:
                    errors.append(f"{artifact_id} version authority receipt is invalid")
                if artifact_manifest.get("provenance") != expected_provenance:
                    errors.append(f"{artifact_id} artifact provenance does not match current HEAD")
                if (artifact / "LICENSE").read_bytes() != (root / "LICENSE").read_bytes():
                    errors.append(f"{artifact_id} LICENSE differs from source LICENSE")

                inventory = read_json(artifact / "canonical-files.json")
                inventory_entries = inventory.get("files", [])
                sources = [entry.get("source") for entry in inventory_entries]
                inventories[(spec["id"], profile["id"])] = set(sources)
                if inventory.get("schema_version") != "3.0":
                    errors.append(f"{artifact_id} canonical inventory schema is stale")
                if len(sources) != len(set(sources)):
                    errors.append(f"{artifact_id} canonical source inventory contains duplicates")
                allowed_prefixes = (f"{spec['source_plugin']}/",)
                if profile["id"] == "source-debug":
                    allowed_prefixes += ("tests/", "evals/")
                for entry in inventory_entries:
                    source = entry.get("source")
                    expected_hash = entry.get("sha256")
                    if not isinstance(source, str) or not source.startswith(allowed_prefixes):
                        errors.append(f"{artifact_id} canonical source path is invalid: {source!r}")
                        continue
                    try:
                        artifact_path = artifact_path_for_source(plugin_root, spec, source)
                    except ValueError as exc:
                        errors.append(str(exc))
                        continue
                    if not artifact_path.is_file():
                        errors.append(f"{artifact_id} canonical file is missing: {source}")
                    elif sha256(artifact_path) != expected_hash:
                        errors.append(f"{artifact_id} canonical file hash mismatch: {source}")

                debug_paths = (plugin_root / "tests", plugin_root / "evals")
                if profile["id"] == "end-user" and any(path.exists() for path in debug_paths):
                    errors.append(f"{artifact_id} end-user artifact contains tests or evals")
                if profile["id"] == "source-debug" and any(
                    not path.is_dir() for path in debug_paths
                ):
                    errors.append(f"{artifact_id} source-debug artifact is missing tests or evals")
                if spec["id"] == "research-routes":
                    for readme_name in ("README.md", "README.en.md"):
                        readme = (artifact / readme_name).read_text(encoding="utf-8")
                        for source_only_path in SOURCE_ONLY_README_PATHS:
                            if source_only_path in readme:
                                errors.append(
                                    f"{artifact_id} {readme_name} contains duplicate path "
                                    f"{source_only_path}"
                                )

        for spec in specs:
            end_user = inventories.get((spec["id"], "end-user"), set())
            source_debug = inventories.get((spec["id"], "source-debug"), set())
            if not end_user or not end_user < source_debug:
                errors.append(f"{spec['id']} source-debug inventory must extend end-user inventory")
            if any(path.startswith(("tests/", "evals/")) for path in end_user):
                errors.append(f"{spec['id']} end-user inventory contains debug sources")
            if not any(path.startswith("tests/") for path in source_debug):
                errors.append(f"{spec['id']} source-debug inventory has no tests")
            if not any(path.startswith("evals/") for path in source_debug):
                errors.append(f"{spec['id']} source-debug inventory has no evals")
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
