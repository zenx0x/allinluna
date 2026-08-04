# All in Luna

[简体中文（默认）](README.md) | [English](README.en.md)

**自适应编排。你的模型、你的预算、你的方式。**

All in Luna 是一个开源 Codex 插件，用于规划并完整执行软件开发项目，同时让用户明确控制模型、推理强度、委派方式、并发度和资源限制。它既支持读取已有仓库，也支持从一个全新想法开始；既能处理只需简短计划的小任务，也能管理可恢复、长期运行的多智能体工程。

这个名字也是一种承诺：你既可以组合使用不同模型，也可以启用严格的 Luna-only 策略，甚至使用更激进的 `mad-luna`（疯狂 Luna）模式。

```text
已有仓库或新想法
  -> 完整且通过验证的计划
  -> 用户主对话 / Sponsor
  -> 独立主 Coordinator + 可选 CounterPilot
  -> 子 Coordinator 分片 + Owners
  -> 风险所需的集成与验收
  -> 完整停止边界
```

## 内置 Skills

- `allinluna-plan`：检查已有仓库或新项目想法，生成完整、依赖关系明确的可执行开发计划。除非用户明确要求，否则不会创建 Goal，也不会开始实施。
- `allinluna-run`：执行已经批准的计划，分别记录请求和实际使用的运行设置，在适合时协调隔离的任务负责人，并能在上下文压缩或中断后恢复，持续推进到集成和验收完成。

## 资源模式

| 模式 | 目标 | 默认行为 |
| --- | --- | --- |
| `premium` | 最高决策质量 | 前沿模型负责规划和验收；强工程模型实施；高风险工作独立复核 |
| `balanced` | 推荐的通用默认值 | 强规划、高效实施和有界并行之间取得平衡 |
| `economy` | 降低资源消耗 | Luna 优先、低并发，升级模型前需要明确授权 |
| `speed` | 缩短总耗时 | 默认目标 12，对独立线路积极并行 |
| `fast` | 高速并发 | 默认目标 24，启用层级协调 |
| `ultra-fast` | 超高速并发 | 默认目标 48，启用层级协调和高质量拆解检查 |
| `all-luna` | 一致的 Luna-only 执行 | 硬性锁定 Luna 模型家族，使用高推理强度和中等并发 |
| `mad-luna` | 最大化 Luna 集群 | 硬性锁定 Luna、最高推理、最大安全并发，并由独立 Luna 验证高风险工作 |
| `custom` | 完全由用户控制 | 分角色指定模型、推理、回退、并发和预算策略 |

资源模式只改变资源分配和完成速度，不会改变任务范围或完成标准。硬模型锁绝不会被静默绕过。如果请求的模型不可用，All in Luna 会明确报告差异，并执行预先设定的暂停或回退策略。

模型、推理强度、委派层级、并发和预算是五个相互独立的控制维度。逻辑模型层级会在运行时根据宿主平台当前提供的模型进行解析；运行状态会分别记录请求值和实际值。

模型解析支持到 `ultra`，并可依据运行目录提供的质量、速度和经济性元数据按 profile 加权选择。Fallback list 会真正逐项执行；缺少评分时保留目录顺序，不伪造成本或性能数据。

默认情况下，用户正在对话的任务是 Sponsor，而不是 Coordinator。Sponsor 负责需求、方向、授权、资源和状态查看；它首先创建一个独立、侧边栏可见的主 Coordinator。主 Coordinator 再创建 Owners，并在并发较高时创建子 Coordinator。每个 Owner 可以在自己的所有权和模型策略内使用有界 subagents。

CounterPilot 是独立的只读逆向副驾驶：它挑战隐含假设、范围缩水、依赖冲突、错误方向和过度治理，但不能无证据阻塞执行，也不代替 Integration 或 Acceptance。

