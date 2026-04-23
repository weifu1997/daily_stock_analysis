# Repository Collaboration Skills

本目录存放仓库级 AI 协作 skill，统一用于 issue 分析、PR 审查和 issue 修复。

## 约定
- 规则真源：`AGENTS.md`
- skill 内容必须与 `AGENTS.md`、`.github/copilot-instructions.md` 保持一致
- 分析产物默认输出到 `.claude/reviews/`
- skill 只负责协作方法，不替代仓库规范

## Skill 一览
- `analyze-issue`：分析 issue，给出根因、影响和建议
- `analyze-pr`：审查 PR，给出正确性、风险与验证意见
- `fix-issue`：按最小修改原则定位并修复问题

## 通用输出风格
- 先结论，后证据
- 先风险，后建议
- 结论要可执行，避免空泛描述
- 与仓库现有流程和测试约定保持一致
