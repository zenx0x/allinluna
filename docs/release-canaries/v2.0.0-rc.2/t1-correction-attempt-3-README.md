# T1 correction attempt 3: final artifact-based Desktop canary

This is the authoritative evidence record for the single accepted `run-t1a3`
producer-to-consumer canary on the pinned PR #2 merge candidate.

- merge ref and detached HEAD: `605a395cb320f4eb73c98499909ea4f61a3256ab`
- merge tree: `508033c434827c5c22073ff91bae8ba653d69024`
- PR head and merge second parent: `6836c9c85591e458a46d98fdecf27bd9e888a997`
- wheel SHA256: `41f336bb86daccb2bd64347354c76e0ca36ba85b23566ae9b760e61cf040f4d0`
- installed CLI: `C:\Users\ZENX0\AppData\Local\Temp\allinluna-t1-pr2-attempt-3-sol-final-20260810\wheel-venv\Scripts\allinluna.exe`
- runtime DB: `C:\Users\ZENX0\AppData\Local\Temp\allinluna-t1-pr2-attempt-3-sol-final-20260810\desktop-canary.db`

The compiled bootstrap was checked before Desktop creation and contained one
non-empty local WorkUnit for each task. The installed CLI then drove this exact
public relay for producer and consumer:

```text
lane next-actions
-> signed lane-direct-work/v1
-> bounded local work plus ArtifactStore.put
-> direct-work-result/v1
-> lane ingest-direct-result
-> independently verified work-handoff/v1
-> installed-wheel EvidenceCollector lane-handoff/v1
-> CoordinatorEngine.ingest_handoff
```

Producer thread `019feb5d-cd1f-72e2-b7ab-0140e1ec10f8` exported verified
`ProducerArtifact` `artifact://sha256:e1da4542ed9cc341ce768e464588c26035bd9168d5a9a161eba389287ed07e42`.
That installed task export released the consumer dependency. Consumer thread
`019feb6a-5665-74f2-aedd-5c0006cb5e1b` independently verified the producer
artifact, created and exported verified `ConsumerArtifact`
`artifact://sha256:a7b47812ac3902dfb885f9bd87e9d7308d88a4ef91ded08bbbaa5a8900c6daed`.
Both Tasks, both root WorkUnits, and the root run completed.

Classification: `PASS`. `native_recursive=NOT_APPLICABLE`,
`callback_used=false`, and `subagent_created=false`. There was exactly one
producer Desktop task, one consumer Desktop task, and one lane-direct WorkUnit
attempt for each. No runtime source was patched.

The installed CLI's child `lane handoff` synthesis is a completed but neutral
envelope with `evidence=null`; it is retained in qualification evidence. The
coordinator accepted only the separate installed-wheel EvidenceCollector
handoffs whose evidence is verified. Requested and resolved routing was
`gpt-5.6-sol` / `xhigh`; host actual-model telemetry remained unresolved under
`observe_if_exposed`.

Evidence files:

- `t1-correction-attempt-3-actions.json`: source, bootstrap, exact Desktop
  action identities, and public relay order.
- `t1-correction-attempt-3-receipts.json`: parent/child task identities, raw
  host receipt bindings, wait cursors, and read results.
- `t1-correction-attempt-3-qualification.json`: signed plan/result bindings,
  artifact/check evidence, WorkHandoffs, LaneHandoffs, dependency release,
  signals, final state, diagnostics, and limitations.
