# daily_stock_analysis 屎山改造实施计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 先把 daily_stock_analysis 的入口、配置、分析、搜索、编排四大重负文件拆薄，建立可测试、可回归、可继续演进的代码骨架，同时不改动现有业务输出语义。

**Architecture:** 这次只做“瘦身 + 分层 + 契约收口”，不做大规模业务重写。核心思路是：`main.py` 只保留启动与参数解析，`src/config.py` 收口配置与校验，`src/analyzer.py` 拆出 prompt/解析/规则，`src/search_service.py` 拆出 provider/fallback/归一化，`src/core/pipeline.py` 只做编排和异常边界。所有行为变更都先用测试锁住，再逐步迁移实现，避免在重构过程中把生产链路打穿。

**Tech Stack:** Python、pytest、现有项目测试体系、现有日志/配置体系、轻量模块拆分。

---

## 0. 改造边界与原则

### 不做的事
- 不改 `AnalysisResult` 的核心 schema，避免前后端和历史链路大面积回归
- 不重写数据源协议，不替换现有 provider
- 不引入新框架，不上新的抽象工厂/插件系统作为第一步
- 不做“顺手优化式”大重构，所有改动都必须有回归测试护航

### 统一原则
- 入口薄、规则纯、适配器外置、编排单一职责
- 先补测试再拆代码
- 每个任务都必须有可执行验证命令
- 每个任务完成后都跑局部测试，最后再跑组合回归

---

## Task 1: 建立重构基线，锁住当前行为契约

**Objective:** 先把当前最容易被拆坏的主流程行为锁住，确保后续拆分不改变核心输出。

**Files:**
- Modify: `tests/test_config_manager.py`
- Modify: `tests/test_config_env_compat.py`
- Modify: `tests/test_analysis_api_contract.py`
- Modify: `tests/test_analysis_portfolio_context.py`
- Modify: `tests/test_analyzer_ma60_prompt.py`（若已有则补强断言）
- Modify: `tests/test_analyzer_ma60_fallback.py`（若已有则补强断言）

**Step 1: Write failing/strengthened tests**

补三类基线断言：
1. `main.py` 入口不会改变现有 CLI 参数语义
2. 配置读取仍保留当前 `.env` / 环境变量优先级
3. `Analyzer` 的关键输出字段、`pipeline` 的基本分析结果语义不变

建议新增/补强断言：
- `parse_arguments()` 仍能解析 `--dry-run` / `--stocks` / `--schedule` / `--market-review`
- `get_config()` / `setup_env()` 读取后关键字段不变
- `AnalysisResult` 的核心字段仍能被正常构造和序列化

**Step 2: Run tests to verify current baseline**

Run:
```bash
python -m pytest tests/test_config_manager.py tests/test_config_env_compat.py tests/test_analysis_api_contract.py tests/test_analysis_portfolio_context.py -q
```

Expected: 先确认当前行为可观测；若存在失败，先记录为基线，不在本任务修复。

**Step 3: Minimal implementation**

本任务只补测试，不改主逻辑。

**Step 4: Run tests to verify pass**

Run:
```bash
python -m pytest tests/test_config_manager.py tests/test_config_env_compat.py tests/test_analysis_api_contract.py tests/test_analysis_portfolio_context.py -q
```

Expected: 现有测试全部通过，新增断言能稳定运行。

---

## Task 2: 把 `main.py` 瘦到只剩启动编排

**Objective:** 把环境 bootstrap、代理设置、运行模式判断、调度入口从 `main.py` 中剥离，降低入口复杂度。

**Files:**
- Modify: `main.py`
- Create: `src/bootstrap/runtime_env.py`
- Create: `src/bootstrap/runtime_logging.py`
- Modify: `tests/test_main_cli.py`（新建）
- Modify: `tests/test_config_env_compat.py`（若需补运行时环境断言）

**Step 1: Write failing test**

新增测试，验证以下事实：
- 入口模块只负责参数解析和调用启动函数
- `setup_env()` / proxy 注入不会散落在多个分支里
- `StockAnalysisPipeline` 仍能被外部导入，但不需要在模块导入时完成沉重初始化

建议测试点：
- `parse_arguments()` 的默认值和已知参数仍然正确
- `main.py` 导入时不触发实际分析执行
- `main()` 能按参数进入对应运行模式

**Step 2: Run test to verify failure**

Run:
```bash
python -m pytest tests/test_main_cli.py -q
```

