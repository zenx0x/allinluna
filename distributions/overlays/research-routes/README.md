# Research Routes

**一句话结果：把多个科研路线放在同一张证据地图上，保留 Claims、Evidence、矛盾、失败区间和未知，并只选择可逆的下一步探索。**

Research Routes 是一个独立的 Codex 研究发行版，不是跳转页，也不把研究地图伪装成实现计划。它与 All in Luna 共用核心 schema、control plane、资源、恢复、路由和测试；All in Luna 负责开发并发，Research Routes 负责多路线证据与可逆探索。

## 三个入口

1. **`$research-routes-plan`**：定义问题边界、候选路线、Claims/Evidence 结构和未知。
2. **`$research-routes-explore`**：比较路线、保留正负/矛盾证据、识别失败 regime，并设计可逆 probe。
3. **`$research-routes-run`**：在明确授权的研究范围内执行探针、记录结果并保持回滚边界；准备转入产品实现时，再把有边界的证据包交给 All in Luna。

## 最短安装与使用

```powershell
python scripts/build_distributions.py --output dist
python scripts/validate_distributions.py
python scripts/validate_installations.py
```

安装 `dist/research-routes` 作为本地 Codex 插件；它使用独立的 `research-routes` 名称，可与 `allinluna` 同时安装。

## 科研边界

terrain map 不是 experiment authorization、implementation order、HumanDecision 或 canonical-state promotion。每个 Claim 应引用 Evidence；Evidence 保留 polarity；下一步 probe 必须显式 `reversible: true`。共享运行时校验器会对越界授权 fail closed。

## 适合的案例

- 比较两条科学路线而不提前选定路线。
- 保留相互矛盾的结果和失败区间，避免只留下正向结论。
- 在实现前选择成本可控、可回滚、能区分假设的下一次 probe。

高级契约见 `plugins/research-routes/skills/`、`distributions/distribution-manifest.json` 和 `scripts/validate_route_packet.py`。
