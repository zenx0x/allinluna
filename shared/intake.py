#!/usr/bin/env python3
"""Normalize user-provided material into an execution-ready intake record."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml"}
PLAN_MARKERS = ("tasks", "milestones", "completion_standard", "plan_id", "dependencies")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_path(path: Path) -> tuple[str, Any]:
    if not path.is_file():
        return "path", {"path": str(path), "exists": False}
    raw = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            return "json", json.loads(raw)
        except json.JSONDecodeError:
            return "text", raw
    if suffix in {".yaml", ".yml"}:
        # Keep YAML dependency-free: preserve the source and extract obvious top-level keys.
        keys = [line.split(":", 1)[0].strip() for line in raw.splitlines()
                if line and not line.startswith((" ", "-", "#")) and ":" in line]
        return "yaml", {"raw": raw, "top_level_keys": keys}
    return ("markdown" if suffix in {".md", ".markdown"} else "text"), raw


def _looks_like_plan(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    keys = set(value)
    keys.update(value.get("top_level_keys", []))
    return len(keys.intersection(PLAN_MARKERS)) >= 2


def _is_complete_plan(value: Any) -> bool:
    if not _looks_like_plan(value):
        return False
    if "top_level_keys" in value:
        return {"tasks", "completion_standard"}.issubset(set(value["top_level_keys"]))
    if not isinstance(value.get("tasks"), list) or not value["tasks"]:
        return False
    standard = value.get("completion_standard")
    return isinstance(standard, list) and bool(standard)


def _classify(text: str, structured: Any, source_kinds: set[str], explicit_plan_complete: bool | None) -> str:
    if _is_complete_plan(structured) or (explicit_plan_complete is True and _is_complete_plan(structured)):
        return "external-plan-complete"
    normalized = text.strip().lower()
    if len(normalized) < 240 and re.match(r"^(fix|change|add|remove|update|run|show|check|execute)\b", normalized):
        return "lightweight-completion"
    if "execute" in normalized or "implement" in normalized or "run " in normalized:
        return "direct-execution"
    return "idea-to-plan"


def collect(*, text: str = "", paths: list[str] | None = None, attachments: list[str] | None = None,
            explicit_plan_complete: bool | None = None, prior_questions: list[str] | None = None) -> dict[str, Any]:
    paths = paths or []
    attachments = attachments or []
    prior_questions = prior_questions or []
    items: list[dict[str, Any]] = []
    fragments = [text] if text else []
    for raw_path in [*paths, *attachments]:
        kind, value = _read_path(Path(raw_path).expanduser())
        source_path = Path(raw_path).expanduser()
        raw_content = value.get("raw", "") if isinstance(value, dict) else str(value)
        items.append({"kind": kind, "path": str(source_path), "exists": source_path.is_file(),
                      "content": value, "content_digest": hashlib.sha256(raw_content.encode("utf-8")).hexdigest()})
        fragments.append(value.get("raw", "") if isinstance(value, dict) else str(value))
    combined = "\n\n".join(fragments).strip()
    structured = next((item["content"] for item in items if _looks_like_plan(item["content"])), None)
    source_kinds = {item["kind"] for item in items}
    if text:
        source_kinds.add("pasted-text")
    action = _classify(combined, structured, source_kinds, explicit_plan_complete)
    missing: list[str] = []
    if not combined or any(not item["exists"] for item in items):
        missing.append("content")
    if action in {"direct-execution", "external-plan-complete"} and not paths and not attachments:
        missing.append("execution_target")
    candidates = [
        f"Please provide {field}." for field in missing
    ]
    questions = list(dict.fromkeys(question for question in candidates if question not in prior_questions))
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return {
        "schema_version": "1.0",
        "intake_id": f"intake-{digest[:16]}",
        "created_at": _now(),
        "sources": items + ([{"kind": "pasted-text", "content": text,
                               "content_digest": hashlib.sha256(text.encode("utf-8")).hexdigest()}] if text else []),
        "content_digest": digest,
        "action": action,
        "parallel_only": action == "external-plan-complete",
        "normalized": {
            "text": combined,
            "structured": structured,
            "source_kinds": sorted(source_kinds),
            "dependencies_normalization": action == "external-plan-complete",
        },
        "questions": questions,
        "prior_questions": prior_questions,
        "duplicate_question_count": len(candidates) - len(questions),
        "ready_for_launch": not questions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--attachment", action="append", default=[])
    parser.add_argument("--plan-complete", action="store_true")
    parser.add_argument("--prior-question", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    result = collect(text=args.text, paths=args.path, attachments=args.attachment,
                     explicit_plan_complete=True if args.plan_complete else None,
                     prior_questions=args.prior_question)
    payload = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