只有在完整检查宿主工具目录后确认“创建顶层任务”的工具确实没有暴露时，All in Luna 才会自动依次回退到根级 subagent、当前任务顺序执行；此时不再要求用户重复确认。计划仍保持 `top_level_tasks=true`，运行状态会记录真实执行层级和 `top-level-tool-unavailable`。如果回退层级无法满足 Luna-only 等硬模型锁，则暂停该线路，而不是冒充满足或更换模型。

在 Codex App 中，All in Luna 会分别读取用户可见顶层任务与 subagent 的模型目录。因此，即使 subagent 只暴露 Sol/Terra，只要 `create_thread` 暴露 Luna，Luna 仍可用于顶层任务。Goal 权限与顶层任务权限彼此独立。

Codex App 的 `create_thread` 通常属于延迟加载工具，未出现在最初的简短工具列表中不代表不可用。All in Luna 必须先搜索宿主的完整/延迟工具目录（例如 `functions.exec` 的 `ALL_TOOLS` 或工具搜索接口），找到后直接创建顶层任务。没有完成这一步就报告“顶层任务工具不可用”，属于执行错误。

如果用户之后要求执行一份较早生成的 plan-only 文件，All in Luna 会创建独立且经过验证的 execute-ready 修订版。当前明确授予的顶层任务权限会写入新修订版；历史计划不会被修改，旧的 `top_level_tasks=false` 也不会再触发静默 subagent 回退。

## 默认执行拓扑（重要）

All in Luna **不会让用户主对话自己兼任总协调和产品实现**。通过 Plan/Run 启动时，它会：

1. 识别可以安全并行、拥有独立交付物和文件边界的主体线路；
2. 先创建独立的 `All in Luna Coordinator — 项目名`；
3. 按风险创建 CounterPilot，并由 Coordinator 创建侧边栏可见的顶层 Codex 任务 Owners；
4. 在 16+ 并发或线路过多时，由主 Coordinator 创建子 Coordinator 分片；
5. 允许每个 Owner 使用有界 subagents；
6. 持续执行、监控、返修，直到停止边界。

主 Coordinator 是默认且强制的独立顶层任务，不需要用户额外启用。Sponsor 和 Coordinator 的 thread ID 必须不同；CounterPilot 也必须独立。只读控制面无需等待 Git，只有并行写代码的 Owners 需要 worktree。

Run 现在通过确定性的 coordinator tick 生成下一步动作和完整负责人 brief：派发 ready 顶层任务、记录 thread/host/worktree/model、等待任务、收集证据、释放依赖并继续下一轮。活动计划可以增量追加任务和停止边界；验收缺陷会结构化退回原 owner，未解决缺陷会阻止完成。Git 证据工具可核对真实 commit、parent、tree、changed paths 与所有权范围。

这里的“顺序”只表示 Owners 之间存在依赖，不表示 Sponsor 或 Coordinator 亲自写实现。即使只有一个任务当前可执行，Coordinator 也应派发该 Owner。

资源模式默认期望并发为：`economy` 4、`balanced` 8、`premium` 12、`speed` 12、`fast` 24、`ultra-fast` 48、`all-luna` 8、`mad-luna` 24。也支持 8、12、16、24、48、64 或 1–64 自定义值；实际并发仍受宿主、机器、依赖和文件所有权限制。

用户选择 16+ 时，Plan 会询问是否使用高质量模型检查依赖、冲突、所有权和分片；用户可以接受或拒绝。该检查只发生一次，不扩张成重复治理。`fast` 相对 `speed` 翻倍到 24，`ultra-fast` 再翻倍到 48。

## 精简并发模式

`parallel-only` 用于已经由用户、Grill Me 或其他工具完成规划的情况。All in Luna 不重新讨论产品方向，只把现有计划规范化为依赖 DAG、所有权和顶层任务，并负责并发派发、监控和恢复。低风险计划不会被机械补上 Integration、Acceptance 或多层审查；只有原计划或真实风险要求时才增加。

