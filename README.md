# All in Luna

[English](README.en.md)

All in Luna 把“把这件事做成”变成一条可继续、可核验的执行路径。你给它一个目标、已有计划、进行中的 run，或 Research Routes 数据包；它会拆出彼此独立的顶层 Task，按依赖推进，并把实现、检查和交接证据保留下来。它适合需要跨多步工具操作、多个交付物，且不能把一句“已完成”当作结果的工作。

## 为什么多一层 Top-level Task

普通 subagent 很适合短小的局部工作，但它们通常只是同一对话中的临时工作者。All in Luna 先把一项结果组织为独立的顶层 Task Lanes：协调器掌握任务间依赖、权限和最终结果；每条 Lane 只拿到完成自己一段工作所需的范围和上下文。这样，一个 Task 的失败、等待或证据缺口不会悄悄变成另一个 Task 的“完成”。局部 worker 仍可使用，但它们不是产品入口，也不会取代顶层 Task。

## 60 秒开始

1. 在 Codex Plugins 中选择 `plugins/allinluna/`，然后直接告诉 All in Luna 你的目标，例如“为服务增加经过测试的 health-check endpoint”。
2. 如果从命令行使用，先安装本仓库，再创建并查看 run：

   ```bash
   python -m pip install -e .
   allinluna start --goal "Add a tested health-check endpoint"
   allinluna status RUN_ID
   ```

3. 查看 `next-actions`，让宿主执行它明确要求的动作；随后运行 `allinluna drive RUN_ID` 继续流程。

你无需预先编写 TaskGraph、选择调度器，或指定模型。

## 一个真实的使用方式

“为服务增加经过测试的 health-check endpoint”是一个普通的软件交付目标。默认 `delivery` Pack 会把它编译为可追踪任务，直到 endpoint、目标测试和改动证据可被检查。先用 `allinluna start --goal "Add a tested health-check endpoint"` 创建 run，再用 `status` 和 `next-actions` 查看真实状态和下一步；编译或预览本身不表示交付完成。完整示例见[纯目标示例](docs/examples/plain-goal.md)。

## 默认资源行为

All in Luna 保持供应商中立：不指定模型时，资源依次由用户显式请求、Task/WorkUnit 覆盖、用户偏好、Pack 能力、部署/宿主以及当前会话默认值决定。它分别保存 `requested`、`resolved` 和宿主实际回传的 `actual`；没有遥测时，`actual` 保持未解析，而不会编造回退模型或执行记录。

## Workflow Packs

- `delivery`：默认的软件交付路径。
- `gsd`：当你明确需要 clarify → specify → decompose → implement → verify → integrate 工作流时使用。
- `research-routes-bridge`：保留 Claims、Evidence、未知项、矛盾与实验授权的研究路线；它不会把研究材料自动当成实施授权。

## 安装

在 Codex 中安装 `plugins/allinluna/` 即可从对话开始。开发或自动化环境可使用：

```bash
python -m pip install -e .
allinluna --help
```

公开 Skill 位于 `plugins/allinluna/skills/allinluna/SKILL.md`；注册表仅用于发现它，不是普通用户必须经过的入口。

## 权限与安全边界

All in Luna 只在动作真正到达时请求权限。凭据、push、部署、发布、破坏性操作和外部 live mutation 默认不会发生；它们需要明确授权。宿主动作的观察证据也不会被猜测：关于 `identity`、`create`、`read`、`wait`、`cancel` 与 `idempotency` 的信息，以及 `requested`、`resolved`、`actual` 资源层，必须来自相应的真实记录。

## 文档地图

- [快速开始](docs/user/quickstart.md)：插件与 CLI 的日常入口。
- [输入与旅程](docs/user/input-and-journeys.md)：目标、计划、run 和 Research Routes 输入如何处理。
- [纯目标示例](docs/examples/plain-goal.md)：一个可复制的 API/CLI 示例。
- [排障](docs/troubleshooting/common-issues.md)：relay、资源、项目解析与恢复问题。
- [公开表面与证据边界](docs/architecture/public-surface.md)：面向需要追溯性的架构说明。
- [RC2 技术契约](docs/architecture/v2-rc2/)：Store、receipt、CLI 和一致性诊断等开发者细节。

## RC 状态

All in Luna `2.0.0-rc.2` 是发布候选版本，当前 PR #2 保持 Draft，尚未是稳定发布。请把它用于评估和集成验证；只有远端 CI、完整运行时旅程、分发验证和真实宿主 canary 都通过后，才会具备转为 Ready 的资格。

Apache License 2.0，详见 [LICENSE](LICENSE)。