Expected: 先失败或不完整，提示 `main.py` 仍太重。

**Step 3: Write minimal implementation**

将以下内容从 `main.py` 拆出去：
- 环境加载与 `.env` 刷新逻辑 → `src/bootstrap/runtime_env.py`
- 代理环境注入 → `src/bootstrap/runtime_env.py`
- 早期日志配置 → `src/bootstrap/runtime_logging.py`

`main.py` 保留：
- 参数解析
- 运行模式分发
- pipeline 启动
- 最外层异常处理

建议新增 helper：
- `bootstrap_runtime_environment()`
- `setup_bootstrap_logging(debug: bool)`
- `reload_env_file_values_preserving_overrides()`

**Step 4: Run test to verify pass**

Run:
```bash
python -m pytest tests/test_main_cli.py tests/test_config_env_compat.py -q
python -m py_compile main.py src/bootstrap/runtime_env.py src/bootstrap/runtime_logging.py
```

Expected: 测试通过，入口文件明显变薄。

---

## Task 3: 收口 `src/config.py` 的解析、默认值与校验

**Objective:** 把配置解析、环境变量标准化、模型路由、校验分层，避免配置规则继续向外扩散。

**Files:**
- Modify: `src/config.py`
- Create: `src/config/parsers.py`
- Create: `src/config/validation.py`
- Create: `src/config/runtime.py`
- Modify: `tests/test_config_manager.py`
- Modify: `tests/test_config_env_compat.py`

**Step 1: Write failing test**

补测试覆盖以下点：
- `parse_env_bool` / `parse_env_int` / `parse_env_float` 的行为保持不变
- LLM channel 归一化逻辑对 `openai` / `anthropic` / `ollama` 仍然兼容
- `NEWS_STRATEGY_PROFILE` / `NEWS_MAX_AGE_DAYS` 的最终值计算不漂移

**Step 2: Run test to verify failure**

Run:
```bash
python -m pytest tests/test_config_manager.py tests/test_config_env_compat.py -q
```

Expected: 若拆分后调用路径不完整，测试会暴露出来。

**Step 3: Write minimal implementation**

把 `src/config.py` 分成三个层次：
- `parsers.py`：纯解析函数
- `validation.py`：配置校验与 issue 收集
- `runtime.py`：读取 `.env`、环境变量和运行时覆盖

`src/config.py` 继续作为兼容导出层，避免全仓库改 import。

**Step 4: Run test to verify pass**

Run:
```bash
python -m pytest tests/test_config_manager.py tests/test_config_env_compat.py -q
python -m py_compile src/config.py src/config/parsers.py src/config/validation.py src/config/runtime.py
```

Expected: 配置行为不变，但实现更清晰。

---

## Task 4: 把 `src/analyzer.py` 拆成“提示词 / 解析 / 规则”三段

**Objective:** 降低 `analyzer.py` 的职责密度，让 prompt 调整、LLM 解析、结果修补互不污染。

**Files:**
- Modify: `src/analyzer.py`
- Create: `src/analyzer/prompt_builder.py`
- Create: `src/analyzer/response_parser.py`
- Create: `src/analyzer/result_rules.py`
- Create: `src/analyzer/chip_rules.py`
- Create: `src/analyzer/institution_rules.py`
- Modify: `tests/test_analyzer_ma60_prompt.py`
- Modify: `tests/test_analyzer_ma60_fallback.py`
- Modify: `tests/test_report_schema.py`（如需补序列化/字段兼容）

**Step 1: Write failing test**

补三组断言：
1. Prompt builder 能单独生成系统提示词，且含有 MA60 / fundamentals-first 规则
2. 响应解析器能独立处理 JSON 修复、scratchpad、截断预览
3. 结果规则函数能独立补 `ma_analysis` / chip / institution 结构

**Step 2: Run test to verify failure**

Run:
```bash
python -m pytest tests/test_analyzer_ma60_prompt.py tests/test_analyzer_ma60_fallback.py tests/test_report_schema.py -q
```

Expected: 先露出拆分前的耦合点。

**Step 3: Write minimal implementation**

拆分建议：
- `prompt_builder.py`：拼接系统 prompt、策略规则、风控约束
- `response_parser.py`：JSON 修复、LLM 结果预览、安全截断
- `result_rules.py`：`check_content_integrity` / placeholder fill / result normalization
- `chip_rules.py`：筹码结构填充与健康度判断
- `institution_rules.py`：机构结构填充与摘要生成

