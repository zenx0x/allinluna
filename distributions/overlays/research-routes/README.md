# Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

**把一个问题放进 Research Routes，你会得到一张保持路线中立的证据地图：Claims、Evidence、矛盾、失败区间和未知被分开保存，下一步只选择可逆的探索。**

Research Routes 是可独立安装、也可与 All in Luna 共存的 Codex 研究发行版。它适合先理解问题和比较路线；All in Luna 负责在明确授权后把边界清楚的软件工作完整交付。

## 30 秒看懂

它不是跳转页，也不是把 terrain map 伪装成实现计划。你可以同时保留路线 A/B、正负证据和失败 regime，直到有足够依据选择下一次 probe。AI 的推断不会自动变成实验授权、实现顺序、HumanDecision 或 canonical state。

## 三个入口

1. **`$research-routes-plan`**：定义问题边界、候选路线、Claims/Evidence 结构和未知。
2. **`$research-routes-explore`**：比较路线，保留正负与矛盾证据，识别 failure regimes，设计可逆 probe。
3. **`$research-routes-run`**：只在明确授权的研究范围内执行 probe、记录结果并保留回滚边界；需要产品实现时，再把有边界的证据包交给 All in Luna。

## 真实体验与一个小例子

```text
使用 Research Routes 比较“稀疏检索”和“知识图谱检索”解决这个问题：
保留 Claims、Evidence、未知、矛盾结果和 failure regimes，不要提前选路线；只设计一个 reversible 的下一步 probe。
```

预期侧边栏结果是多个 route Owner 并行整理路线与证据，CounterPilot 独立只读地挑战假设；最终得到 terrain map 和下一次可逆 probe，而不是一个未经授权的结论。

## 第一次使用：你会先看到什么

第一次使用仍然是 outcome-first：Sponsor 保持问题方向，独立 Coordinator 出现在侧边栏，随后出现多个 route Owner。重复 tick 只会对已知 dispatch 做 `no-op`、`reuse` 或 `wait`，不会重复创建 Owner。你会看到真实 thread receipt、host/worktree/repo 身份和 monitor cursor；最后的 integration 只做 `mechanical-only` 对账。

### 资源确认卡与最短 prompt

`requested` 是请求值，`resolved` 是主机解析值，`actual` 只能由真实 host receipt 证明；缺少 receipt 时必须是 BLOCKED/UNVERIFIED。第一次可直接复制：

```text
使用 Research Routes 比较这个问题的候选路线：
[问题、约束和已知资料]
保留 Claims、Evidence、未知、矛盾和 failure regimes；由独立 Coordinator 派发多个 route Owner，重复 tick 不得重复创建，持续到真实 receipt、monitor 和可逆 probe。不要把 terrain map 当成实验授权或产品实现计划。
```

成功运行会得到路线中立 terrain map、多个 Owner receipt、monitor cursor 和一次可逆 probe；失败时原 Owner 的 `product_failure` 走同一 dispatch 恢复，host/tool unavailable 与 checker error 分别停在 BLOCKED 或 CHECKER_ERROR。CI 的 `FIXTURE_PASS` 永远不等于 `REAL_PASS`。高级 protocol、schema 和只读 checker 见 [first-use protocol](https://github.com/zenx0x/allinluna/blob/main/docs/first-use-protocol.md)。

## 最短安装与首次使用

如果你从源码仓库安装，在 Codex Plugins 中选择本地路径并选择仓库根目录；根目录 marketplace 会同时列出 All in Luna 和 Research Routes。若你拿到的是独立发行包，选择这个包的根目录；其中真正的插件根是 `plugins/research-routes/`，不需要在发行包里再次运行源仓库构建器。

插件入口位于 `plugins/research-routes/skills/`，清单位于 `plugins/research-routes/.codex-plugin/plugin.json`，共享契约位于 `plugins/research-routes/shared/`。

## 科研边界

terrain map 不是 experiment authorization、implementation order、HumanDecision 或 canonical-state promotion。每个 Claim 应指向 Evidence；Evidence 保留 polarity；下一步 probe 必须显式 `reversible: true`。越界授权由共享运行时 fail closed。

准备进入产品实现时，交接一个有边界的路线/证据包，并明确哪些事实仍是 unknown；不要把研究地图直接当成实现计划。

## 适合的案例

- 比较两条科学路线而不提前选定路线。
- 保留相互矛盾的结果和失败区间，避免只留下正向结论。
- 在实现前选择成本可控、可回滚、能区分假设的下一次 probe。
- 对软件方案、论文方向、实验记录或已有资料做可追溯的路线整理。

## 常见问题

**Research Routes 会替我选路线吗？** 不会。它让差异、证据和未知保持可见；人的选择仍需要明确的 HumanDecision。

**它能直接改产品代码吗？** 研究执行保持路线边界。进入软件交付时，把有边界的证据包交给 All in Luna。

**可以只用 All in Luna 吗？** 可以。All in Luna 适合目标已经明确、需要完整开发并发和交付的工作。

## 高级契约与许可证

详细边界见 `plugins/research-routes/skills/research-routes/SKILL.md`、`plugins/research-routes/skills/research-routes-run/SKILL.md`、`plugins/research-routes/shared/` 和 `plugins/research-routes/.codex-plugin/plugin.json`。

Apache License 2.0，详见 `LICENSE`。
