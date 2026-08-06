#!/usr/bin/env python3
"""Validate dual-distribution parity, overlays, and commit provenance."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from build_distributions import ROOT, build, expand_sources, is_rc_version, plugin_root_for, read_json, sha256, source_provenance


SOURCE_ONLY_README_PATHS = ("shared/", "runtime/shared", "allinluna-plan", "allinluna-run")
EXPECTED_RELEASE = {
    "status": "release-candidate",
    "stable_release": False,
    "tag_owner": "T6",
    "tag_timing": "after merged main",
}
def validate(root: Path = ROOT, dist: Path | None = None) -> list[str]:
    manifest = read_json(root / "distributions" / "distribution-manifest.json")
    errors: list[str] = []
    specs = manifest.get("distributions", [])
    if len(specs) != 2 or {spec.get("id") for spec in specs} != {"all-in-luna", "research-routes"}:
        errors.append("manifest must define exactly all-in-luna and research-routes")
    release = manifest.get("release", {})
    for field, expected in EXPECTED_RELEASE.items():
        if release.get(field) != expected:
            errors.append(f"release.{field} must be {expected!r}")
    for spec in specs:
        version = spec.get("version")
        plugin_name = spec.get("plugin_name")
        if not is_rc_version(version):
            errors.append(f"{spec.get('id')} manifest version must be a semantic RC version")
        if spec.get("rc_tag") != f"{plugin_name}/{version}":
            errors.append(f"{spec.get('id')} manifest rc_tag must match plugin_name/version")
    if not manifest.get("overlay_allowlist") or not manifest.get("canonical_paths"):
        errors.append("overlay allowlist is missing")
    try:
        expected_provenance = source_provenance(root)
    except Exception as exc:  # pragma: no cover - useful CLI diagnostic
        errors.append(f"cannot resolve source provenance: {exc}")
        expected_provenance = {}
    with tempfile.TemporaryDirectory(prefix="allinluna-distributions-") as temp:
        built = build(root, Path(temp)) if dist is None else [dist / spec["id"] for spec in specs]
        inventories: list[dict] = []
        for artifact, spec in zip(built, specs, strict=True):
            plugin_root = plugin_root_for(artifact, spec)
            required = [plugin_root / ".codex-plugin/plugin.json", artifact / ".agents/plugins/marketplace.json", artifact / "distribution-manifest.json", artifact / ".source-provenance.json", artifact / "canonical-files.json", artifact / "LICENSE", plugin_root / "runtime/allinluna_runtime/__init__.py", plugin_root / "skills/allinluna/SKILL.md"]
            if spec["id"] == "research-routes":
                required.extend([
                    artifact / "README.md",
                    artifact / "README.en.md",
                    plugin_root / "runtime/research_routes_runtime/__init__.py",
                    plugin_root / "runtime/research_routes_runtime/schemas/research-pack.schema.json",
                ])
            for path in required:
                if not path.is_file():
                    errors.append(f"{spec['id']} missing {path.relative_to(artifact)}")
            if any(not path.is_file() for path in required):
                continue
            if not (plugin_root / ".codex-plugin/plugin.json").is_file():
                continue
            plugin = read_json(plugin_root / ".codex-plugin/plugin.json")
            if plugin.get("name") != spec["plugin_name"]:
                errors.append(f"{spec['id']} plugin name mismatch")
            if plugin.get("version") != spec.get("version"):
                errors.append(f"{spec['id']} plugin version does not match the RC manifest")
            prompts = plugin.get("interface", {}).get("defaultPrompt")
            if not isinstance(prompts, list) or len(prompts) != 3:
                errors.append(f"{spec['id']} must expose exactly 3 defaultPrompt entries")
            expected_skills = "./skills/allinluna" if spec["id"] == "all-in-luna" else "./skills/"
            if plugin.get("skills") != expected_skills:
                errors.append(f"{spec['id']} skill entrypoint is not canonical: {plugin.get('skills')!r}")
            if spec["id"] == "research-routes" and plugin.get("runtime", {}).get("source") != "./runtime/research_routes_runtime":
                errors.append("research-routes Pack runtime entrypoint is not canonical")
            marketplace = read_json(artifact / ".agents/plugins/marketplace.json")
            entries = marketplace.get("plugins", [])
            if marketplace.get("name") != spec["plugin_name"] or len(entries) != 1:
                errors.append(f"{spec['id']} standalone marketplace identity is invalid")
            expected_source_path = "./plugins/research-routes" if spec["id"] == "research-routes" else "./."
            if entries and entries[0].get("source", {}).get("path") != expected_source_path:
                errors.append(f"{spec['id']} standalone marketplace source path is invalid")
            elif entries and entries[0].get("name") != plugin.get("name"):
                errors.append(f"{spec['id']} standalone marketplace entry does not match plugin root")
            provenance = read_json(artifact / ".source-provenance.json")
            if provenance != expected_provenance:
                errors.append(f"{spec['id']} source provenance does not match current HEAD")
            artifact_manifest = read_json(artifact / "distribution-manifest.json")
            for field, expected in {
                "distribution_id": spec["id"],
                "plugin_name": spec["plugin_name"],
                "version": spec["version"],
                "release_status": release.get("status"),
                "rc_tag": spec["rc_tag"],
                "tag_owner": release.get("tag_owner"),
                "tag_timing": release.get("tag_timing"),
            }.items():
                if artifact_manifest.get(field) != expected:
                    errors.append(f"{spec['id']} artifact {field} is stale or mismatched")
            if artifact_manifest.get("provenance") != expected_provenance:
                errors.append(f"{spec['id']} artifact provenance does not match current HEAD")
            if (artifact / "LICENSE").read_bytes() != (root / "LICENSE").read_bytes():
                errors.append(f"{spec['id']} LICENSE differs from source LICENSE")
            inventory = read_json(artifact / "canonical-files.json")
            entries = inventory.get("files", [])
            sources = [entry.get("source") for entry in entries]
            if len(sources) != len(set(sources)):
                errors.append(f"{spec['id']} canonical source inventory contains duplicates")
            canonical_prefix = "plugins/allinluna/"
            for entry in entries:
                source = entry.get("source")
                expected_hash = entry.get("sha256")
                if not isinstance(source, str) or not source.startswith((canonical_prefix, "tests/", "evals/")):
                    errors.append(f"{spec['id']} canonical source path is invalid: {source!r}")
                    continue
                if source.startswith(canonical_prefix):
                    artifact_path = plugin_root / source.removeprefix(canonical_prefix)
                elif source.startswith(("tests/", "evals/")):
                    artifact_path = plugin_root / source
                else:
                    errors.append(f"{spec['id']} canonical source path is invalid: {source!r}")
                    continue
                if not artifact_path.is_file():
                    errors.append(f"{spec['id']} canonical file is missing: {source}")
                elif sha256(artifact_path) != expected_hash:
                    errors.append(f"{spec['id']} canonical file hash mismatch: {source}")
            if spec["id"] == "research-routes":
                for readme_name in ("README.md", "README.en.md"):
                    readme = (artifact / readme_name).read_text(encoding="utf-8")
                    for source_only_path in SOURCE_ONLY_README_PATHS:
                        if source_only_path in readme:
                            errors.append(f"{spec['id']} {readme_name} contains source-only path {source_only_path}")
            inventories.append(inventory)
            if (plugin_root / "shared").exists() or (plugin_root / "runtime" / "shared").exists():
                errors.append(f"{spec['id']} contains a duplicate shared runtime")
        if len(inventories) == 2 and inventories[0] != inventories[1]:
            errors.append("canonical source inventory differs between distributions")
        if len(built) == 2:
            luna_root = plugin_root_for(built[0], specs[0])
            routes_root = plugin_root_for(built[1], specs[1])
            luna_skills = {p.relative_to(luna_root / "skills").as_posix() for p in (luna_root / "skills").rglob("*") if p.is_file()}
            routes_skills = {p.relative_to(routes_root / "skills").as_posix() for p in (routes_root / "skills").rglob("*") if p.is_file()}
            if not luna_skills.issubset(routes_skills):
                errors.append("Research Routes is missing shared skill files")
            if "research-routes/SKILL.md" not in routes_skills:
                errors.append("Research Routes overlay skill is missing")
            if "research-routes/SKILL.md" in luna_skills:
                errors.append("Research Routes skill leaked into All in Luna overlay")
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
