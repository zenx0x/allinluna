# 贡献指南

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

我们欢迎能够改善真实用户流程、确定性状态行为、平台兼容性或评测覆盖的贡献。

1. 创建一条范围明确的分支。
2. 保持每个 `SKILL.md` 简洁，将详细合同放入同一 Skill 下一级 `references/` 文件。
3. 每次修改状态、资源策略或计划验证行为时，都要新增或更新测试。
4. 运行 `python -m unittest discover -s tests -v` 和 `python scripts/validate_repository.py`。
5. 在 Pull Request 中说明行为影响、验证结果和兼容性限制。

禁止添加隐藏网络请求、遥测、隐式外部写入、硬编码的私有模型名称，或会静默削弱用户范围的指令。
