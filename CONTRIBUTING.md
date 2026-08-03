# Contributing

Contributions should improve a real user journey, deterministic state behavior, platform compatibility, or evaluation coverage.

1. Create a focused branch.
2. Keep each `SKILL.md` concise and move detailed contracts into one-level `references/` files.
3. Add or update tests for every state, resource-policy, or plan-validation change.
4. Run `python -m unittest discover -s tests -v` and `python scripts/validate_repository.py`.
5. Describe behavioral impact, validation, and compatibility limits in the pull request.

Do not add hidden network calls, telemetry, implicit external writes, hard-coded private model names, or instructions that silently weaken user scope.
