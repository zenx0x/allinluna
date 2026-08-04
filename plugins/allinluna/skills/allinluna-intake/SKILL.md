---
name: allinluna-intake
description: Intake ideas, conversation history, pasted text, attachments, local paths, Markdown, text, JSON, YAML, plans, repositories, papers, data, and experiment records without duplicate questioning, then route direct execution, lightweight completion, idea-to-plan, or explicit external-plan-complete parallel-only takeover. Use whenever a user brings material to All in Luna before planning or execution.
---

# All in Luna - Conversation Intake

Normalize the material already supplied by the user before asking anything. Accept chat context,
attachments, local paths, pasted text, `.md`, `.txt`, `.json`, `.yaml`, incomplete or complete plans,
other Plan Skills, repositories, historical material, papers, datasets, and experiment records.

Run `scripts/intake.py` to create a machine-readable intake record. Preserve source paths and content
digests; do not invent missing facts. Ask only for fields that are still missing and exclude questions
already present in `prior_questions`.

Route the record as follows:

- `direct-execution` for an explicit implementation/run request.
- `lightweight-completion` for a bounded change or answerable completion.
- `idea-to-plan` for an idea or incomplete plan; continue through `$allinluna-plan`.
- `external-plan-complete` only when the supplied plan is explicitly complete; use parallel-only mode
  and normalize dependencies, ownership, resources, permissions, stop, and recovery without redesigning.

Do not create a Goal during intake. Do not formalize execution until the one launch confirmation is
created by `$allinluna-launch`.

