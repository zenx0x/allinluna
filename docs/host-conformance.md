# Host conformance diagnostics

All in Luna now uses a compact host-conformance checker for host-neutral execution evidence.

The checker validates a host trace with three parts:

- host `identity` (thread/workspace/repo context)
- mandatory actions: `create`, `read`, `wait`, `cancel`
- `idempotency` values for each action (`no-op`, `reuse`, `wait`)

Run the checker directly:

```powershell
python scripts/host_conformance.py --mode fixture
python scripts/host_conformance.py --mode real --trace C:\path\to\host-trace.json
```

`PASS` indicates all required action and identity checks are complete.
