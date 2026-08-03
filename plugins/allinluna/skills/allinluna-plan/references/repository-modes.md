# Repository modes

## Existing repository

Capture current evidence before designing work:

- absolute repository root and Git common directory;
- branch, HEAD, worktree list, and concise status;
- applicable `AGENTS.md` or equivalent instructions;
- build manifests, lockfiles, language/toolchain versions, and generated-code boundaries;
- relevant source, tests, contracts, migrations, and user-facing surfaces;
- protected sources, generated data, large artifacts, and unrelated dirty changes.

The inspection helper is bounded and read-only. Its suggested commands are heuristics; confirm them from repository scripts or documentation before making them acceptance criteria.

Do not rescan large archives simply because they exist. Prefer manifests, hashes, previous verified inventories, indexes, and targeted source reads.

## Greenfield

When the target path does not exist or contains no project:

1. record `repository.mode = greenfield`;
2. preserve the user's product goal, users, constraints, and target environment;
3. create an explicit bootstrap task for repository, license, toolchain, basic architecture, and initial checks;
4. distinguish chosen architecture from user-provided fact;
5. include a runnable end-to-end slice early, then complete the remaining scope;
6. avoid introducing databases, cloud services, frameworks, or deployment targets without need.

## Mixed or multi-repository work

Represent each repository as its own target with base revision and authority boundary. A task must name which target it owns. Cross-repository integration should be a dependent task, not concurrent shared ownership.

## Non-Git projects

Git worktrees and commits are optional only when Git is genuinely absent or excluded. Replace commit evidence with deterministic file inventories, checksums where appropriate, and an explicit backup/recovery plan. Never initialize Git solely to satisfy this skill unless the user wants it.
