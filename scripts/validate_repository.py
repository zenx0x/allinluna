#!/usr/bin/env python3
"""Run deterministic structural validation for the All in Luna plugin repository."""

from __future__ import annotations

import json
import py_compile
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "allinluna"
SKILLS = PLUGIN / "skills"
EXPECTED_SKILLS = {"allinluna-plan", "allinluna-run"}
STALE_TERMS = {
    "agent-development-orchestrator",
    "development-orchestrator",
    "$plan-development",
    "$orchestrate-development",
    "allluna",
}


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no YAML frontmatter")
    try:
        block = text.split("---\n", 2)[1]
    except IndexError as exc:
        raise ValueError(f"{path} has malformed YAML frontmatter") from exc
    result: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


def markdown_links(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)


def validate() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    required = [
        ROOT / "README.md",
        ROOT / "LICENSE",
        ROOT / "CONTRIBUTING.md",
        ROOT / "SECURITY.md",
        ROOT / ".agents" / "plugins" / "marketplace.json",
        PLUGIN / ".codex-plugin" / "plugin.json",
    ]
    for path in required:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(ROOT)}")

    try:
        marketplace = json.loads(required[4].read_text(encoding="utf-8"))
        plugin = json.loads(required[5].read_text(encoding="utf-8"))
        if marketplace.get("name") != "allinluna":
            errors.append("marketplace name must be allinluna")
        entries = marketplace.get("plugins", [])
        if len(entries) != 1 or entries[0].get("name") != "allinluna":
            errors.append("marketplace must expose exactly the allinluna plugin")
        source = entries[0].get("source", {}).get("path") if entries else None
        if source != "./plugins/allinluna":
            errors.append("marketplace source must be ./plugins/allinluna")
        if plugin.get("name") != "allinluna":
            errors.append("plugin name must be allinluna")
        if plugin.get("skills") != "./skills/":
            errors.append("plugin skills path must be ./skills/")
    except (OSError, json.JSONDecodeError, IndexError) as exc:
        errors.append(f"invalid plugin metadata: {exc}")

    actual_skills = {path.name for path in SKILLS.iterdir() if path.is_dir()} if SKILLS.exists() else set()
    if actual_skills != EXPECTED_SKILLS:
        errors.append(f"expected skills {sorted(EXPECTED_SKILLS)}, found {sorted(actual_skills)}")
    for skill_name in EXPECTED_SKILLS:
        skill_dir = SKILLS / skill_name
        skill_file = skill_dir / "SKILL.md"
        agent_file = skill_dir / "agents" / "openai.yaml"
        if not skill_file.is_file():
            errors.append(f"missing {skill_file.relative_to(ROOT)}")
            continue
        try:
            metadata = frontmatter(skill_file)
            if metadata.get("name") != skill_name:
                errors.append(f"{skill_name} frontmatter name mismatch")
            if len(metadata.get("description", "")) < 80:
                errors.append(f"{skill_name} description is not trigger-specific enough")
            unknown = set(metadata) - {"name", "description"}
            if unknown:
                errors.append(f"{skill_name} has unsupported frontmatter fields: {sorted(unknown)}")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        line_count = len(skill_file.read_text(encoding="utf-8").splitlines())
        if line_count > 500:
            errors.append(f"{skill_name}/SKILL.md exceeds 500 lines")
        if not agent_file.is_file() or f"${skill_name}" not in agent_file.read_text(encoding="utf-8"):
            errors.append(f"{skill_name} agent metadata lacks its explicit skill prompt")
        for link in markdown_links(skill_file):
            if "://" in link or link.startswith("#"):
                continue
            if not (skill_dir / link).resolve().is_file():
                errors.append(f"broken skill link in {skill_name}: {link}")

    for json_path in ROOT.rglob("*.json"):
        if ".git" in json_path.parts:
            continue
        try:
            json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid JSON {json_path.relative_to(ROOT)}: {exc}")

    for script in [*ROOT.glob("scripts/*.py"), *SKILLS.glob("*/scripts/*.py")]:
        try:
            py_compile.compile(str(script), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"Python compile failed for {script.relative_to(ROOT)}: {exc}")

    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.suffix in {".pyc"}:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "[TODO" in text or "TODO:" in text:
            errors.append(f"placeholder remains in {path.relative_to(ROOT)}")
        for term in STALE_TERMS:
            if term in text:
                errors.append(f"stale brand term {term!r} in {path.relative_to(ROOT)}")

    plan_script = SKILLS / "allinluna-plan" / "scripts" / "validate_plan.py"
    example = SKILLS / "allinluna-plan" / "assets" / "development-plan.example.json"
    eval_script = ROOT / "scripts" / "validate_evals.py"
    for command, label in [
        ([sys.executable, str(plan_script), str(example)], "example plan"),
        ([sys.executable, str(eval_script)], "evaluation datasets"),
    ]:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        if result.returncode:
            errors.append(f"{label} validation failed: {result.stdout.strip()} {result.stderr.strip()}")

    profiles_path = SKILLS / "allinluna-run" / "assets" / "resource-profiles.json"
    try:
        profiles = json.loads(profiles_path.read_text(encoding="utf-8"))["profiles"]
        required_profiles = {"premium", "balanced", "economy", "speed", "all-luna", "mad-luna", "custom"}
        if set(profiles) != required_profiles:
            errors.append("resource profile set is incomplete or contains unexpected modes")
        mad = profiles["mad-luna"]
        if mad.get("hard_model_lock", {}).get("family") != "luna":
            errors.append("mad-luna must hard-lock the Luna family")
        if any(role.get("reasoning") != "max" for role in mad.get("roles", {}).values()):
            errors.append("every mad-luna role must request max reasoning")
    except (OSError, json.JSONDecodeError, KeyError, AttributeError) as exc:
        errors.append(f"invalid resource profiles: {exc}")

    if "Apache License" not in (ROOT / "LICENSE").read_text(encoding="utf-8"):
        errors.append("LICENSE is not the Apache License text")
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def main() -> int:
    errors, warnings = validate()
    print(
        json.dumps(
            {"valid": not errors, "errors": errors, "warnings": warnings},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
