#!/usr/bin/env python3
"""Build co-installable end-user and source/debug artifacts deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDED_FILES = {".DS_Store", "Thumbs.db"}
RC_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+-rc\.[1-9]\d*$")


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


def is_rc_version(value: object) -> bool:
    return isinstance(value, str) and RC_VERSION_PATTERN.fullmatch(value) is not None


def rc_tag(plugin_name: str, version: str) -> str:
    return f"{plugin_name}-v{version}"


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


def _repository_path(root: Path, relative: str, *, label: str) -> Path:
    path = root / relative
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"{label} escapes repository root: {relative}")
    return path


def load_version_authority(root: Path, manifest: dict) -> tuple[Path, dict]:
    relative = manifest.get("version_authority")
    if not isinstance(relative, str) or not relative:
        raise ValueError("distribution manifest must name release/versions.json")
    path = _repository_path(root, relative, label="version authority")
    if path.relative_to(root).as_posix() != "release/versions.json" or not path.is_file():
        raise ValueError("release/versions.json is the required version authority")
    authority = read_json(path)
    if authority.get("release_status") != "release-candidate":
        raise ValueError("version authority must describe a release candidate")
    if authority.get("stable_release") is not False:
        raise ValueError("version authority must forbid stable publication")
    policy = authority.get("tag_policy", {})
    if policy.get("format") != "<plugin_name>-v<version>":
        raise ValueError("version authority must use hyphenated RC tags")
    if policy.get("create_or_publish") is not False:
        raise ValueError("version authority must not authorize creating or publishing tags")
    return path, authority


def resolved_specs(root: Path, manifest: dict) -> list[dict]:
    _, authority = load_version_authority(root, manifest)
    products = authority.get("products", {})
    resolved: list[dict] = []
    for raw_spec in manifest.get("distributions", []):
        spec = dict(raw_spec)
        key = spec.get("version_key")
        product = products.get(key) if isinstance(products, dict) else None
        if not isinstance(product, dict):
            raise ValueError(f"missing version authority product {key!r}")
        for field in ("distribution_id", "plugin_name"):
            expected = spec["id"] if field == "distribution_id" else spec["plugin_name"]
            if product.get(field) != expected:
                raise ValueError(f"version authority {key}.{field} does not match distribution")
        version = product.get("version")
        tag = product.get("tag")
        if not is_rc_version(version):
            raise ValueError(f"{spec['id']} must declare a semantic RC version")
        if tag != rc_tag(spec["plugin_name"], version):
            raise ValueError(f"{spec['id']} must use the exact hyphenated RC tag")
        spec.update(version=version, rc_tag=tag)
        resolved.append(spec)
    return resolved


def artifact_profiles(manifest: dict) -> list[dict]:
    profiles = manifest.get("artifact_profiles", [])
    if {profile.get("id") for profile in profiles} != {"end-user", "source-debug"}:
        raise ValueError("artifact profiles must define end-user and source-debug")
    return [dict(profile) for profile in profiles]


def artifact_name(spec: dict, profile: dict) -> str:
    return f"{spec['id']}{profile.get('suffix', '')}"


def expand_sources(root: Path, entries: Iterable[str]) -> list[tuple[str, Path]]:
    expanded: list[tuple[str, Path]] = []
    root_resolved = root.resolve()
    for entry in entries:
        source = _repository_path(root, entry, label="release source")
        if not source.exists():
            raise FileNotFoundError(f"release source does not exist: {entry}")
        if source.is_symlink():
            raise ValueError(f"release source may not be a symlink: {entry}")
        if source.is_file():
            expanded.append((entry, source))
        else:
            for child in sorted(p for p in source.rglob("*") if p.is_file() and is_release_file(p)):
                if child.is_symlink():
                    raise ValueError(
                        f"release source contains a symlink: {child.relative_to(root)}"
                    )
                if root_resolved not in child.resolve().parents:
                    raise ValueError(
                        f"release source escapes repository root: {child.relative_to(root)}"
                    )
                expanded.append((child.relative_to(root).as_posix(), child))
    return expanded


def canonical_inventory(root: Path, entries: Iterable[str]) -> list[dict[str, str]]:
    return [
        {"source": relative, "sha256": sha256(path)}
        for relative, path in expand_sources(root, entries)
    ]


def copy_tree(source: Path, target: Path) -> None:
    if source.is_symlink():
        raise ValueError(f"release source may not be a symlink: {source}")
    if source.is_dir():
        for child in source.rglob("*"):
            if child.is_symlink():
                raise ValueError(f"release source contains a symlink: {child}")
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


def plugin_root_for(artifact: Path, spec: dict) -> Path:
    if spec["id"] == "research-routes":
        return artifact / "plugins" / "research-routes"
    return artifact


def artifact_path_for_source(plugin_root: Path, spec: dict, source: str) -> Path:
    source_plugin = f"{spec['source_plugin'].rstrip('/')}/"
    if source.startswith(source_plugin):
        return plugin_root / source.removeprefix(source_plugin)
    if source.startswith(("tests/", "evals/")):
        return plugin_root / source
    raise ValueError(f"{spec['id']} canonical source is outside its owned roots: {source}")


def _copy_product_sources(root: Path, plugin_root: Path, spec: dict) -> None:
    source_plugin = root / spec["source_plugin"]
    copy_tree(source_plugin / "skills", plugin_root / "skills")
    copy_tree(source_plugin / "runtime", plugin_root / "runtime")


def _copy_user_docs(root: Path, artifact: Path, spec: dict) -> None:
    for relative in spec.get("user_docs", []):
        copy_tree(root / relative, artifact / relative)
    if spec["id"] == "all-in-luna":
        copy_tree(root / "README.md", artifact / "README.md")
        copy_tree(root / "README.en.md", artifact / "README.en.md")


def build_distribution(
    root: Path,
    output: Path,
    manifest: dict,
    spec: dict,
    profile: dict,
    provenance: dict,
) -> Path:
    version_path, authority = load_version_authority(root, manifest)
    policy = authority["tag_policy"]
    artifact = output / artifact_name(spec, profile)
    if artifact.exists():
        shutil.rmtree(artifact)
    artifact.mkdir(parents=True)
    plugin_root = plugin_root_for(artifact, spec)
    _copy_product_sources(root, plugin_root, spec)
    _copy_user_docs(root, artifact, spec)

    include_debug = profile.get("include_debug_sources") is True
    if include_debug:
        for relative in manifest.get("debug_source_paths", []):
            copy_tree(root / relative, plugin_root / relative)
    elif (plugin_root / "tests").exists() or (plugin_root / "evals").exists():
        raise ValueError("end-user artifacts may not include tests or evals")

    copy_tree(root / "LICENSE", artifact / "LICENSE")
    copy_tree(version_path, artifact / "release" / "versions.json")

    overlay = root / spec["overlay"]
    if not overlay.is_dir():
        raise FileNotFoundError(f"overlay does not exist: {spec['overlay']}")
    allowed_overlay_roots = {
        "brand.json", "cases.json", "default-entry.md", "README.md", "README.en.md",
        "social.md", "topics.json", "skill-metadata",
    }
    for source in overlay.rglob("*"):
        if source.is_symlink():
            raise ValueError(f"overlay contains a symlink: {source}")
        if source.is_file() and source.relative_to(overlay).parts[0] not in allowed_overlay_roots:
            raise ValueError(
                f"overlay file is outside the manifest allowlist: {source.relative_to(overlay)}"
            )
    overlay_target = artifact / "overlay"
    for source in sorted(p for p in overlay.rglob("*") if p.is_file() and is_release_file(p)):
        relative = source.relative_to(overlay)
        if relative.name in {"README.md", "README.en.md"}:
            copy_tree(source, artifact / relative.name)
        else:
            copy_tree(source, overlay_target / relative)

    source_plugin_json = read_json(root / spec["source_plugin"] / ".codex-plugin" / "plugin.json")
    if source_plugin_json.get("version") != spec["version"]:
        raise ValueError(
            f"{spec['id']} source plugin version {source_plugin_json.get('version')!r} "
            f"does not match release/versions.json {spec['version']!r}"
        )
    plugin = {
        **source_plugin_json,
        "name": spec["plugin_name"],
        "description": f"{spec['display_name']}: {read_json(overlay / 'brand.json')['purpose']}",
        "interface": {
            **source_plugin_json.get("interface", {}),
            "displayName": spec["display_name"],
            "shortDescription": read_json(overlay / "brand.json")["tagline"],
        },
    }
    prompts = plugin.get("interface", {}).get("defaultPrompt")
    if not isinstance(prompts, list) or len(prompts) != 3:
        raise ValueError(f"{spec['id']} must expose exactly three default prompts")
    plugin_dir = plugin_root / ".codex-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(plugin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    marketplace_dir = artifact / ".agents" / "plugins"
    marketplace_dir.mkdir(parents=True, exist_ok=True)
    source_path = "./plugins/research-routes" if spec["id"] == "research-routes" else "./."
    marketplace = {
        "name": spec["plugin_name"],
        "interface": {"displayName": spec["display_name"]},
        "plugins": [
            {
                "name": plugin["name"],
                "source": {"source": "local", "path": source_path},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": plugin.get("interface", {}).get("category", "Productivity"),
            }
        ],
    }
    (marketplace_dir / "marketplace.json").write_text(
        json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    canonical_paths = list(spec.get("canonical_paths", []))
    if include_debug:
        canonical_paths.extend(manifest.get("debug_source_paths", []))
    inventory = canonical_inventory(root, canonical_paths)
    (artifact / "canonical-files.json").write_text(
        json.dumps({"schema_version": "3.0", "files": inventory}, indent=2) + "\n",
        encoding="utf-8",
    )
    (artifact / ".source-provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    artifact_manifest = {
        "schema_version": "2.0",
        "distribution_id": spec["id"],
        "artifact_profile": profile["id"],
        "public_artifact": profile.get("public") is True,
        "includes_debug_sources": include_debug,
        "plugin_name": spec["plugin_name"],
        "display_name": spec["display_name"],
        "version": spec["version"],
        "release_status": authority["release_status"],
        "rc_tag": spec["rc_tag"],
        "tag_owner": policy["owner"],
        "tag_timing": policy["timing"],
        "tag_creation_authorized": policy["create_or_publish"],
        "version_authority": {
            "path": "release/versions.json",
            "product": spec["version_key"],
            "sha256": sha256(version_path),
        },
        "canonical_runtime": spec["canonical_paths"][0].removeprefix(f"{spec['source_plugin']}/"),
        "canonical_skills": spec["canonical_paths"][1].removeprefix(f"{spec['source_plugin']}/"),
        "overlay": spec["overlay"],
        "dependencies": plugin.get("dependencies", []),
        "provenance": provenance,
    }
    (artifact / "distribution-manifest.json").write_text(
        json.dumps(artifact_manifest, indent=2) + "\n", encoding="utf-8"
    )
    return artifact


def build(
    root: Path = ROOT,
    output: Path | None = None,
    *,
    profiles: Iterable[str] | None = None,
) -> list[Path]:
    manifest = read_json(root / "distributions" / "distribution-manifest.json")
    output = output or (root / "dist")
    output.mkdir(parents=True, exist_ok=True)
    provenance = source_provenance(root)
    specs = resolved_specs(root, manifest)
    available_profiles = artifact_profiles(manifest)
    selected = (
        set(profiles) if profiles is not None else {item["id"] for item in available_profiles}
    )
    unknown = selected - {item["id"] for item in available_profiles}
    if unknown:
        raise ValueError(f"unknown artifact profiles: {sorted(unknown)}")
    return [
        build_distribution(root, output, manifest, spec, profile, provenance)
        for profile in available_profiles
        if profile["id"] in selected
        for spec in specs
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--profile",
        action="append",
        choices=("end-user", "source-debug"),
        help="Build only this artifact profile; may be repeated (default: both).",
    )
    args = parser.parse_args()
    artifacts = build(ROOT, args.output.resolve(), profiles=args.profile)
    print(json.dumps({"built": [str(path) for path in artifacts]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
