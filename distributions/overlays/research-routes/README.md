# Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

**把问题交给 Research Routes，你得到一张路线中立的证据地图：Claims、Evidence、unknowns、矛盾、failure regimes 和可逆 probe 保持分开。**

Research Routes 可以独立安装，也可以和 All in Luna 共存。它负责路线中立研究；明确授权产品交付后，再把有边界的证据包交给 All in Luna。

## 普通用户固定路径

```text
一句研究问题/已有证据包 → 一次资源卡确认 → Coordinator → route dependency waves → result
```

一次资源卡确认研究范围、交付模式、速度/模型、Coordinator，以及用户提供的 skills/plugins/MCP bindings。Research Routes 不会默认多层治理、不会频繁打断、不会每次 real canary；普通探索也不要求把 terrain map 变成实现计划。

| 模式 | 默认路径 | 边界 |
| --- | --- | --- |
| `quick` | Coordinator + 必要 route Owner | 小范围路线整理；默认不做 Integration/Acceptance |
| `standard` | Coordinator + 多个独立 route Owner | 需要并行比较时使用；只有共享证据工件或真实冲突才做一次 Integration |
| `full` | Coordinator + route Owners + 风险所需路径 | 只用于高风险、大型跨契约或科研权威任务；显式提高证据边界，lean runtime 不物化 Acceptance/CounterPilot lane |

`fast`、`ultra-fast` 和 `all-luna` 仍是可选资源策略；它们改变速度/模型锁，不会自动增加治理层。完整第三方计划仍使用 `parallel-only`，保留原方向和完成标准。

## 三个研究入口

1. **`$research-routes-plan`**：定义问题边界、候选路线、Claims/Evidence 结构和未知。
2. **`$research-routes-explore`**：比较路线，保留正负/矛盾证据，识别 failure regimes，设计可逆 probe。
3. **`$research-routes-run`**：只在明确授权的研究范围内执行 probe、记录结果并保留回滚边界。

AI 推断不会自动变成 experiment authorization、implementation order、HumanDecision 或 canonical state。每个 Claim 应指向 Evidence，Evidence 保留 polarity，probe 明确 `reversible: true`。

## 首次使用证据（按需）

普通研究运行不要求每次 real canary。需要核验真实主机时，receipt 区分 `requested`、`resolved`、`actual`；CI 只报告 `FIXTURE_PASS`，完整真实证据才可能是 `REAL_PASS`；缺失证据为 `BLOCKED`/`UNVERIFIED`，Integration 边界为 `mechanical-only`。详细只读 protocol 见 [first-use protocol](https://github.com/zenx0x/allinluna/blob/main/docs/first-use-protocol.md)。

## 交接给 All in Luna

准备进入产品实现时，交接路线/证据包并列出仍为 unknown 的事实，不要把 terrain map 直接当成实现计划。All in Luna 的入口、一次资源卡和依赖波次见 [All in Luna README](../../../README.md)。

## 安装与文件位置

从源码仓库安装时，在 Codex Plugins 选择仓库根目录；根 marketplace 同时列出两个发行版。独立发行包的真实插件根是 `plugins/research-routes/`，不需要在包内再次运行源仓库构建器。

- 插件入口：`plugins/research-routes/skills/`，包括 `plugins/research-routes/skills/research-routes`
- 清单：`plugins/research-routes/.codex-plugin/plugin.json`
- 共享契约：`plugins/research-routes/shared/`
- All in Luna 的详细用户流：`plugins/allinluna/skills/allinluna-run/references/user-flow.md`

Apache License 2.0，详见 `LICENSE`。
