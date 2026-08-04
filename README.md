# All in Luna

[简体中文（默认）](README.md) | [English](README.en.md)

**把一个目标交给 All in Luna，你得到的不是一条更长的聊天记录，而是一支真正开工的 Codex 团队：独立 Coordinator 拆依赖，多个顶层 Owner 在侧边栏并发推进，最后集成、恢复并验收。**

如果你第一次看到这个项目，只要记住三件事：它适合把事情完整做完；中文是默认入口；你不需要先学会 schema 才能开始。

## 30 秒看懂

All in Luna 是面向完整交付的 Codex 编排层。它把“想做什么”变成可执行的工作流，并把计划、资源、所有权、worktree、失败恢复、集成和验收放进同一条真实路径。

你会得到：

- 一个保持方向和授权的 Sponsor 对话；
- 一个独立的 primary Coordinator，负责依赖、资源、派发和恢复；
- 侧边栏中多个可见的顶层 Owner，每个 Owner 有清晰的范围和验证结果；
- 按需加入的 CounterPilot、Integration 和 Acceptance；
- 一个完整结果，而不是“先做一个 demo，剩下的以后再说”。

All in Luna 默认不创建 Goal。Goal、push、发布和外部写入都必须由你明确授权；并发模式改变资源和速度，不会悄悄缩小完成标准。

## 它和普通单代理 / GSD 的区别

| 体验 | 普通单代理或普通 GSD | All in Luna |
| --- | --- | --- |
| 谁在推进 | 一个线程读、想、写、等 | Sponsor 保持方向，Coordinator 管调度，多个 Owner 真正执行 |
| 并发方式 | 通常串行，或把并发藏在一次回复里 | 独立 Owner 是侧边栏可见的顶层 Codex 任务，可在依赖允许时并发 |
| 上下文与文件 | 一个上下文、一个工作区，边界容易变模糊 | 每个 Owner 有自包含 brief、所有权和 worktree；共享文件有明确归属 |
| 出错之后 | 回到原聊天手动解释和重做 | 记录状态，恢复未完成 Owner；行为缺陷回到原 Owner 修复 |
| 结束标准 | 计划写完或首个切片通过就停止 | 所有授权范围执行完，经过需要的集成与验收才算完成 |

## 默认执行拓扑（重要）：Sponsor → Coordinator → 多个 Owner

你在当前对话里是 Sponsor：说明目标、补充事实、做需要人的选择。运行开始后，All in Luna 会创建独立 Coordinator；Coordinator 再把真正的产品工作派成侧边栏中的顶层任务。Sponsor 不会冒充 Coordinator，也不会把普通 Owner 的实现塞回当前聊天。

```text
你 / Sponsor
└─ All in Luna Coordinator（独立、负责依赖与恢复）
   ├─ CounterPilot（可选，独立只读挑战）
   ├─ Owner：后端 / 数据
   ├─ Owner：前端 / 交互
   ├─ Owner：测试 / 文档
   ├─ Integration（合并机械边界，必要时）
   └─ Acceptance（独立检查，按风险启用）
```

这就是你预期看到的侧边栏：不是一个“总管线程”假装完成了所有工作，而是 Coordinator、CounterPilot 和多个有名字的顶层 Owner 各自有进度、提交、worktree 和验证证据。依赖未满足的 Owner 会等待；不相关的 Owner 可以继续。

## 三种最快入口

### 1. 只有想法

直接告诉它你想得到的结果，不必先写计划。All in Luna 会先接收现有上下文，再把想法整理成完整计划。

```text
使用 All in Luna 把这个想法做成可交付产品：
[写下目标、用户、约束和“完成”的定义]
先保留我的方向，再给出可执行计划；批准后由独立 Coordinator 派发侧边栏顶层 Owner，完整执行到验收。
```

### 2. 已有计划：给路径或直接粘贴

可以给 `.md`、`.txt`、`.json`、`.yaml` 或仓库路径，也可以把计划全文粘贴进来。已有完整计划使用 `parallel-only`，只规范依赖、所有权、资源和恢复，不重新设计产品方向。

