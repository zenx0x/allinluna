# T1 correction attempt 2: final Desktop canary

This is the authoritative final-artifact record for the exact PR #2 merge
candidate:

- source commit: `db4b8e78d9b53535056a40209bfe4107f3f2e0a4`
- source tree: `60855b002c7830c21eaed9ab127f7e7d54d3f443`
- merge ref: `2bdc3e80410ce63eb1bad353d0b90b744ad33940`
- clean-wheel SHA256: `8eddfa6fd0a8070781601bf50d2e37907a2521df4788d4eda785fdc861d2c30f`
- installation: `clean-wheel`
- absolute CLI: `C:\Users\ZENX0\AppData\Local\Temp\allinluna-t1-pr2-final-20260810\wheel-venv\Scripts\allinluna.exe`

The fresh run is `run-t1-pr2-final-desktop-canary-20260810`. It used two real
Desktop `codex_app__create_thread` actions in projectless targets. The relay
was:

```text
lane next-actions
→ lane-direct-work/v1
→ bounded local work
→ direct-work-result/v1 with typed export
→ lane ingest-direct-result
→ evidence-backed work-handoff/v1
→ evidence-backed lane-handoff/v1
→ producer export releases consumer
→ root completed
```

The final classification is `PASS`. Producer and consumer are completed, the
consumer imports the verified `ProducerArtifact`, and the root run is
completed. Native recursion is `NOT_APPLICABLE`; no `direct_work_executor`
callback or native recursion was used.

The public CLI `lane handoff` command emits the runtime's neutral,
evidence-null `lane-handoff/v1` envelope. Those child-generated IDs are kept in
the evidence. Coordinator acceptance uses the installed wheel's independent
`EvidenceCollector` plus `CoordinatorEngine.ingest_handoff` path, which
produced the evidence-verified handoffs recorded in the qualification JSON.
This qualification does not modify runtime source code.

The consumer's first retry was correctly blocked with
`artifact_unverified`, `declared_exports_missing`, and
`export_unverified:ConsumerArtifact`. The payload was then materialized using
`ArtifactStore.put` and retried through the same public durable relay. The
blocked record is preserved as a superseded diagnostic; it is not rewritten as
PASS. The earlier `acceptance.json` remains the historical failed diagnostic.

Evidence files:

- `t1-correction-attempt-2-actions.json`: exact Desktop actions and relay order.
- `t1-correction-attempt-2-receipts.json`: startup receipts plus wait/read
  cursors and child turn IDs.
- `t1-correction-attempt-2-qualification.json`: plans, direct results, typed
  exports, work/lane handoffs, recovery diagnostic, and final state.
