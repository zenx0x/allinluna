# Git and ownership

## Baseline

Before dispatch, record:

- repository and Git common directory;
- current branch and HEAD;
- all worktrees and their branches/commits;
- dirty/untracked paths;
- protected source trees and generated/large-artifact boundaries.

Existing changes belong to the user unless proven otherwise. Do not reset, clean, stash, overwrite, or reformat them merely to obtain a clean baseline.

## Independent writers

Use a separate clean worktree and `codex/*` branch for independent owner tasks when Git supports it. Verify the resolved path remains inside the intended worktree parent before recursive move/delete operations.

Each owner gets an exclusive path set. Overlap is allowed only when tasks are dependency-ordered or one is the designated integration owner.

## Commits

A commit is evidence, not acceptance. Record:

- full commit ID and sole parent(s);
- tree ID when exact integration matters;
- changed paths and ownership check;
- focused tests actually run;
- whether generated files are reproducible;
- remaining dirty state.

Avoid multiple follow-up commits when a task contract requires one auditable commit; amend before handoff when safe and requested.

## Integration

Verify owner commits before merging or cherry-picking. Resolve shared files field-by-field; do not choose an entire file from one lane when both contain valid changes. Never let integration rewrite protected scientific/product authority solely to resolve a conflict.

## Prohibited defaults

- `git reset --hard`, destructive checkout, or clean;
- force-push or history rewrite;
- deleting a worktree with uncollected changes;
- committing secrets or user-local run state;
- treating untracked files as disposable.
