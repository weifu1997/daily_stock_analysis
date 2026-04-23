# Issue 4: 补全 public API 类型注解 + CI mypy

## 背景
当前项目类型注解覆盖不均：
- `api/` 端点：部分缺失返回值类型、依赖项类型
- `src/services/`：大量 `Dict[str, Any]` 和 `Optional` 未标注
- `src/repositories/`：基本无类型注解
- 这导致无法开启 mypy/pyright CI 检查

## 目标
1. 从 `api/` 和 `src/services/` 开始，渐进补全类型注解
2. 引入 mypy 配置，先设 `warn_return_any = True` 软约束
3. CI 中增加 mypy 检查步骤，但允许失败（不阻塞合并）
4. 逐步硬化到 `strict = True`

## 具体任务清单

### Phase 1: 基础设施
- [ ] 创建 `mypy.ini` 或 `pyproject.toml` [tool.mypy] 配置
  - `python_version = 3.11`
  - `warn_return_any = True`
  - `ignore_missing_imports = True`（外部库）
  - `exclude = data_provider/|tests/|bot/`
- [ ] 安装 `mypy` 到 dev dependencies
- [ ] 本地运行基线扫描，记录当前错误数量

### Phase 2: 核心模块类型注解
- [ ] `api/v1/endpoints/*.py` — 所有端点函数补全参数类型和返回类型
- [ ] `api/v1/schemas/*.py` — Pydantic 模型已自动类型化，检查是否有字段缺失类型
- [ ] `src/services/*.py` — 补全 Service 类公共方法的签名
  - `portfolio_service.py`
  - `history_service.py`
  - `backtest_service.py`
  - `system_config_service.py`
  - `notification.py`
- [ ] `src/repositories/*.py` — 补全 Repository 层

### Phase 3: 工具函数类型注解
- [ ] `src/utils/*.py`
- [ ] `src/report_*.py`
- [ ] `src/storage.py` 中 DatabaseManager 的公共方法

### Phase 4: CI 集成
- [ ] `.github/workflows/ci.yml` 增加 mypy 步骤
  - `mypy src/ api/ --ignore-missing-imports`
  - 设 `continue-on-error: true`（初期不阻塞）
- [ ] 在 README 或 CONTRIBUTING 中说明类型注解规范

### Phase 5: 硬化
- [ ] 当错误数降到 0 时，移除 `continue-on-error`
- [ ] 开启 `strict = True` 中部分规则（如 `check_untyped_defs`）

## 验收标准
- [ ] `mypy src/ api/` 通过（0 errors）
- [ ] CI 中 mypy 步骤正常运行
- [ ] 无运行时回归

## 预估工时
12-16 小时（渐进式，可分多个 PR）

## 优先级
**P3**（工程债务，不影响业务功能）
