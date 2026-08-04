# First-use verification protocol

This protocol is a small, repeatable check of the ordinary newcomer path. It
proves the control-plane boundary without creating tasks from the checker and
without writing runtime state into the product repository.

## What the newcomer path proves

The bounded sequence is:

`Sponsor → distinct Coordinator → multiple top-level Owners → repeated tick →
host thread receipt → monitor cursor/receipt → mechanical integration boundary`.

Every receipt carries machine-readable event sequence and identity fields:

- `thread_id`, `host_id`, `worktree`, `repo`, `branch`, and `commit` identify the
  real execution context;
- each tool/capability records `requested`, `resolved`, and `actual` values;
- a repeated tick records `no-op`, `reuse`, or `wait`, so polling cannot create a
  duplicate Owner;
- monitor evidence records a cursor and receipts; integration is explicitly
  `mechanical-only`, so semantic defects return to the original Owner.

The JSON contract is [`first-use-protocol.schema.json`](first-use-protocol.schema.json).
The checker is [`scripts/first_use_protocol.py`](../scripts/first_use_protocol.py).

## Real Codex App versus CI fixture

| Step | Real Codex App / host receipt | CI fixture |
| --- | --- | --- |
| Sponsor and Coordinator | The App must create and return distinct real thread identities. | Deterministic synthetic identities are allowed. |
| Owner dispatch | The independent Coordinator uses the host's top-level-task tool; the host returns each receipt. | The checker generates two synthetic Owners only to exercise ordering and idempotency. |
| Repeated tick | The Coordinator re-reads host state and proves `no-op`/`reuse`/`wait`; it must not create a task itself. | The checker emits the same bounded actions without host calls. |
| Thread receipt | `source=codex_app`, `actual_tool=codex_app__create_thread`, real `thread_id`, host/worktree/repo identity. | `source=fixture`, `actual_tool=fixture-simulated`; this can never become `REAL_PASS`. |
| Monitor | A host cursor and externally observed receipts are required. | A deterministic fixture cursor and receipt list are sufficient for CI. |
| Integration boundary | The host evidence must show mechanical reconciliation only; product semantics remain Owner-owned. | The same boundary is checked synthetically. |

Real mode is deliberately read-only: `first_use_protocol.py --mode real` reads
one host receipt and reports evidence. It never calls `codex_app__create_thread`,
never creates an unbounded sidebar task, and never mutates a worktree. A missing
receipt, pending `clientThreadId` without a returned `thread_id`, unavailable
tool, or missing host identity stops at `BLOCKED`/`UNVERIFIED`; it is not a pass.

## Run the bounded checker

Fixture success and failure recovery are deterministic and safe to run in CI:

```powershell
python scripts/first_use_protocol.py --mode fixture --scenario success
python scripts/first_use_protocol.py --mode fixture --scenario failure-recovery
```

The output is `FIXTURE_PASS`, intentionally distinct from `REAL_PASS`. A host
receipt can be checked without changing the repository:

```powershell
python scripts/first_use_protocol.py --mode real --receipt C:\path\from\codex-host\first-use-receipt.json
```

The checker returns zero only for `FIXTURE_PASS` or `REAL_PASS`; it returns a
non-zero result for `BLOCKED`, `UNVERIFIED`, `CHECKER_ERROR`, or product `FAIL`.
Use `--output` only when a caller explicitly wants a report file outside the
product repository.

## Evidence and recovery cases

### Success

The host returns a distinct Coordinator, two or more distinct Owner receipts,
and a monitor cursor. The second tick reconciles the same dispatch IDs with
`duplicate_dispatch=no-op`, completed work with `reuse`, or pending work with
`wait`. The report can end at the mechanical integration boundary. Only this
complete external receipt can produce `REAL_PASS`.

### Failure and recovery

An Owner may return a `product_failure`. The Coordinator records the failure,
returns recovery to that same dispatch identity, and the next tick reuses or
waits on the existing thread. The checker accepts recovery evidence only when
the original failure remains visible and no second Owner/thread is invented.
Host/tool unavailability is separate from a product failure; malformed or
schema-incompatible receipts are `CHECKER_ERROR`. The stop report always says
whether evidence is sufficient and what is missing.
