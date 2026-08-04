#!/usr/bin/env python3
"""Validate dual-distribution parity, overlays, and commit provenance."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from build_distributions import ROOT, build, expand_sources, read_json, sha256, source_provenance


SOURCE_ONLY_README_PATHS = (
    "scripts/build_distributions.py",
    "scripts/validate_distributions.py",
    "scripts/validate_installations.py",
    "plugins/research-routes/",
    "scripts/validate_route_packet.py",
)


def validate(root: Path = ROOT, dist: Path | None = None) -> list[str]:
    manifest = read_json(root / "distributions" / "distribution-manifest.json")
    errors: list[str] = []
    specs = manifest.get("distributions", [])
    if {spec.get("id") for spec in specs} != {"all-in-luna", "research-routes"}:
        errors.append("manifest must define exactly all-in-luna and research-routes")
    if not manifest.get("overlay_allowlist"):
        errors.append("overlay allowlist is missing")
    try:
        expected_provenance = source_provenance(root)
    except Exception as exc:  # pragma: no cover - useful CLI diagnostic
        errors.append(f"cannot resolve source provenance: {exc}")
        expected_provenance = {}
    with tempfile.TemporaryDirectory(prefix="allinluna-distributions-") as temp:
        built = build(root, Path(temp) if dist is None else Path(temp) / "built")
        if dist is not None:
            built = build(root, Path(temp) / "built")
        inventories: list[dict] = []
        for artifact, spec in zip(built, specs, strict=True):
            required = [artifact / ".codex-plugin/plugin.json", artifact / ".agents/plugins/marketplace.json", artifact / "distribution-manifest.json", artifact / ".source-provenance.json", artifact / "shared-files.json", artifact / "LICENSE"]
            if spec["id"] == "research-routes":
                required.extend([artifact / "README.md", artifact / "README.en.md"])
            for path in required:
                if not path.is_file():
                    errors.append(f"{spec['id']} missing {path.relative_to(artifact)}")
            if not (artifact / ".codex-plugin/plugin.json").is_file():
                continue
            plugin = read_json(artifact / ".codex-plugin/plugin.json")
            if plugin.get("name") != spec["plugin_name"]:
                errors.append(f"{spec['id']} plugin name mismatch")
            prompts = plugin.get("interface", {}).get("defaultPrompt")
            if not isinstance(prompts, list) or len(prompts) != 3:
                errors.append(f"{spec['id']} must expose exactly 3 defaultPrompt entries")
            marketplace = read_json(artifact / ".agents/plugins/marketplace.json")
            entries = marketplace.get("plugins", [])
            if marketplace.get("name") != spec["plugin_name"] or len(entries) != 1:
                errors.append(f"{spec['id']} standalone marketplace identity is invalid")
            elif entries[0].get("name") != plugin.get("name") or entries[0].get("source", {}).get("path") != "./.":
                errors.append(f"{spec['id']} standalone marketplace entry does not match plugin root")
            provenance = read_json(artifact / ".source-provenance.json")
            if provenance != expected_provenance:
                errors.append(f"{spec['id']} source provenance does not match current HEAD")
            if (artifact / "LICENSE").read_bytes() != (root / "LICENSE").read_bytes():
                errors.append(f"{spec['id']} LICENSE differs from source LICENSE")
            if spec["id"] == "research-routes":
                for readme_name in ("README.md", "README.en.md"):
                    readme = (artifact / readme_name).read_text(encoding="utf-8")
                    for source_only_path in SOURCE_ONLY_README_PATHS:
                        if source_only_path in readme:
                            errors.append(f"{spec['id']} {readme_name} contains source-only path {source_only_path}")
            inventories.append(read_json(artifact / "shared-files.json"))
        if len(inventories) == 2 and inventories[0] != inventories[1]:
            errors.append("shared file inventory differs between distributions")
        if len(built) == 2:
            luna_skills = {p.relative_to(built[0] / "skills").as_posix() for p in (built[0] / "skills").rglob("*") if p.is_file()}
            routes_skills = {p.relative_to(built[1] / "skills").as_posix() for p in (built[1] / "skills").rglob("*") if p.is_file()}
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
