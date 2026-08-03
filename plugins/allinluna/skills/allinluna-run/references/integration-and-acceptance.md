# Integration and acceptance

## Phase integration

One integration owner should:

1. run `verify_task_evidence.py` for every expected owner commit and changed-path boundary;
2. combine shared contracts deliberately;
3. regenerate checked-in clients or artifacts when required;
4. run focused cross-lane product-chain tests;
5. verify no protected or unrelated source changed;
6. publish an integration commit and evidence bundle.

Integration may fix mechanical conflicts, schema/client drift, imports, adapters, and shared test wiring within its assigned scope. It must return scientific, authority, or owner-specific behavioral defects to the original lane.

## Independent acceptance

Acceptance evaluates the actual completion standard and user journeys against the integrated baseline. It should inspect:

- happy paths and failure/recovery paths;
- permissions, isolation, provenance, and fail-closed behavior;
- real API/client/UI or equivalent end-to-end chains;
- representative sparse, rich, empty, stale, conflict, and unknown states;
- required accessibility, performance, and platform dimensions;
- absence of unauthorized external mutation.

Acceptance is read-only by default. A failure names the owner and exact reproduction. After repair, re-run the failed checks plus the smallest required regression set.

Record a failure with `manage_defect.py --action create`. This reopens the original owner
instead of allowing integration or acceptance to silently repair owner-owned science or behavior.
After the owner supplies a repair commit, record it with `manage_defect.py --action resolve`,
re-integrate once, and re-run the failed acceptance evidence.

## Avoid governance inflation

Do not add promotion, registry revision, meta-review, and duplicate acceptance layers unless a release contract explicitly requires them. The normal pattern is:

```text
owners → one integration → one independent acceptance → accepted baseline
```

## Acceptance result

Use `PASS`, `FAIL`, or `BLOCKED`:

- `PASS`: completion evidence exists and no required work remains.
- `FAIL`: implementation does not meet a requirement; return it to an owner.
- `BLOCKED`: an external condition prevents verification and cannot be substituted.

Do not convert unverified behavior into a pass.