```text
使用 All in Luna 的 parallel-only 执行这个已有计划：
计划路径：C:\path\to\plan.md
保持原方向和完成标准；请由独立 Coordinator 派发无冲突的侧边栏顶层 Owner，并持续执行到计划结束。
```

### 3. 只接管并发执行

如果计划已经批准、方向已经冻结，只需说清楚“接管执行”，不必再走一轮方案讨论。

```text
只接管并发执行，不重写产品方向：
[粘贴已批准计划，或给出计划文件路径]
使用 parallel-only；保留依赖、所有权、停止边界和恢复约束，完成所有 Owner、集成与计划要求的验证。
```

## Spark：轻量、机械、边界清晰的执行资源

随本版本加入的 Spark（`gpt-5.3-codex-spark`）面向机械文档、格式化、边界清晰的小修复、扫描与分类、定向测试、确定性迁移和 boilerplate。它适合把一批低歧义工作快速做完，同时把完整交付的范围留给正确的 Owner。

Spark 不是 Coordinator、CounterPilot、科学权威、架构集成者，也不是独立验收者；它不负责拆全局依赖、做科学判断、合并跨 Owner 语义或替人确认完成。实际可用性以当前运行时发现为准；不可用时应按资源 profile 的真实 fallback 或暂停策略处理，不把请求值冒充成已使用模型。

## CounterPilot：可选的独立第二视角

CounterPilot 是只读的独立挑战者，用来检查范围、假设、依赖、恢复声明或关键里程碑。它可以指出可复现的问题并给出证据，但不能直接改产品文件、提升权限或替你做人的决定；确认是实现缺陷时，问题回到原 Owner。

| 模式 | 什么时候用 | 你会看到什么 |
| --- | --- | --- |
| `off` | 低风险、方向很明确的小改动 | 不创建 CounterPilot |
| `risk-triggered` | 默认推荐 | 在高风险、失败或边界变化时挑战一次 |
| `milestone` | 想在关键节点复核 | 在计划形成、集成前或里程碑触发 |
| `continuous` | 长周期或高不确定性工作 | 持续提供独立、只读的证据检查 |

## 资源模式：用户看到的模型、推理与并发

下表是“请求策略”，不是虚构的固定模型名：`tier:frontier`、`tier:standard`、`tier:fast` 和 `family:luna` 会由当前 Codex 主机解析为实际可用模型；运行状态分别记录 requested 与 actual，主机没有暴露的 telemetry 就记为 unavailable。实际并发还会受主机、机器容量、依赖宽度、文件所有权和预算限制。

| 模式 | 用户看到的模型请求 | 推理侧重点 | 目标并发 | 适用场景 |
| --- | --- | --- | ---: | --- |
| `balanced` | 规划/挑战 `frontier`；Coordinator/Owner `standard`；机械 worker `fast` | 规划 high，挑战 xhigh，Owner high | 8 | 默认；质量、速度、成本均衡 |
| `economy` | 全角色优先 `family:luna` | Coordinator/worker medium，其余按角色 high/max | 4 | 小团队、资源紧张，范围仍完整 |
| `speed` | 规划/挑战 `frontier`；Coordinator `standard`；worker `fast` | Coordinator medium，主要执行 high | 12 | 依赖清楚，优先缩短等待时间 |
| `fast` | 规划/挑战 `frontier`；Coordinator/Owner `standard`；worker `fast` | 规划/挑战 xhigh，Coordinator/Owner high | 24 | 多个独立 Owner，适合层级调度 |
| `ultra-fast` | 规划/协调/挑战 `frontier`；Owner `standard`；worker `fast` | 规划/挑战 ultra，集成 xhigh，执行 high | 48 | 大量无冲突任务，机器和主机允许时 |
| `all-luna` | 全角色硬锁 `family:luna` | 通常 high；CounterPilot max | 8 | 希望所有角色使用 Luna 家族 |
| `mad-luna` | 全角色硬锁 `family:luna` | 所有角色 max；高风险独立复核 | 24 | Luna-only 的最大安全并发，不突破主机上限 |
| `custom` | 用户指定具体模型/家族、fallback 和角色分配 | 用户指定 | 1–64 | 有明确模型、预算或组织策略 |

