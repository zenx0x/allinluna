# All in Luna

[English](README.en.md)

All in Luna 是一个分层执行运行时：把用户目标编译为全局 Task Graph，由 Global Coordinator 释放 Task Lanes；每个 Lane 递归调度有边界的 WorkUnits，并以 typed contract、artifact、receipt 和 handoff 持续推进到结果。

## 用户入口

只有一个公开 Skill：`plugins/allinluna/skills/allinluna/SKILL.md`。用户可以直接提供：

- idea / 一句话目标；
- existing plan；
- active run；
- Research Routes route packet。

Skill 会编译 `RunIntent` 与 `TaskContracts`，选择 Workflow Pack，调用 vNext runtime/CLI；用户不需要先学习内部 schema 或调度状态。

## 执行模型

```text
Conversation → Global Coordinator → Task Lanes → WorkUnits → tools/skills/plugins/MCP
```

Coordinator 只维护跨 Lane 依赖、contract、资源与完成状态。Lane 拥有局部 WorkGraph、local scheduler、context slice、subagent receipts、局部综合和 handoff。子 WorkUnit 的 scope、authority、ownership、resource 只能收窄；跨 Lane 工作走 promotion request。

## Workflow Packs

- `delivery`：真实软件交付编译器，支持可配置 TaskGraph templates、contract expansion、done_when、handoff、promotion 与资源默认值。
- `gsd`：可执行的 clarify → specify → decompose → implement → verify → integrate；支持动态 expansion、bounded lanes/work units 和失败恢复。
- `research-routes-bridge`：路线中立地把 Claims、Evidence、unknowns、矛盾、failure regimes、HumanDecision 和 experiment authorization 编译为 RunIntent/TaskContracts；不会把研究输入伪装成实现授权或 canonical state。

Pack 只能经公开 Core API 访问 Store/Context/Artifact/Host；manifest、entrypoint、capability、permission 和版本兼容性会在 registry loader 中验证。

## 资源与权限

模型与推理等级由 Run 资源配置决定，并可由更窄的 Task/WorkUnit 配置覆盖；不再硬锁 Luna-high。可按宿主能力选择 Luna 的 medium/high/xhigh/max、Codex Spark 或其他模型。requested、resolved、actual 始终分开记录；actual host receipt 不可得时为 `unresolved`，不伪造成功或 fallback。

真实 host receipt 必须回传 `resource_receipt.requested/resolved/actual`、`actual_state`、`evidence_source` 与 `observed_at`。三组 model/reasoning 必须一致且匹配持久化 dispatch action；缺字段、时间戳无效、找不到 action 基线或任一值不匹配时均保持 `unresolved`。`runtime.db` schema v5 将三组资源值分别持久化，支持 crash recovery、replay 与 status 查询。

权限在动作边界 JIT 请求：credentials、push、deploy、publish、destructive work、live external mutation 默认不发生，只有到达动作并获得明确授权后才可继续。

## CLI、状态与恢复

```text
allinluna start --goal "..."
allinluna status RUN_ID
allinluna next-actions RUN_ID
allinluna ingest-receipt RUN_ID RECEIPT.json
allinluna pause RUN_ID
allinluna resume RUN_ID
allinluna retry RUN_ID --task TASK_ID
allinluna cancel RUN_ID --task TASK_ID
allinluna reconcile RUN_ID
allinluna set-policy RUN_ID POLICY.json
```

runtime CLI 还提供 `set-policy`。legacy plan/run import 通过下方 read-only API 完成；恢复依据 SQLite state/journal、真实 host receipt、lease、Git/workspace identity 和 snapshot validity 重算 ready actions；不可恢复的问题返回 blocker，并保留 immutable artifacts。

## Legacy import

`LegacyPlanImportAPI`、`LegacyRunStateImportAPI`、`LegacyResourceTranslator` 都是 read-only parse/validate/translate API：旧 plan/run-state 不回写，resource profiles 转成 `ResourceEnvelope`，loss、unknown、warnings 和 model evidence 都显式返回。没有 actual receipt 时 model evidence 保持 unresolved。

## 安装与示例

在 Codex Plugins 中选择 `plugins/allinluna/`。Python 入口示例：

```python
from allinluna_runtime.packs import SinglePublicSkillAPI

compiled = SinglePublicSkillAPI().compile({
    "goal": "Implement the requested software outcome",
    "done_when": ["tests and changed-path evidence are available"],
})
print(compiled.task_graph.to_dict())
```

Apache License 2.0，详见 `LICENSE`。
