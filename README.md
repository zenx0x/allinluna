# All in Luna

[简体中文（默认）](README.md) | [English](README.en.md)

**自适应编排。你的模型、你的预算、你的方式。**

All in Luna 是一个开源 Codex 插件，用于规划并完整执行软件开发项目，同时让用户明确控制模型、推理强度、委派方式、并发度和资源限制。它既支持读取已有仓库，也支持从一个全新想法开始；既能处理只需简短计划的小任务，也能管理可恢复、长期运行的多智能体工程。

这个名字也是一种承诺：你既可以组合使用不同模型，也可以启用严格的 Luna-only 策略，甚至使用更激进的 `mad-luna`（疯狂 Luna）模式。

```text
已有仓库或新想法
  -> 完整且通过验证的计划
  -> 解析运行时模型和平台能力
  -> 文件所有权互不冲突的实施线路
  -> 一次阶段集成
  -> 一次独立验收
  -> 获得验收通过的公共基线
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
| `speed` | 缩短总耗时 | 对真正独立的负责人线路实施更积极的并行 |
| `all-luna` | 一致的 Luna-only 执行 | 硬性锁定 Luna 模型家族，使用高推理强度和中等并发 |
| `mad-luna` | 最大化 Luna 集群 | 硬性锁定 Luna、最高推理、最大安全并发，并由独立 Luna 验证高风险工作 |
| `custom` | 完全由用户控制 | 分角色指定模型、推理、回退、并发和预算策略 |

资源模式只改变资源分配和完成速度，不会改变任务范围或完成标准。硬模型锁绝不会被静默绕过。如果请求的模型不可用，All in Luna 会明确报告差异，并执行预先设定的暂停或回退策略。

模型、推理强度、委派层级、并发和预算是五个相互独立的控制维度。逻辑模型层级会在运行时根据宿主平台当前提供的模型进行解析；运行状态会分别记录请求值和实际值。

默认情况下，根协调任务会把独立且有实质交付的负责人线路派发为用户可见的顶层 Codex 任务。每个顶层负责人可以在自己的所有权和模型策略内使用有界 subagents；根协调任务不会静默地用 subagent 替代原定顶层负责人。

在 Codex App 中，All in Luna 会分别读取用户可见顶层任务与 subagent 的模型目录。因此，即使 subagent 只暴露 Sol/Terra，只要 `create_thread` 暴露 Luna，Luna 仍可用于顶层任务。Goal 权限与顶层任务权限彼此独立。

如果用户之后要求执行一份较早生成的 plan-only 文件，All in Luna 会创建独立且经过验证的 execute-ready 修订版。当前明确授予的顶层任务权限会写入新修订版；历史计划不会被修改，旧的 `top_level_tasks=false` 也不会再触发静默 subagent 回退。

## 默认执行拓扑（重要）

All in Luna **默认不是在当前任务里单线程顺序完成所有工作**。通过插件自带的 Plan/Run 入口启动时，它会：

1. 识别可以安全并行、拥有独立交付物和文件边界的主体线路；
2. 为这些线路创建多个用户可见的顶层 Codex 任务，它们会出现在 Codex 侧边栏；
3. 让根协调任务负责依赖排序、等待、缺陷退回、阶段集成和验收；
4. 允许每个顶层负责人按自身需要继续创建有界 subagents；
5. 仅在任务很小、强耦合、共享写入边界无法安全拆分时使用单任务顺序执行。

资源模式给出的期望并发为：`balanced` 3 个、`premium` 4 个、`economy` 2 个、`speed` 6 个、`all-luna` 4 个、`mad-luna` 8 个。实际同时运行数受宿主平台并发上限、任务依赖和文件所有权约束。All in Luna 不会为了凑数量而把每个小修复都创建成顶层任务。

插件自带的默认中文 Prompt 已明确授权创建这些顶层任务，因此普通用户不需要手写 `top_level_tasks=true`。如果用户绕过插件入口、自己编写含义不明确的 Prompt，All in Luna 会询问一次，而不会静默退回根级 subagent 或伪装成并行执行。

第一次使用可以直接输入：

```text
使用 All in Luna 完整推进当前项目。先生成完整计划，再通过多个侧边栏可见的顶层 Codex 任务并行实施；每个顶层负责人可以按需要使用有界 subagents。使用 balanced 模式，不创建 Goal。
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
通过顶层 Codex 任务负责人执行；每个负责人可以使用有界 subagents。使用 economy 模式；如需升级模型，必须先询问我。
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
- 用户可见顶层任务是另一项独立授权；禁止 Goal 不等于禁止创建顶层任务。
- Skill 自带 Prompt 会明确授权顶层负责人，因此这是普通用户默认的首次使用路径。
- 每个顶层负责人可以使用有界 subagents；根协调任务不得用 subagent 替代负责人线路。
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
python plugins/allinluna/skills/allinluna-run/scripts/render_status.py RUN_DIRECTORY
python plugins/allinluna/skills/allinluna-run/scripts/validate_run.py RUN_DIRECTORY --pretty
```

Schema 和可编辑示例位于各 Skill 的 `assets/` 目录中。触发评测和行为评测位于 `evals/`，并与完整生命周期测试一起在 CI 中运行。

## 设计参考

本项目的实现是原创的，并从 Agent Skills 规范、OpenAI 插件示例、Anthropic 的 Skill 评测方法、Vercel 的渐进式信息披露模式以及 Superpowers 的规划与验证工作流中吸收通用经验。项目没有复制第三方 Skill 的文本或代码。

## 许可证

采用 Apache License 2.0，详见 [LICENSE](LICENSE)。
