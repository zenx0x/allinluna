#!/usr/bin/env python3
"""Validate the vNext repository topology and canonical-source contract."""

from __future__ import annotations

import json
import py_compile
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "allinluna"
SKILLS = PLUGIN / "skills"
LEGACY_PATHS = (
    ROOT / "shared",
    PLUGIN / "runtime" / "shared",
    SKILLS / "allinluna-intake",
    SKILLS / "allinluna-launch",
    SKILLS / "allinluna-plan",
    SKILLS / "allinluna-run",
)


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    block = text.split("---\n", 2)[1]
    return {key.strip(): value.strip() for line in block.splitlines() if ":" in line for key, value in [line.split(":", 1)]}


def markdown_links(path: Path) -> list[str]:
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8"))


def validate() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    required = (
        ROOT / "README.md", ROOT / "README.en.md", ROOT / "LICENSE",
        ROOT / ".agents/plugins/marketplace.json", PLUGIN / ".codex-plugin/plugin.json",
        PLUGIN / "skills/allinluna/SKILL.md", PLUGIN / "runtime/allinluna_runtime/__init__.py",
        ROOT / "distributions/distribution-manifest.json",
    )
    for path in required:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(ROOT)}")
    for path in LEGACY_PATHS:
        if path.exists():
            errors.append(f"forbidden legacy path remains: {path.relative_to(ROOT)}")

    try:
        marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        plugin = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        if {entry.get("name") for entry in marketplace.get("plugins", [])} != {"allinluna", "research-routes"}:
            errors.append("marketplace must expose allinluna and research-routes")
        if plugin.get("name") != "allinluna" or plugin.get("skills") != "./skills/allinluna":
            errors.append("allinluna manifest must point at the single public Skill")
        runtime = plugin.get("runtime", {})
        if runtime.get("source") != "./runtime/allinluna_runtime":
            errors.append("allinluna manifest must point at the canonical runtime")
        prompts = plugin.get("interface", {}).get("defaultPrompt", [])
        if len(prompts) < 2:
            errors.append("allinluna manifest needs its public prompts")
        research = ROOT / "plugins/research-routes/.codex-plugin/plugin.json"
        if not research.is_file() or json.loads(research.read_text(encoding="utf-8")).get("name") != "research-routes":
            errors.append("research-routes plugin metadata is missing or invalid")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid plugin metadata: {exc}")

    skill = PLUGIN / "skills/allinluna/SKILL.md"
    if skill.is_file():
        try:
            metadata = frontmatter(skill)
            if metadata.get("name") != "allinluna":
                errors.append("public Skill frontmatter name mismatch")
            if len(metadata.get("description", "")) < 60:
                errors.append("public Skill description is too short")
            for link in markdown_links(skill):
                if "://" not in link and not link.startswith("#") and not (skill.parent / link).resolve().is_file():
                    errors.append(f"broken public Skill link: {link}")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))

    for json_path in ROOT.rglob("*.json"):
        if ".git" in json_path.parts:
            continue
        try:
            json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid JSON {json_path.relative_to(ROOT)}: {exc}")
    for script in [*ROOT.glob("scripts/*.py"), *ROOT.glob("tests/**/*.py")]:
        try:
            py_compile.compile(str(script), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"Python compile failed for {script.relative_to(ROOT)}: {exc}")

    result = subprocess.run([sys.executable, str(ROOT / "scripts/validate_evals.py")], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        errors.append(f"evaluation validation failed: {result.stdout.strip()} {result.stderr.strip()}")
    if "Apache License" not in (ROOT / "LICENSE").read_text(encoding="utf-8"):
        errors.append("LICENSE is not the Apache License text")
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def main() -> int:
    errors, warnings = validate()
    print(json.dumps({"valid": not errors, "errors": errors, "warnings": warnings}, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