`src/analyzer.py` 保留 `Analyzer` / `GeminiAnalyzer` 兼容入口，逐步把内部 helper 迁移出去。

**Step 4: Run test to verify pass**

Run:
```bash
python -m pytest tests/test_analyzer_ma60_prompt.py tests/test_analyzer_ma60_fallback.py tests/test_report_schema.py -q
python -m py_compile src/analyzer.py src/analyzer/*.py
```

Expected: 行为不变，文件职责明显下降。

---

## Task 5: 拆 `src/search_service.py` 的 provider 层和 fallback 层

**Objective:** 把多搜索源 provider、结果归一化、时间过滤、fallback 策略拆开，避免一个文件承担所有搜索逻辑。

**Files:**
- Modify: `src/search_service.py`
- Create: `src/search_service/providers/base.py`
- Create: `src/search_service/providers/tavily.py`
- Create: `src/search_service/providers/serpapi.py`
- Create: `src/search_service/providers/bocha.py`
- Create: `src/search_service/providers/brave.py`
- Create: `src/search_service/providers/searxng.py`
- Create: `src/search_service/fallback_policy.py`
- Create: `src/search_service/normalization.py`
- Create: `src/search_service/time_filter.py`
- Modify: `tests/test_search_service.py`
- Modify: `tests/test_news_search_diagnosis.py`（如已有则补路径断言）

**Step 1: Write failing test**

新增测试覆盖：
- provider 返回结构统一
- fallback 顺序不乱
- 时间窗口过滤仍按原语义工作
- `SearchService.is_available` 与真实能力一致，不只看某一个 provider

**Step 2: Run test to verify failure**

Run:
```bash
python -m pytest tests/test_search_service.py -q
```

Expected: 拆分前后接口变化会被暴露。

**Step 3: Write minimal implementation**

拆分方式：
- provider 类只做“调用外部搜索服务”
- normalization 只做“统一成 SearchResult / SearchResponse”
- fallback policy 只做“失败后换谁、什么时候停”
- time_filter 只做“发布时间/发布时间戳/时效窗口判断”

**Step 4: Run test to verify pass**

Run:
```bash
python -m pytest tests/test_search_service.py tests/test_news_search_diagnosis.py -q
python -m py_compile src/search_service.py src/search_service/**/*.py
```

Expected: 搜索逻辑可读性显著提升，回退策略更容易排查。

---

## Task 6: 把 `src/core/pipeline.py` 收敛成纯编排器

**Objective:** 让 pipeline 只负责串联任务、传递上下文、汇总结果，不再承担过多业务修补逻辑。

**Files:**
- Modify: `src/core/pipeline.py`
- Create: `src/core/context_builder.py`
- Create: `src/core/result_orchestrator.py`
- Create: `src/core/analysis_session.py`
- Modify: `tests/test_pipeline_flow.py`
- Modify: `tests/test_analysis_api_contract.py`
- Modify: `tests/test_analysis_portfolio_context.py`

**Step 1: Write failing test**

补测试断言：
- pipeline 仍能完成一只股票的完整分析流程
- 持仓上下文、市场上下文、基本面上下文在进入 analyzer 前可被独立构造
- pipeline 不直接依赖大量散落 helper 逻辑

**Step 2: Run test to verify failure**

Run:
```bash
python -m pytest tests/test_pipeline_flow.py tests/test_analysis_api_contract.py -q
```

**Step 3: Write minimal implementation**

将 pipeline 内部逻辑切分：
- `context_builder.py`：组装股票/市场/持仓/新闻/基本面上下文
- `analysis_session.py`：封装单次分析生命周期
- `result_orchestrator.py`：处理分析结果落库、通知、后处理

`StockAnalysisPipeline` 只保留顶层编排。

**Step 4: Run test to verify pass**

Run:
```bash
python -m pytest tests/test_pipeline_flow.py tests/test_analysis_api_contract.py tests/test_analysis_portfolio_context.py -q
python -m py_compile src/core/pipeline.py src/core/*.py
```

Expected: 主流程变薄，逻辑边界更清楚。

---

## Task 7: 收口通知、历史与报告语言层的展示职责

**Objective:** 让展示层只做文本与格式化，不再暗含业务修补或决策逻辑。

**Files:**
- Modify: `src/report_language.py`
- Modify: `src/notification.py`
- Modify: `src/services/history_service.py`
- Modify: `tests/test_notification.py`
- Modify: `tests/test_analysis_history.py`
- Modify: `tests/test_report_renderer.py`

