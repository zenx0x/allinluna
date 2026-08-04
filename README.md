# All in Luna

[简体中文（默认）](README.md) | [English](README.en.md)

**给 All in Luna 一句目标，得到的是一条真正执行到结果的 Codex 工作流，而不是一轮更长的方案讨论。**

All in Luna 把用户目标交给独立 Coordinator，再按依赖波次安排必要的 Owner。普通用户不需要先学习 schema、run-state 或多层治理。

## 普通用户固定路径

```text
一句需求/已有计划 → 一次资源卡确认 → Coordinator → dependency waves → result
```

1. **一句需求/已有计划**：直接说想得到什么，或提供已有计划、第三方计划、仓库/本地路径和已有上下文。
2. **一次资源卡确认**：只确认一次交付模式、速度/模型偏好、并发、Coordinator，以及用户提供的 skills/plugins/MCP bindings。
3. **Coordinator**：独立 Coordinator 接管依赖、Owner 派发、恢复和完成证据；Sponsor 保留方向与人的选择。
4. **dependency waves**：Coordinator 释放依赖已满足且文件无冲突的 Owner 波次。
5. **result**：返回完整范围、实际检查、工件/提交证据和剩余 blocker。

### 三种交付模式

| 模式 | 默认路径 | 治理边界 |
| --- | --- | --- |
| `quick` | Coordinator + 必要 Owner | 默认适合小而清楚的工作；默认不创建 Integration/Acceptance |
| `standard` | Coordinator + 多个独立 Owner | 依赖图确实需要并行时使用；只有共享契约、真实跨 Owner 冲突或计划要求才做一次 Integration |
| `full` | Coordinator + Owners + 风险所需路径 | 只用于高风险、大型跨契约或科研权威任务；显式提高证据边界，lean runtime 仍不物化 Acceptance/CounterPilot lane |

模式改变拓扑和等待时间，不会缩小完成标准。All in Luna **不会默认多层治理、不会频繁打断、不会每次 real canary**。一次资源卡足够；只有实际风险、缺失授权或必要人的选择才会再次停下来。

## 默认执行拓扑（重要）

Sponsor 保持方向，独立 Coordinator 负责依赖波次，再派发侧边栏可见的顶层 Codex 任务 Owner。`quick` 只创建必要 Owner；`standard` 在依赖图有价值时并行多个 Owner；`full` 显式提高高风险、大型跨契约或科研权威任务的证据边界，lean runtime 仍不物化额外 Acceptance/CounterPilot lane。

## 保留的兼容路径

- 已有完整计划使用 `parallel-only`：保留原方向和完成标准，只规范安全执行所需的依赖、所有权、资源、恢复和派发。
- `fast` 与 `ultra-fast` 仍然可用来提高并发目标；`all-luna` 与 `mad-luna` 仍然保留 Luna-family hard lock。它们是资源/速度选择，不会自动增加治理层。
- 用户提供的 skills、plugins 和 MCP bindings 保持在资源卡与运行证据中。`requested`、`resolved`、`actual` 分开记录；主机没有 receipt 时显示 `unavailable`，不会假装调用成功。
- Goal、push、发布、部署、凭据和 live external writes 均需单独授权，默认不会发生。

## 资源模式（按需）

以下 profile 仍可在资源卡中选择；它们调整分配、模型或速度，不改变完成标准，也不会单独添加治理层：`premium`、`balanced`、`economy`、`speed`、`fast`、`ultra-fast`、`all-luna`、`mad-luna`、`custom`。

## 结果如何推进

Coordinator 按 dependency waves 释放冲突隔离的 Owner。每个 Owner 有独占范围、自包含 brief、worktree/提交身份和定向检查；某一波阻塞时，不相关波次继续。完成不是首个切片、一次 dispatch、一个 commit 或一个 smoke test，而是用户授权范围真正闭环。

`quick` 通常以 Owner 检查结束；`standard` 在确有共享结果时最多做一次机械 Integration；`full` 是显式风险/证据升级，不会让 lean runtime 物化额外治理 lane。产品/科研语义缺陷回到原 Owner。

## Research Routes 的关系

Research Routes 负责路线中立的 Claims、Evidence、unknowns、矛盾、failure regimes 和可逆 probe；它不把 terrain map 偷换成 experiment authorization、implementation order、HumanDecision 或 canonical state。准备进入产品交付时，把有边界的证据包交给 All in Luna，再走同一条一次资源卡确认和依赖波次路径。

## 首次使用证据（按需）

普通执行不要求每次 real canary。需要核验真实主机时，再查看 [`docs/first-use-protocol.md`](docs/first-use-protocol.md)：receipt 会区分 `requested`、`resolved`、`actual`；CI 只报告 `FIXTURE_PASS`，完整真实证据才可能是 `REAL_PASS`；缺失证据为 `BLOCKED`/`UNVERIFIED`，Integration 边界为 `mechanical-only`。

## 最短入口示例

只有需求时：

```text
使用 All in Luna 完整实现这个目标：
[一句目标、用户、约束和完成定义]
请给我一次资源卡确认；确认后由 Coordinator 按依赖波次执行到 result。
```

已有计划时：

```text
使用 All in Luna 的 parallel-only 执行这个已有计划：
计划路径：[path 或粘贴内容]
保留原方向和完成标准，只做一次资源确认，然后由 Coordinator 持续执行到结果。
```

## 安装与按需深入

从 Codex Plugins 选择本地仓库根目录，或直接选择 `plugins/allinluna/`。构建双发行版：

```powershell
python scripts/build_distributions.py --output dist
python scripts/validate_distributions.py
python scripts/validate_installations.py
```

普通用户可以停在本页。需要精确控制时再看：

- [Conversation Intake](plugins/allinluna/skills/allinluna-intake/SKILL.md)：接收一句需求、上下文和已有计划；
- [Launch Confirmation](plugins/allinluna/skills/allinluna-launch/SKILL.md)：生成唯一资源确认卡；
- [Plan](plugins/allinluna/skills/allinluna-plan/SKILL.md)：把想法或不完整计划整理成可执行契约；
- [Run](plugins/allinluna/skills/allinluna-run/SKILL.md)：短入口；深层规则从其 references 按需读取；
- [First-use protocol](docs/first-use-protocol.md)：只读的真实 receipt/fixture 核验；
- [Research Routes distribution](distributions/overlays/research-routes/README.md)：独立研究发行和边界。

Apache License 2.0，详见 [`LICENSE`](LICENSE)。
