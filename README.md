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

`TaskGraph` 是编译图与图校验的唯一权威；`CompiledRunGraph` 仅保留为 Pack 的稳定导入名。SQLite `Store` 只持有连接、迁移和事务边界，实体仓储、资源 claim、host dispatch/receipt、跨实体服务、观测与调度读模型分别由独立运行时模块承担，避免将领域职责重新聚回根 Store。

## Workflow Packs

- `delivery`：真实软件交付编译器，支持可配置 TaskGraph templates、contract expansion、done_when、handoff、promotion 与资源默认值。
- `gsd`：可执行的 clarify → specify → decompose → implement → verify → integrate；支持动态 expansion、bounded lanes/work units 和失败恢复。
- `research-routes-bridge`：路线中立地把 Claims、Evidence、unknowns、矛盾、failure regimes、HumanDecision 和 experiment authorization 编译为 RunIntent/TaskContracts；不会把研究输入伪装成实现授权或 canonical state。

Pack 只能经公开 Core API 访问 Store/Context/Artifact/Host；manifest、entrypoint、capability、permission 和版本兼容性会在 registry loader 中验证。

## 资源与权限

模型与推理等级由 Run 资源配置决定，并可由更窄的 Task/WorkUnit 配置覆盖；不再硬锁 Luna-high。可按宿主能力选择 Luna 的 medium/high/xhigh/max、Codex Spark 或其他模型。requested、resolved、actual 始终分开记录：requested 与 resolved 描述路由；actual 只在宿主明确回传时记录，绝不从路由或任务正文推断。

宿主资源路由遥测是可选的 adapter diagnostics。只有明确的 `actual host receipt` 能填充 actual；缺少模型、推理或 reroute 遥测时，`actual` 保持 `null`、`actual_state` 保持 `unresolved`；普通执行、handoff 和结果完成仍可继续。宿主提供 actual 证据时，adapter 会将 requested 与持久化的 dispatch action 比对，并要求 actual 与报告的 resolved 路由一致。`runtime.db` schema v5 分别持久化三组资源值（包含 unresolved actual state），支持 crash recovery、replay 与 status 查询。

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
Host-side conformance 会同时校验 `requested`、`resolved`、`actual` 与 host `identity`，并检查 `create`、`read`、`wait`、`cancel`、`idempotency` 的完整性；缺失迹象会返回 `BLOCKED`。

Evidence checks 使用受超时约束的受控命令执行并将 stdout、stderr、超时和执行错误作为证据保存；任意 Python callable 不能被安全强制终止，因此不会作为 check runner 执行。

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
