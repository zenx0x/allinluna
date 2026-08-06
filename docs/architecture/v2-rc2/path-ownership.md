---
artifact: path_ownership
version: 1
protocol: contract-freeze/v1
task: run-allinluna-v2-rc2-product-surface:task:T0
status: frozen
---

# RC2 path ownership contract

Every write in the RC2 run belongs to exactly one Task Lane. The paths below
are the persisted ownership declarations loaded from the Runtime DB. A lane
may read shared product context when its contract permits it, but it may write
only its own paths.

## Lane ownership matrix

| Lane | Outcome | Exclusive write set |
| --- | --- | --- |
| T0 | Contract Freeze | `docs/architecture/v2-rc2/**` |
| T1 | User Surface and Documentation | `README.md`, `README.en.md`, `docs/user/**`, `docs/architecture/**`, `docs/troubleshooting/**`, `docs/examples/**` |
| T2 | Vendor-Neutral Resource Policy | `plugins/allinluna/runtime/allinluna_runtime/resource.py`, `resource_policy.py`, `core/model.py`, `store_resources.py`, `packs/public_skill.py`, `examples/resource-policies/**`, resource tests |
| T3 | Verification Planning and Command Trust | `verification.py`, `evidence.py`, `handoff.py`, `packs/goal_compiler.py`, `packs/delivery.py`, `verification_planner.py`, `check_trust.py`, verification/integration tests |
| T4 | Host, CLI and Real Desktop Canary | `pyproject.toml`, `cli.py`, `adapters/host/**`, `coordinator_driver.py`, `lane_driver.py`, `docs/release-canaries/**`, CLI/host tests |
| T5 | Distribution and Release Integrity | `distributions/**`, distribution scripts, `plugins/research-routes/**`, `CHANGELOG.md`, `docs/releases/**`, distribution tests |
| T6 | Product Evals and User-Journey Tests | `evals/**`, `tests/product/**`, `tests/e2e/**`, `scripts/validate_product_experience.py` |
| T7 | Luna Max Integration and RC2 Qualification | `plugins/allinluna/.codex-plugin/plugin.json`, `plugins/allinluna/skills/allinluna/**`, `release/versions.json`, integration-only conflict paths |
| T8 | RC2 Artifact Canary and Release Recommendation | `dist/**`, `docs/release-canaries/v2.0.0-rc.2/**`, release artifacts |

The abbreviated entries in the table are the exact persisted path families
for the named lane; they are not permission to widen a lane's write set.

## Overlap resolution

T0's `docs/architecture/v2-rc2/**` is the frozen architecture namespace. It
is more specific than T1's umbrella `docs/architecture/**`; T1 must treat the
T0 subtree as excluded from its write set. Any change to the frozen subtree
after this handoff requires an explicit coordinator contract revision and a
new artifact digest.

The same rule applies to any future integration-only conflict path: a specific
owner wins over an umbrella path, and an unresolved overlap is a blocker, not
an invitation to edit both sides.

## Protected and out-of-scope paths

The run policy marks these paths protected: `.git`, `.allinluna`,
`release-control`, and `uv.lock`. T0 does not edit them. Runtime DB records,
dispatch outbox rows, leases, receipts, and artifact-store rows are runtime
authority and are not source-tree ownership for this lane.

## Handoff path rules

- `changed_paths` must be relative, normalized, and contained by the owning
  WorkUnit's path set.
- T0's WorkUnit is `run-allinluna-v2-rc2-product-surface:task:T0:work:T0-root`.
- T0's only source-tree changed paths are the four files in this directory.
- Artifact payloads are content-addressed and linked to the T0 task; their
  artifact refs, not untracked absolute paths, are the durable handoff.
- A lane that discovers work outside its set emits a promotion request or a
  blocker; it does not write the path directly.
