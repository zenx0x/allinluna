# Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

Research Routes 保持路线中立：Claims、Evidence、unknowns、矛盾、failure regimes 和可逆 probe 分开保存。它是独立插件；只有明确授权交付后，才把有边界的证据包交给唯一的 `$allinluna` Skill。

## 运行边界

```text
问题/证据包 -> 路线地图 -> Claims/Evidence -> 可逆 probe -> 明确交接
```

分发包从唯一 canonical `allinluna_runtime` 构建。分发验收报告区分 `requested`、`resolved`、`actual` 资源值。`identity`、`create`、`read`、`wait`、`cancel` 与 `idempotency` 都完整时为 `PASS`；缺失或阻塞的证据为 `BLOCKED`。

## 安装与文件位置

- 插件技能：`plugins/research-routes/skills/`
- 共享 public Skill：`plugins/research-routes/skills/allinluna/`
- canonical runtime：`plugins/research-routes/runtime/allinluna_runtime/`
- 插件清单：`plugins/research-routes/.codex-plugin/plugin.json`

Apache License 2.0，详见 `LICENSE`。
