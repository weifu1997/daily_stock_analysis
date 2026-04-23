# Issue 6: CI 引入 mypy / pyright

## 背景
Issue 4 完成后，需要把类型检查集成到 CI 中，防止新代码引入类型退化。

## 目标
1. `.github/workflows/ci.yml` 增加 mypy 步骤
2. 可选：增加 pyright 步骤（更严格，但较慢）
3. 初期允许失败，逐步硬化

## 具体任务清单
- [ ] 等待 Issue 4 完成（类型注解补全）
- [ ] 修改 `.github/workflows/ci.yml`
- [ ] 本地验证 `act` 或通过 PR 触发测试

## 依赖
- Issue 4: 补全 public API 类型注解

## 预估工时
2-4 小时

## 优先级
**P3**（依赖 Issue 4）