所有 All in Luna 计划都必须记录 `top_level_tasks=true` 和 `top_level_tasks_basis=allinluna-default`，不存在生成 `false` 的模式。即使项目很小、非 Git、plan-only 或最终只有一条紧耦合线路，该授权仍保持为 `true`；实际可并行任务数再由依赖关系和宿主能力决定。

非 Git 项目会先进入 Git 准备流程：All in Luna 检查 Git 是否安装、目录是否已经初始化、是否存在可供 worktree 使用的基线提交，然后一次性请求安装 Git、初始化仓库和创建基线提交的授权。用户接受后由 All in Luna 完成准备并继续多开隔离的顶层任务；用户拒绝后改用普通 subagents 或当前任务顺序执行，同时保持计划中的 `top_level_tasks=true`，并如实记录实际回退原因。

资源策略可以组合。例如 `all-luna + ultra-fast` 仍硬锁 Luna，同时采用 48 目标并发和层级协调。

第一次使用可以直接输入：

```text
使用 All in Luna 完整推进当前项目。当前对话仅作为 Sponsor，创建独立主 Coordinator 和按风险触发的 CounterPilot；由 Coordinator 派发 Owners。使用 balanced 8 并发，不创建 Goal。
```

## 安装

将本仓库添加为 Codex Marketplace：

```powershell
codex plugin marketplace add zenx0x/allinluna
```

随后在 Codex 的 Plugins 页面中安装 **All in Luna**，并新建一个任务，使 Codex 发现新安装的 Skills。

## 提示词示例

```text
使用 $allinluna-plan 检查这个仓库，只生成完整的实施计划。
使用 balanced 模式。将独立负责人线路规划为用户可见的顶层 Codex 任务；每个负责人可以使用有界 subagents。不要创建 Goal，也不要开始实施。
```

```text
使用 $allinluna-run 完整执行已经批准的计划，持续推进到实施、集成和验收完成。
当前对话只作 Sponsor，创建独立 Coordinator；通过顶层 Codex 任务负责人执行。使用 economy 模式。
```

```text
使用 $allinluna-run 的 parallel-only + fast 模式执行我已经完成的计划。
不要重新设计方案；使用独立 Coordinator，并把计划拆为无冲突的顶层任务并发实施。
```

```text
使用 $allinluna-run 的 mad-luna 模式。将所有委派角色硬锁定为 Luna，
请求宿主支持的最高推理强度和最大安全并发，持久化运行状态，
创建用户可见的独立顶层 Codex 任务，不创建 Goal，绝不静默替换为其他模型。
```

```text
将 $allinluna-run 作为长期 Goal 运行。为相互独立的线路创建隔离的顶层任务，
让协调任务专注于依赖关系、监控和恢复，并持续执行到完整完成标准得到满足。
```

## 行为保证

- 用户要求的完整范围始终是完成标准；第一个纵向切片只是进度检查点。
- Goal 必须由用户明确选择，不能因任务规模较大而自动创建。
- 所有 All in Luna 计划一律授权用户可见顶层任务；禁止 Goal 不会改变这一字段。
- 顶层任务工具确实未暴露、实际无法使用 worktree 或用户拒绝 Git 准备时，只降级运行层级，不把计划字段改回 `false`；工具缺失回退无需再次确认。
- 每个顶层负责人可以使用有界 subagents；主 Coordinator 或子 Coordinator 不得用 subagent 替代负责人线路。
- 项目指令和脏工作区会被检查并保留。
- 独立写入者获得明确的文件所有权；发现缺陷后返回原实施任务修复。
- 请求的模型/推理设置和实际运行设置分开记录。
- 平台没有提供用量或成本信息时，记录为 `unavailable`，绝不编造。
- 实时外部修改和破坏性操作仍然需要用户授权。
- 运行状态默认存储在仓库之外的 `~/.codex/allinluna/runs`。

## 开发与验证

确定性辅助工具只使用 Python 标准库，需要 Python 3.11 或更高版本。

```powershell
python -m unittest discover -s tests -v
python scripts/validate_repository.py
```