`premium` 也可用：目标并发 12，规划/权威/验收优先 `frontier` 与 max 推理，适合高风险决策。资源模式只改变分配和速度，不会把“完整执行”降级成 MVP。

## 第一次使用：你会先看到什么

第一次运行的目标不是让你学习内部协议，而是让你看见一条真实、可追踪的执行链：Sponsor 对话保持你的方向，独立 Coordinator 出现在侧边栏，随后出现两个或更多有名字的顶层 Owner。重复刷新或 tick 只会对已知 dispatch 做 `no-op`、`reuse` 或 `wait`，不会重复创建同一个 Owner。

接下来你会看到 Owner 的真实 thread receipt、host/worktree/repo 身份和 monitor cursor；最后到达 `mechanical-only` integration boundary。持久 receipt 还必须保留 `source=codex_app`、`actual_tool`、完整 capability、monitor cursor/receipt 和 integration boundary；如果只有 `threadId`、`hostId`、output dir，结果会明确显示 BLOCKED/UNVERIFIED，不会把 CI fixture 当成真实成功。

### 资源确认卡

运行状态应把三件事分开显示：`requested`（你请求的工具/能力）、`resolved`（主机解析到的工具/能力）、`actual`（主机 receipt 证明实际使用的工具/能力）。主机没有暴露 telemetry 时显示 `unavailable`；不要把请求值当成实际值。

| 你确认的字段 | 你应看到的证据 |
| --- | --- |
| `thread` / `host` | Sponsor、Coordinator、每个 Owner 的身份彼此可区分 |
| `worktree` / `repo` | Owner receipt 中的真实隔离位置与仓库身份 |
| `duplicate` | 重复 tick 为 `no-op`，已完成 Owner 为 `reuse`，未完成 Owner 为 `wait` |
| `monitor` / `integration` | cursor 与 receipt 齐全，integration 明确只做机械对账 |

### 最短可复制 prompt

```text
使用 All in Luna 完整实现这个目标：
[目标、用户、约束和完成定义]
先接收我已经提供的上下文，再创建独立 Coordinator 和多个侧边栏顶层 Owner；持续执行到真实 thread receipt、monitor、集成和验收。不要创建 Goal、不要 push 或发布，除非我明确授权。
```

### 一个成功运行与一次失败恢复

成功运行会得到：Coordinator → 多个顶层 Owner → 重复 tick 无重复 → 每个 Owner 的真实 receipt → monitor cursor → mechanical-only integration。失败时，Owner 的 `product_failure` 会回到原 dispatch 身份恢复；host/tool 不可用与 checker error 会分别停在 BLOCKED 或 CHECKER_ERROR，并报告缺少的证据。

高级 protocol、schema 和只读 checker 见 [`docs/first-use-protocol.md`](docs/first-use-protocol.md)。CI 可以运行 fixture success/recovery，但 fixture 的 `FIXTURE_PASS` 永远不等于 `REAL_PASS`。

## 最短安装与首次使用

### 直接从这个仓库安装

在 Codex Plugins 中选择从本地路径安装，选择仓库根目录。根目录的 `.agents/plugins/marketplace.json` 会同时列出 `allinluna` 和 `research-routes`；只想安装 All in Luna 时，也可以直接选择 `plugins/allinluna/`。

### 构建可携带的本地发行包

```powershell
python scripts/build_distributions.py --output dist
python scripts/validate_distributions.py
python scripts/validate_installations.py
```

然后把 `dist/all-in-luna` 作为本地 Codex 插件来源安装。第一次使用可以直接复制：

```text
使用 All in Luna 完整实现这个目标：
[目标或计划]
中文默认；先接收我已经提供的上下文，再创建独立 Coordinator 和需要的 CounterPilot；把独立工作放进侧边栏多个顶层 Owner，持续执行、集成并验收。不要创建 Goal、不要 push、不要发布，除非我明确授权。
```

Coordinator 的派发会在侧边栏显示为可追踪的 Owner 任务，你可以看到每个 Owner 的状态、worktree、提交和验证；如果状态更新有延迟，系统会先依据已记录的 task/worktree 身份恢复和对账，不会因为重复轮询就再次创建同一个 Owner 或 worktree。

