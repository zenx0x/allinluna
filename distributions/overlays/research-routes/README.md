# Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

Research Routes 保持路线中立：Claims、Evidence、unknowns、矛盾、failure regimes、成熟方法 comparator 和可逆 probe 分开保存。`research-pack/v1` runtime 还会以 append-only 方式保存 failure polarity、what did not fail、RewindProposal、Lesson、ReopenedProblem、canonical downgrade 和 Human route authorization。它是独立插件；只有明确授权交付后，才把有边界的证据包交给唯一的 `$allinluna` Skill。

## 运行边界

```text
问题/证据包 -> 路线地图 -> Claims/Evidence -> failure/recovery 记录 -> 可逆 probe -> HumanDecision seam -> 明确交接
```

研究 Pack runtime 位于 `plugins/research-routes/runtime/research_routes_runtime/`，只使用 Core 的通用 artifact、snapshot、decision 和 promotion 边界。terrain map 不选择路线，也不授权 experiment、implementation 或 canonical state；route authorization 必须引用 confirmed HumanDecision，canonical promotion 还需独立的 `canonical-promotion` decision。分发包从唯一 canonical `allinluna_runtime` 加载宿主运行时，并同步研究 Pack runtime。分发验收报告区分 `requested`、`resolved`、`actual` 资源值。`identity`、`create`、`read`、`wait`、`cancel` 与 `idempotency` 都完整时为 `PASS`；缺失或阻塞的证据为 `BLOCKED`。

## 安装与文件位置

- 插件技能：`plugins/research-routes/skills/`
- 共享 public Skill：`plugins/research-routes/skills/allinluna/`
- canonical runtime：`plugins/research-routes/runtime/allinluna_runtime/`
- 插件清单：`plugins/research-routes/.codex-plugin/plugin.json`

Apache License 2.0，详见 `LICENSE`。