本插件不包含 MCP 服务、遥测、托管服务或隐式网络请求。它只编排用户当前 Codex 环境已经提供的能力，并在缺少某种能力时如实从顶层任务降级为 subagent，再降级为顺序执行。

## 确定性辅助工具

两个 Skills 附带了可移植、仅依赖标准库的计划和恢复工具：

```powershell
# 对仓库进行有边界、只读的清点
python plugins/allinluna/skills/allinluna-plan/scripts/inspect_project.py . --pretty

# 验证可执行计划
python plugins/allinluna/skills/allinluna-plan/scripts/validate_plan.py plan.json --pretty

# 根据宿主模型目录解析 mad-luna
python plugins/allinluna/skills/allinluna-run/scripts/resolve_profile.py `
  --profile mad-luna --catalog runtime-catalog.json `
  --delegation top-level-task --pretty

# 初始化、查看和验证可恢复运行状态
python plugins/allinluna/skills/allinluna-run/scripts/prepare_execution_plan.py `
  plan.json --output plan.execute-ready.json `
  --authorize-implementation-writes --authorize-top-level-tasks --deny-goal
python plugins/allinluna/skills/allinluna-run/scripts/init_run.py plan.json `
  --profile balanced --catalog runtime-catalog.json
python plugins/allinluna/skills/allinluna-run/scripts/bootstrap_control_plane.py RUN_DIRECTORY --pretty
python plugins/allinluna/skills/allinluna-run/scripts/sponsor_tick.py RUN_DIRECTORY --pretty
python plugins/allinluna/skills/allinluna-run/scripts/coordinator_tick.py `
  RUN_DIRECTORY --coordinator-id primary --pretty
python plugins/allinluna/skills/allinluna-run/scripts/coordinator_tick.py `
  RUN_DIRECTORY --coordinator-id subcoordinator-1 --pretty
python plugins/allinluna/skills/allinluna-run/scripts/render_status.py RUN_DIRECTORY
python plugins/allinluna/skills/allinluna-run/scripts/validate_run.py RUN_DIRECTORY --pretty

# 活动计划增量修订、缺陷返修与人类控制
python plugins/allinluna/skills/allinluna-run/scripts/revise_active_plan.py `
  RUN_DIRECTORY --patch revision.json --reason "用户追加范围"
python plugins/allinluna/skills/allinluna-run/scripts/manage_defect.py `
  RUN_DIRECTORY --action create --defect-id D1 --owner-task T1 `
  --summary "..." --reproduction "..." --reason "独立验收失败"
python plugins/allinluna/skills/allinluna-run/scripts/control_run.py `
  RUN_DIRECTORY --action set-concurrency --concurrency 12 --reason "用户调整并发"
python plugins/allinluna/skills/allinluna-run/scripts/refresh_task_resources.py `
  RUN_DIRECTORY --catalog runtime-catalog.json `
  --role engineer=gpt-5.6-luna:high --reason "用户调整模型与推理强度"

# 将用户已有计划转换为精简并发执行计划
python plugins/allinluna/skills/allinluna-run/scripts/import_parallel_plan.py `
  existing-plan.json --output parallel.execute-ready.json `
  --profile fast --high-concurrency-review accepted `
  --decomposition-model gpt-5.6-sol
```

资源调整只作用于尚未派发或需要重试的负责人；运行中和已完成任务的实际模型证据不会被改写。

Schema 和可编辑示例位于各 Skill 的 `assets/` 目录中。触发评测和行为评测位于 `evals/`，并与完整生命周期测试一起在 CI 中运行。

## 设计参考

本项目的实现是原创的，并从 Agent Skills 规范、OpenAI 插件示例、Anthropic 的 Skill 评测方法、Vercel 的渐进式信息披露模式以及 Superpowers 的规划与验证工作流中吸收通用经验。项目没有复制第三方 Skill 的文本或代码。

## 许可证

采用 Apache License 2.0，详见 [LICENSE](LICENSE)。
