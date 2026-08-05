# First-use verification protocol

This is an advanced, read-only evidence check for the ordinary newcomer path.
It is not a mandatory multi-layer workflow and it does not create tasks or
write runtime state into the product repository.

## The ordinary path

The user-visible sequence is fixed:

```text
one-sentence request or existing plan
  -> one resource confirmation card
  -> Coordinator
  -> dependency waves
  -> result
```

The card is confirmed once. `quick` uses a Coordinator and necessary Owner(s)
without mechanical Integration by default; `standard` uses multiple Owners and
at most one Integration when a shared result needs it; `full` is an explicit
evidence upgrade reserved for high-risk, large cross-contract, or scientific-
authority work, but the lean runtime does not materialize Acceptance/CounterPilot
lanes. All in Luna does not
default to multi-layer governance, frequent interruptions, or a real canary on
every run.

## What the advanced check proves

When a real first-use receipt is requested, the bounded evidence chain is:

`Sponsor → distinct Coordinator → dependency-wave Owner receipt(s) → repeated tick → host receipt → monitor cursor/receipt → mechanical integration boundary`.

The quick path may have only the necessary Owner(s); standard/full checks may
show multiple Owners. Every receipt carries machine-readable identity fields:

- `thread_id`, `host_id`, `worktree`, `repo`, `branch`, and `commit` identify
  the real execution context;
- every tool/capability records `requested`, `resolved`, and `actual` values;
- every Owner host receipt records matching `resource_receipt.requested`,
  `resolved`, and `actual` model/reasoning values, plus `actual_state=resolved`,
  a host evidence source, and an observation timestamp;
- a repeated tick records `no-op`, `reuse`, or `wait`, so polling cannot create
  a duplicate Owner;
- monitor evidence records a cursor and receipts; the integration boundary is
  explicitly `mechanical-only`, so semantic defects return to the owning lane.

### Persistent receipt contract

The persisted real protocol receipt is an evidence envelope, not the raw
transport response from `create_thread`. It must persist, in the same receipt
chain, `source=codex_app`, `actual_tool=codex_app__create_thread`, all requested /
resolved / actual tool and capability values, every Sponsor/Coordinator/Owner
identity, `monitor.source=codex_app` with both `cursor` and `receipts`, and
`integration_boundary.source=codex_app` with `boundary=mechanical-only`.
Each Owner receipt must also carry the complete resource receipt triple. A
missing field blocks acceptance; a requested/resolved/actual mismatch is a
product failure and can never be normalized into a pass.

An object containing only `threadId`, `hostId`, and an output directory is an
incomplete host transport receipt. The checker does not infer missing protocol
fields from those values or the output directory. It reports
`BLOCKED`/`UNVERIFIED` and lists missing paths; it cannot produce `REAL_PASS`.

The JSON contract is [`first-use-protocol.schema.json`](first-use-protocol.schema.json).
The checker is [`scripts/first_use_protocol.py`](../scripts/first_use_protocol.py).

## Real Codex App versus CI fixture

| Step | Real Codex App / host receipt | CI fixture |
| --- | --- | --- |
| Sponsor and Coordinator | The App creates and returns distinct real thread identities. | Deterministic synthetic identities exercise the bounded ordering. |
| Owner dispatch | The independent Coordinator uses the host's top-level-task tool and receives each receipt. | The checker creates synthetic Owner evidence only for ordering/idempotency. |
| Repeated tick | The Coordinator re-reads host state and proves `no-op`/`reuse`/`wait`; it does not create a task itself. | The checker emits the same bounded actions without host calls. |
| Thread receipt | `source=codex_app`, `actual_tool=codex_app__create_thread`, real thread and host/worktree/repo identity, plus matching requested/resolved/actual model and reasoning evidence. | `source=fixture`, `actual_tool=fixture-simulated`, and explicitly synthetic resource evidence; this can never become `REAL_PASS`. |
| Monitor | A host cursor and externally observed receipts are required. | A deterministic fixture cursor and receipt list are sufficient for CI. |
| Integration boundary | Host evidence shows mechanical reconciliation only; product semantics remain Owner-owned. | The same boundary is checked synthetically. |

Real mode is deliberately read-only: `first_use_protocol.py --mode real` reads
one persisted host receipt and reports evidence. It never calls
`codex_app__create_thread`, creates an unbounded sidebar task, or mutates a
worktree. A missing receipt, pending `clientThreadId` without a returned
`thread_id`, unavailable tool, or missing host identity stops at
`BLOCKED`/`UNVERIFIED`; it is not a pass.

## Run the bounded checker

Fixture success and failure recovery are deterministic and safe in CI:

```powershell
python scripts/first_use_protocol.py --mode fixture --scenario success
python scripts/first_use_protocol.py --mode fixture --scenario failure-recovery
```

The output is `FIXTURE_PASS`, intentionally distinct from `REAL_PASS`. A host
receipt can be checked without changing the repository:

```powershell
python scripts/first_use_protocol.py --mode real --receipt C:\path\from\codex-host\first-use-receipt.json
```

The checker returns zero only for `FIXTURE_PASS` or `REAL_PASS`; it returns
non-zero for `BLOCKED`, `UNVERIFIED`, `CHECKER_ERROR`, or product `FAIL`. Use
`--output` only when a caller explicitly wants a report outside the product
repository.

## Evidence and recovery cases

### Success

The host returns a distinct Coordinator, the Owner receipt(s) required by the
selected mode, and a monitor cursor. The next tick reconciles the same dispatch
IDs with `duplicate_dispatch=no-op`, completed work with `reuse`, or pending
work with `wait`. Only complete external evidence can produce `REAL_PASS`.

### Failure and recovery

An Owner may return a `product_failure`. The Coordinator records the failure,
returns recovery to that same dispatch identity, and the next tick reuses or
waits on the existing thread. The checker accepts recovery evidence only when
the original failure remains visible and no second Owner/thread is invented.
Host/tool unavailability is separate from a product failure; malformed or
schema-incompatible receipts are `CHECKER_ERROR`. The stop report says whether
evidence is sufficient and what is missing.
