# {{task_id}} — {{title}}

## Objective

{{complete_objective}}

## Repository contract

- Repository/worktree: `{{absolute_worktree}}`
- Branch: `{{branch}}`
- Base commit: `{{base_commit}}`
- Owned paths: {{owned_paths}}
- Forbidden/protected paths: {{protected_paths}}
- Applicable instructions: {{instruction_files}}

## Resource contract

- Requested model: `{{requested_model}}`
- Requested reasoning: `{{requested_reasoning}}`
- Fallback/unavailable policy: `{{fallback_policy}}`
- Actual model and reasoning must be reported from host evidence or as `unavailable`.

## Required implementation

{{deliverables}}

This is the complete lane scope. The first vertical slice is a progress checkpoint, not the completion standard.

## Verification

{{verification}}

Run focused checks during implementation. Do not substitute a smoke test for the required behavior.

## External actions

{{external_action_policy}}

## Final report

Return:

- completion state;
- commit and parent;
- changed files;
- checks actually run and results;
- requested and actual runtime settings;
- protected/dirty-path status;
- unknowns and blockers.
