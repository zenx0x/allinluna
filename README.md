# All in Luna × Research Routes

[简体中文（默认）](README.md) | [English](README.en.md)

**一句话结果：同一套核心现在可构建两个可独立安装、可同时安装的 Codex 发行版：All in Luna 负责开发并发，Research Routes 负责多路线证据与可逆探索。**

快速看图/GIF：把快速演示放在 [`docs/media/`](docs/media/)，当前仓库用 [`docs/media/README.md`](docs/media/README.md) 说明素材位置。

## 三个入口

1. **All in Luna**：使用 `$allinluna-plan` 规划完整开发，再用 `$allinluna-run` 通过 Coordinator、Owners、worktree、恢复、集成和验收推进交付。
2. **Research Routes**：使用 `$research-routes` 建立路线中立的 terrain map，分离 Claims/Evidence，比较路线、未知和失败区间，选择可逆探针。它不是跳转页。
3. **本地开发/发行**：在本仓库运行确定性构建器，得到两个包并运行 parity 与共存校验；不会创建或发布 GitHub 仓库。

## 最短安装与使用

```powershell
# 从源码构建两个发行版
python scripts/build_distributions.py --output dist
python scripts/validate_distributions.py
python scripts/validate_installations.py
```

将 `dist/all-in-luna` 和 `dist/research-routes` 分别作为本地 Codex 插件来源安装；两者使用不同的插件名 `allinluna` 与 `research-routes`，可以共存。已有 All in Luna 用户仍可按旧入口使用 `plugins/allinluna`。

## 什么时候用哪一个

| 需要 | 入口 | 产物边界 |
| --- | --- | --- |
| 把已授权工作完整做完 | All in Luna | 计划、并发负责人、实现、恢复、集成、验收 |
| 还在比较多条研究路线 | Research Routes | Claims、Evidence、矛盾、失败区间、未知、可逆探针 |
| 让已批准计划直接并发执行 | All in Luna `parallel-only` | 保留原方向，只规范依赖、所有权和执行 |

Research Routes 的 terrain map 不是实验授权、实现顺序、HumanDecision 或 canonical-state promotion；只有用户明确授权后，才把有边界的路线证据交给 All in Luna。

## 默认执行拓扑（重要）

All in Luna 的 Sponsor 对话会创建独立 Coordinator，再由 Coordinator 派发侧边栏可见的顶层 Codex 任务；需要时使用子协调、CounterPilot、worktree 和恢复。这里的开发并发不等于 Research Routes 的多路线证据比较。

## 真实使用案例

- 新功能：先用 All in Luna 生成完整依赖计划，按风险拆成侧边栏可见的顶层任务，最后回到集成和验收。
- 已有计划：用 `parallel-only` 执行，不重新设计产品方向。
- 科研探索：Research Routes 同时保留路线 A/B、正负证据和失败 regime，只安排可回滚的下一次 probe，不把地图伪装成结论。

## 共享核心与双发行版

两个包来自一个源仓库，并共享 core、schema、control-plane、resources、recovery、router、tests 和 evals。品牌、README、默认入口、skill metadata、cases、topics、social 文案是显式 overlay；构建器会记录源 commit/tree/parent/ref，校验器会拒绝共享文件漂移或 overlay 越界。

## 高级文档与开发检查

- 计划格式与资源策略：`plugins/allinluna/skills/allinluna-plan/references/`、`plugins/allinluna/skills/allinluna-run/references/`
- 双发行版契约：`distributions/distribution-manifest.json`
- 构建、parity、来源追溯：`scripts/build_distributions.py`、`scripts/validate_distributions.py`
- 安装共存验证：`scripts/validate_installations.py`
- 完整检查：`python -m unittest discover -s tests -v` 与 `python scripts/validate_repository.py`

## 资源模式

`premium`、`balanced`、`economy`、`speed`、`fast`、`ultra-fast`、`all-luna`、`mad-luna` 和 `custom` 只改变资源分配与速度，不改变完成标准。请求值和实际值分别记录；不可用的模型或 telemetry 不会被静默伪造。

## 许可证

Apache License 2.0，详见 [`LICENSE`](LICENSE)。
