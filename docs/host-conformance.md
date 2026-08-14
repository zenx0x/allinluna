# Host conformance diagnostics

All in Luna now uses a compact host-conformance checker for host-neutral execution evidence.

The checker validates a host trace with three parts:

- host `identity` (thread/workspace/repo context)
- mandatory actions: `create`, `read`, `wait`, `cancel`
- `idempotency` values for each action (`no-op`, `reuse`, `wait`)

Resource evidence is reported as three separate values: the route `requested`
by policy, the route `resolved` before dispatch, and the `actual` host
observation. A resolved route is not proof that the requested model executed;
missing actual telemetry remains explicitly unknown.

Run the checker directly:

```powershell
python scripts/host_conformance.py --mode fixture
python scripts/host_conformance.py --mode real --trace C:\path\to\host-trace.json
```

`PASS` indicates all required action and identity checks are complete.