## 两个小例子：从输入到完整结果

### 小型软件

```text
使用 All in Luna 做一个小型待办服务：Python + FastAPI，支持新增/完成/删除、SQLite 持久化、最小 Web 页面、API 测试和 README。
请先给完整依赖计划；批准后并发拆分后端、页面、测试/文档，保留 worktree 边界，运行测试并交付可启动结果。
```

预期侧边栏：Coordinator → `Owner: API + SQLite`、`Owner: Web UI`、`Owner: tests + README` → Integration → Acceptance。你最后拿到的是能启动、能测试、能交接的项目，而不是三个互不相干的代码片段。

### 科研路线

```text
使用 Research Routes 比较“稀疏检索”和“知识图谱检索”解决这个问题：
保留 Claims、Evidence、未知、矛盾结果和 failure regimes，不要提前选路线；只设计一个 reversible 的下一步 probe。
如果之后决定进入产品实现，再把有边界的证据包交给 All in Luna。
```

预期侧边栏：Research Routes 的 route owners 并行整理路线 A/B 与证据，CounterPilot 保持只读挑战；完成后得到路线中立的 terrain map 和可逆 probe，而不是被 AI 偷换出来的实验授权或 canonical 结论。

## 首次运行后，你应该看到什么

- Sponsor 对话仍由你掌握，且不会因为“还没有 Goal”就失去顶层任务编排。
- 独立 Coordinator 出现在侧边栏，并拥有依赖、资源、恢复和完成状态。
- 每个实质性工作面都有自己的顶层 Owner；Owner 内部可以有有界 subagent，但 subagent 不是额外的完成证明。
- 计划未完成时，系统继续派发可执行且无冲突的任务；失败会留下证据并回到正确 Owner。
- 运行结束前，必须能回答“谁做了什么、在哪个 worktree、通过了哪些检查、还有什么未知”。

## 常见问题

**我没有计划，能直接开始吗？** 可以。给想法、目标、约束和完成定义，先从 `$allinluna-plan` 开始。

**我已经有一份完整计划，会不会被重新设计？** 不会。明确使用 `parallel-only`，All in Luna 只规范依赖、所有权、资源、恢复和派发。

**并发 48 是否一定会同时开 48 个任务？** 不一定。48 是 `ultra-fast` 的目标值；主机、机器、依赖宽度和文件冲突会决定实际并发。

**Coordinator 派发有延迟，会不会重复创建 Owner？** 不会。派发身份和 worktree 会被记录并对账；延迟只影响你看到进度的时间，不会把同一个 Owner 变成第二个 Owner。

**Owner 会不会互相覆盖文件？** 独立 Owner 使用独占路径和 worktree；共享文件应归 Integration 或明确的共享 Owner，冲突不会靠静默选边解决。

**CounterPilot 会不会阻止我做决定？** 它只读并提供证据。产品方向、权威边界和不可逆选择仍由你决定。

**All in Luna 会自动 push、发布或创建 Goal 吗？** 默认不会。push、发布、外部写入和 Goal 都需要明确授权。

**Research Routes 和 All in Luna 是什么关系？** Research Routes 负责多路线证据与可逆探索；All in Luna 负责经过授权的完整软件交付。研究地图不是实现计划。

## 高级文档与发行说明

普通用户可以停在上面；需要精确控制时再看：

- 入口接收与路由：[`allinluna-intake`](plugins/allinluna/skills/allinluna-intake/SKILL.md)；
- 计划契约：[`allinluna-plan`](plugins/allinluna/skills/allinluna-plan/SKILL.md)；
- 执行、恢复、Owner 和验收：[`allinluna-run`](plugins/allinluna/skills/allinluna-run/SKILL.md)；
- 真实资源 profile：[`resource-profiles.json`](plugins/allinluna/skills/allinluna-run/assets/resource-profiles.json)；
- 双发行版契约：[`distribution-manifest.json`](distributions/distribution-manifest.json)；
- Research Routes 的独立入口与边界：[`plugins/research-routes/skills/`](plugins/research-routes/skills/)。

## 许可证

Apache License 2.0，详见 [`LICENSE`](LICENSE)。