**Step 1: Write failing test**

补测试确认：
- 操作建议、决策类型、仓位语义的显示口径一致
- 历史记录和通知展示不再改写业务结论
- 占位文本、语言归一化仍能正常工作

**Step 2: Run test to verify failure**

Run:
```bash
python -m pytest tests/test_notification.py tests/test_analysis_history.py tests/test_report_renderer.py -q
```

**Step 3: Write minimal implementation**

把业务判断从展示层剥离，只保留：
- 文案本地化
- markdown / plain text 渲染
- 兼容旧字段
- 缺失字段占位

**Step 4: Run test to verify pass**

Run:
```bash
python -m pytest tests/test_notification.py tests/test_analysis_history.py tests/test_report_renderer.py -q
```

Expected: 展示层稳定，且不再掺杂决策逻辑。

---

## Task 8: 清理导入链和兼容出口，避免循环依赖

**Objective:** 保证拆分后模块之间导入链稳定，旧路径仍可用，但内部实现逐步切换到新模块。

**Files:**
- Modify: `src/analyzer.py`
- Modify: `src/config.py`
- Modify: `src/search_service.py`
- Modify: `src/core/pipeline.py`
- Create: `tests/test_import_smoke.py`

**Step 1: Write failing test**

新增导入烟雾测试，覆盖：
- `import main`
- `import src.analyzer`
- `import src.config`
- `import src.search_service`
- `import src.core.pipeline`

**Step 2: Run test to verify failure**

Run:
```bash
python -m pytest tests/test_import_smoke.py -q
```

**Step 3: Write minimal implementation**

修正：
- 任何新模块都尽量只向下依赖，不要反向 import 顶层入口
- 兼容出口只放在少数顶层模块
- 避免 `main.py` 在 import 时做重型初始化

**Step 4: Run test to verify pass**

Run:
```bash
python -m pytest tests/test_import_smoke.py -q
python -m py_compile main.py src/**/*.py
```

Expected: 导入链干净，循环依赖风险下降。

---

## Final verification: 全链路回归

当所有任务完成后，必须做一次组合验证。

Run:
```bash
python -m pytest \
  tests/test_config_manager.py \
  tests/test_config_env_compat.py \
  tests/test_main_cli.py \
  tests/test_import_smoke.py \
  tests/test_search_service.py \
  tests/test_pipeline_flow.py \
  tests/test_analysis_api_contract.py \
  tests/test_analysis_portfolio_context.py \
  tests/test_notification.py \
  tests/test_analysis_history.py \
  tests/test_report_renderer.py \
  tests/test_analyzer_ma60_prompt.py \
  tests/test_analyzer_ma60_fallback.py \
  -q
```

以及：
```bash
python -m py_compile main.py src/config.py src/analyzer.py src/search_service.py src/core/pipeline.py
```

Expected:
- 关键测试全过
- 无新增导入错误
- 入口文件明显变薄
- 核心规则逻辑已下沉到独立模块

---

## 推荐实施顺序

如果只想先做最值钱的三步，按这个顺序开干：

1. Task 2：瘦 `main.py`
2. Task 4：拆 `src/analyzer.py`
3. Task 5：拆 `src/search_service.py`

这三步最能快速降低屎山浓度，同时不太容易打穿业务结果。

---

## 风险点

- `main.py` 和 `src/config.py` 改动最容易引入初始化顺序问题，必须先跑导入烟雾测试
- `src/analyzer.py` 里有不少结果修补逻辑，拆的时候不要误伤 placeholder 语义
- `src/search_service.py` 涉及 provider/fallback/time filter，任何一点顺序变化都可能让新闻条数变化
- `src/core/pipeline.py` 是主编排器，别一次性把所有 helper 挪走；先抽 context，再抽 orchestrator
- 若测试本来就有不稳定项，必须先标记为基线，不要和重构回归混在一起

---

## 验收标准

- 入口文件 `main.py` 明显变薄，核心逻辑不再堆叠在启动层
- `src/config.py` 的解析/校验/运行时入口清晰分层
- `src/analyzer.py` 不再是“prompt + 解析 + 规则 + 修补”的大杂烩
- `src/search_service.py` 的 provider、归一化、fallback、时效判断拆开
- `src/core/pipeline.py` 只做编排，不再承担过多规则修补
- 关键测试覆盖拆分前后的兼容行为
- 现有业务语义不变，输出不出现大面积漂移

