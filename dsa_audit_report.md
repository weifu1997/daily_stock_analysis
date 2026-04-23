# daily_stock_analysis 整库代码审计报告

**审计对象**：`daily_stock_analysis`（A股自选股智能分析系统）
**审计时间**：2026-04-23
**HEAD**：`8e1e247`（与 `origin/main` 一致）
**工作区状态**：存在未提交修改
**审计范围**：后端 Python 代码全量（343 个文件，~113,000 行），不含前端（`apps/dsa-web/`、`apps/dsa-desktop/`）

---

## 1. 项目整体理解与主链路

### 1.1 一句话定位

基于 AI 大模型的 A 股自选股每日分析系统：自动抓取行情/筹码/新闻/基本面数据，生成带买卖点位和风控检查清单的决策仪表盘，推送到飞书/企业微信/邮件等多渠道。

### 1.2 核心链路

```
[候选池构建] ──▶ [数据获取] ──▶ [多维度分析] ──▶ [LLM决策] ──▶ [归一化/风控] ──▶ [报告生成] ──▶ [通知推送]
     │                │               │               │               │               │              │
  STOCK_LIST    DataFetcher      技术面/筹码     GeminiAnalyzer   normalization   Jinja2模板    NotificationService
  mx-xuangu     Manager          基本面/新闻     Agent路径        risk_penalty    Markdown      飞书/微信/邮件
```

### 1.3 主运行模式

| 模式 | 入口 | 说明 |
|------|------|------|
| CLI 单次分析 | `main.py` | `run_full_analysis()` 一次性跑完个股+大盘 |
| 定时任务 | `main.py --schedule` | 委托给 `src/runtime/main_dispatch.py` |
| API 服务 | `server.py` / `main.py --serve` | FastAPI，支持分析触发/历史查询/持仓管理/认证 |
| 仅服务 | `main.py --serve-only` | 不自动执行分析，只提供 API |
| Bot 接入 | `bot/dispatcher.py` | Discord/Telegram/钉钉/飞书 Stream |

### 1.4 模块边界图

```
main.py
├─ 参数解析 (argparse)
├─ 模式路由 → runtime/main_dispatch.py
│   ├─ run_full_analysis() → pipeline.run()
│   ├─ run_schedule_mode()
│   ├─ run_serve_only_mode() → uvicorn
│   └─ ...
└─ 全局异常捕获 + 退出码管理

src/core/pipeline.py (StockAnalysisPipeline)
├─ __init__: 初始化 fetcher/search/analyzer/notifier
├─ run(): 并发调度 ThreadPoolExecutor
├─ analyze_stock(): 单股完整链路 (~732 行)
│   ├─ Step 1: 实时行情 (fetcher_manager.get_realtime_quote)
│   ├─ Step 2: 日线数据 + 筹码分布
│   ├─ Step 2.5: 基本面聚合 (get_fundamental_context)
│   ├─ Step 3: 趋势分析 (StockTrendAnalyzer)
│   ├─ Step 4: 新闻搜索 (search_service.search_comprehensive_intel)
│   ├─ Step 5-7: 上下文整理 → LLM 分析 → 结果归一化
│   └─ Agent 分支: _analyze_with_agent()
└─ _enhance_context(): 数据增强与合并

src/analyzer.py (GeminiAnalyzer)
├─ analyze(): 组装 prompt → litellm 调用 → 解析 JSON
├─ _format_prompt(): 构造最终 prompt
├─ 解析: json_repair + 正则提取 JSON fence
└─ AnalysisResult dataclass

data_provider/
├─ base.py: DataFetcherManager (~2850 行)
│   ├─ get_daily_data(): 日线 fallback 链
│   ├─ get_realtime_quote(): 实时行情 fallback
│   ├─ get_chip_distribution(): 筹码分布 fallback
│   ├─ get_fundamental_context(): 基本面聚合 (~333 行)
│   └─ get_stock_name(): 名称解析 fallback
└─ akshare_fetcher.py / tushare_fetcher.py / efinance_fetcher.py

src/search_service.py (SearchService, ~3960 行)
├─ BaseSearchProvider → Tavily/Bocha/Brave/SerpAPI/MiniMax
├─ SearXNG 自建/公共实例支持
├─ search_comprehensive_intel(): 多维度情报搜索
├─ search_stock_news(): 新闻搜索主入口
└─ MX 路由: mx_search_adapter (若启用)

src/services/
├─ portfolio_service.py: 交易流水回放 → 持仓快照
├─ backtest_service.py: 历史分析准确率回测 (1日窗口)
├─ history_service.py: 分析历史查询/导出
├─ report_renderer.py: Jinja2 模板渲染
├─ system_config_service.py: .env 读写/导出/导入
└─ task_queue.py: 后台任务队列

src/notification.py (NotificationService, ~2017 行)
├─ generate_daily_report(): Markdown/WeChat/Brief 报告
├─ generate_dashboard_report(): 完整仪表盘报告
├─ send(): 多渠道并发发送
└─ 支持: 飞书/企业微信/邮件/Discord/Telegram/Slack/钉钉/Pushover

api/
├─ app.py: FastAPI 工厂, CORS, SPA fallback, 静态文件
├─ v1/endpoints/: analysis, auth, backtest, history, portfolio,
│                  system_config, stocks, usage, agent
├─ middlewares/auth.py: 可选认证中间件
└─ middlewares/error_handler.py: 统一异常封装

src/storage.py: SQLAlchemy + SQLite, 日线/新闻/历史/基本面表
src/config.py: 单例 Config dataclass, _load_from_env (~1536 行)
src/agent/: Agent 多轮对话、orchestrator、runner、skills
src/analysis/: 归一化、technical_factor_summary
bot/: 多平台机器人命令分发
```

---

## 2. 审核范围与阅读结论

| 区域 | 状态 | 说明 |
|------|------|------|
| `main.py` / `src/core/pipeline.py` | 精读 | 主链路、并发控制、异常传播 |
| `src/analyzer.py` / `src/agent/` | 精读 | Prompt、LLM 调用、结果解析 |
| `src/search_service.py` / `src/search/` | 精读 | 搜索路由、fallback、MX 集成 |
| `data_provider/` 全部 | 扫描+抽查 | 多数据源适配、fallback 链 |
| `src/services/` 全部 | 扫描+抽查 | 持仓、通知、报告、回测 |
| `api/` 全部 | 精读 | FastAPI 路由、认证、中间件 |
| `bot/` 全部 | 扫描 | 多平台机器人 |
| `src/storage.py` | 抽查 | SQLite 操作、事务 |
| `templates/` / `src/reports/` | 扫描 | Jinja2 模板、报告生成 |
| `tests/` | 扫描 | 130+ 测试文件，覆盖主要链路 |
| `apps/dsa-web/` / `apps/dsa-desktop/` | 排除 | 按用户要求不纳入 |
| CI / Docker / 脚本 | 扫描 | `.github/workflows/`, `docker/`, `scripts/` |

---

## 3. 高优先级问题清单

### P0（立即修）

| 编号 | 文件 | 行号 | 现象 | 根因 | 风险 | 修复建议 | 上下游影响 |
|------|------|------|------|------|------|----------|------------|
| P0-1 | `api/app.py` | 195-200 | SPA fallback `file_path = static_dir / full_path` 未做目录约束 | 路径穿越 | 任意文件读取 | 加 `resolve()` + `startswith` 目录校验 | 影响所有前端部署 |
| P0-2 | `main.py` | 24-88 | 标准库和项目模块被重复导入两遍 | 代码合并时未清理 | 符号冲突隐患、维护混乱 | 删除第 72-88 行重复导入块 | 无 |
| P0-3 | `src/search_service.py` | 509,761,1151,1257,1373,1384,1549,1560,1640,1819,1895,1913 | 大量 `except Exception:` 后直接返回 `'未知来源'`、`True`、HTTP 状态文本等硬编码值 | fail-open 过度 | 上游无法区分"搜索成功但无结果"与"搜索服务异常" | 统一返回带 `success=False` + `error_message` 的结构，由调用方决定是否降级 | 影响 pipeline 搜索判断 |
| P0-4 | `src/config.py` | 851-2386 | `_load_from_env` 单函数 1536 行，承担环境变量读取、类型转换、默认值填充、结构化校验等全部职责 | 未拆分 | 可测试性极差、改动极易引入回归、review 几乎不可行 | 按职责拆分为 `_parse_env_value`、`_coerce_type`、`_apply_defaults`、`_validate_required` 等独立函数 | 影响所有配置消费者 |
| P0-5 | `src/core/pipeline.py` | 329-1061 | `analyze_stock` 函数 732 行，承担数据获取、搜索、趋势分析、基本面聚合、Agent 分流、LLM 调用、结果填充等全部职责 | 未按阶段拆分 | 不可测试、改动易回归、review 困难 | 按 Step 拆分为私有方法或引入子组件（如 `DataCollector`、`NewsEnricher`、`LLMInvoker`） | 影响 analyzer/测试 |
| P0-6 | `src/services/portfolio_service.py` | 435-448 | `get_portfolio_snapshot()` 调用 `_build_snapshot_payload` 后立刻 `_persist_snapshot_payload` | 名义查询实际触发写库 | 高并发下锁竞争，已观察到 `409 portfolio_busy` | 区分 readonly query 与 write materialize；查询时只 replay 不 persist | 影响 API/并发 |
| P0-7 | `api/v1/endpoints/system_config.py` | 137-165 | `export_desktop_env` 返回 `.env` 文件原始内容，包含 API key、密码、Token | 未掩码 | 敏感信息泄露 | export 时对已知敏感 key 做掩码处理；加独立鉴权 | 影响安全 |
| P0-8 | `src/analysis/normalization/service.py` | 183-190 | `_DEFAULT_RULES` 中 `DecisionConsistencyRule` 出现两次 | 重复注册 | 同一规则被执行两遍，性能浪费且可能产生意外副作用 | 去重或检查重复 | 影响归一化结果 |

### P1（尽快修）

| 编号 | 文件 | 行号 | 现象 | 风险 | 修复建议 |
|------|------|------|------|------|----------|
| P1-1 | `main.py` | 811 | `run_full_analysis` 吞掉异常后 `return report`，`main()` 未检查 `report.success` | 调度层误判成功，CI/定时任务无法感知失败 | `main()` 检查 `report.success`，失败时返回非 0 |
| P1-2 | `src/search_service.py` | 2387 | `SearchService.is_available` 仅检查 legacy provider 的 API key 是否非空，不反映 MX 路由可用性 | 纯 MX 场景错误降级为"无搜索" | 将 MX 路由纳入可用性判断 |
| P1-3 | `src/storage.py` | 810 | `cursor.execute(f"PRAGMA busy_timeout={int(self._sqlite_busy_timeout_ms)}")` | 虽值经 `int()` 转换，但风格上未参数化；未来若来源被污染则有注入风险 | SQLite PRAGMA 不支持参数化，应额外校验数值范围后拼接，或加注释说明不可用户输入 |
| P1-4 | `main.py` | 647-655 | mx-moni 查询异常用 `except Exception:` 直接返回空 dict，无日志 | 服务异常时用户完全无感知，"持仓=0"误导 | 至少加 `logger.warning` |
| P1-5 | `src/notification.py` | ~1771 | `except Exception:` 后回退图片引擎，但无日志 | 图片生成失败无感知 | 加日志记录 |
| P1-6 | `src/stock_analyzer.py` | 264-274 | `MA60` 数据不足时用 `MA20` 替代 | downstream 可能按 MA60 逻辑解释，技术指标语义漂移 | 显式标记 `MA60` 为估算值或缺失 |
| P1-7 | `src/analyzer.py` | ~1256 | `_format_prompt` 512 行，含两套几乎重复的 prompt 模板（LEGACY + SYSTEM） | 维护困难、容易改一处漏一处 | 抽离 prompt 模板为独立文件或函数 |
| P1-8 | `api/v1/endpoints/portfolio.py` | 全部 | 所有 portfolio 接口未校验 `owner_id`，仅按 `account_id` 过滤 | 水平越权：用户 A 可访问用户 B 的 account（若知道 ID） | 绑定 session owner |
| P1-9 | `api/v1/endpoints/analysis.py` | ~408 | `get_task_list` 返回所有任务，无 owner/session 过滤 | 多用户场景下任务互相可见 | 加 owner 过滤 |
| P1-10 | `src/services/system_config_service.py` | `get_config` | `get_system_config` 返回未掩码的配置值 | 敏感字段明文暴露 | 返回时对敏感 key 做掩码 |

---

## 4. Danger-Surface 专项

| 编号 | 检查项 | 结论 | 证据 |
|------|--------|------|------|
| 1 | API 默认暴露面 | 高风险 | 认证默认关闭；`0.0.0.0:8000`；`/docs` 和 `/openapi.json` 免认证 |
| 2 | 首次初始化口令 | 中风险 | 首次设置密码无现密码校验（因为此前无密码），但需请求体提交；无验证码/邮件验证 |
| 3 | 静态文件 fallback 路径穿越 | P0 漏洞 | `api/app.py:195` 未约束目录，可穿越 |
| 4 | 系统配置明文返回 | 中风险 | `export_desktop_env` 返回原始 `.env`；`get_system_config` 返回未掩码值 |
| 5 | LLM 测试接口 | 中风险 | `system_config.py` 有 `test_llm_channel` 和 `discover_llm_models`，可能访问内网 URL |
| 6 | Webhook 外联 | 中风险 | `src/notification.py` 支持自定义 webhook，可能作为 SSRF 出口 |
| 7 | Session/History owner | 高风险 | `portfolio.py` / `analysis.py` / `history.py` 均按 ID 过滤，无 owner 绑定 |
| 8 | 名义只读触发写库 | 高风险 | `get_portfolio_snapshot()` 读时写库 |
| 9 | 异常回显 | 低风险 | `error_handler.py` 统一封装，未直接暴露 `str(exc)` |
| 10 | CORS | 中风险 | `CORS_ALLOW_ALL=true` 时允许任意来源，但 credentials 被置为 False |

---

## 5. 逻辑漏洞与契约漂移

### 5.1 退出码漂移

`run_full_analysis` 内部异常被 `except Exception` 捕获后设置 `report.success = False`，但 `main()` 未检查 `report.success`，仍返回 0。调度层（如 GitHub Actions / cron）会误判为成功。

### 5.2 搜索可用性语义漂移

`SearchService.is_available` 仅检查 legacy provider 的 API key，不反映 MX 路由可用性。当只有 MX 可用时，`is_available()` 返回 False，但 `get_capability_status().mx_route_available` 可能为 True。调用方若只用 `is_available` 判断会错误降级。

### 5.3 prompt 硬约束与上下文支撑漂移

`analyzer.py` 的 prompt 要求 LLM 输出 `position_advice.no_position` 和 `position_advice.has_position`。但 `pipeline.py` 中 `_build_portfolio_context_for_stock` 在失败时返回空结构，prompt 仍要求区分持仓/空仓建议，LLM 只能猜测。

### 5.4 持仓 snapshot 读写语义漂移

`GET /portfolio/snapshot` 是只读语义，但后端实际做 replay + materialize + persist，每次查询都触发写操作。

### 5.5 文档状态漂移

`docs/REPO_GUIDE.md` 声称"当前本地仓库与 GitHub 远端是同步的"，但当前工作区有大量未提交修改，文档状态描述不准确。

---

## 6. 健壮性与可维护性评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 功能完整性 | 4/5 | 覆盖分析、搜索、通知、回测、持仓、Bot、API，功能面广 |
| 代码正确性 | 3/5 | 主链路异常处理较完善（无 bare except），但 broad except 过多、fail-open 语义不一致 |
| 安全性 | 3/5 | 有明显路径穿越漏洞；认证默认关闭；敏感信息 export 未掩码；对象权限缺失 |
| 可维护性 | 2/5 | 超长函数（1536 行配置解析、732 行分析函数）、类型注解缺失 37.6%、类设计薄弱 |
| 可测试性 | 3/5 | 测试文件多但单元测试困难，集成测试为主；缺乏异常路径覆盖 |
| 文档一致性 | 3/5 | 文档分层清晰，但与实现存在漂移；AGENTS.md 规则较完善 |

---

## 7. 整改优先级路线图

### 立即修（本周内）

| 编号 | 问题 | 文件 | 预估工时 |
|------|------|------|----------|
| P0-1 | SPA fallback 路径穿越 | `api/app.py` | 0.5h |
| P0-2 | 删除 main.py 重复导入 | `main.py` | 0.5h |
| P0-3 | search_service fail-open 返回值统一 | `src/search_service.py` | 4h |
| P0-6 | snapshot 读时写库分离 | `src/services/portfolio_service.py` | 3h |
| P0-7 | export_desktop_env 掩码敏感字段 | `src/services/system_config_service.py` | 2h |
| P1-1 | main.py 失败时返回非 0 | `main.py` | 1h |

### 尽快修（2周内）

| 编号 | 问题 | 文件 | 预估工时 |
|------|------|------|----------|
| P0-4 | 拆分 _load_from_env | `src/config.py` | 8h |
| P0-5 | 拆分 analyze_stock | `src/core/pipeline.py` | 8h |
| P0-8 | 归一化规则去重 | `src/analysis/normalization/service.py` | 0.5h |
| P1-2 | is_available 语义修正 | `src/search_service.py` | 3h |
| P1-3 | storage PRAGMA f-string | `src/storage.py` | 0.5h |
| P1-4 | mx-moni 异常加日志 | `main.py` | 0.5h |
| P1-8 | portfolio owner 校验 | `api/v1/endpoints/portfolio.py` | 4h |
| P1-9 | analysis task owner 过滤 | `api/v1/endpoints/analysis.py` | 2h |

### 可排期（1个月内）

| 编号 | 问题 | 文件 | 预估工时 |
|------|------|------|----------|
| - | 全项目 broad except 分类审查 | 全项目 | 6h |
| - | 补全 public API 类型注解 | 全项目 | 12h |
| - | CI 引入 mypy/pyright | `.github/workflows/ci.yml` | 4h |
| - | prompt 字段消费链路审计 | `src/analyzer.py` | 4h |
| - | 模板与 dataclass 契约版本控制 | `templates/` / `src/schemas/` | 4h |
| - | 对象级权限加固（history/session owner 绑定） | `api/v1/endpoints/` | 8h |

---

## 8. 确定事实 vs 需运行态验证

### 已确定（有源码证据）

- SPA fallback 路径穿越（`api/app.py:195`）
- main.py 重复导入（行号 24/72 等）
- search_service 12+ 处 fail-open 硬编码
- `_load_from_env` 1536 行
- `analyze_stock` 732 行
- `DecisionConsistencyRule` 重复注册
- snapshot 读时写库（`_persist_snapshot_payload`）
- export_desktop_env 返回原始 `.env`
- `is_available` 不反映 MX 路由
- `run_full_analysis` 失败时 `main()` 返回 0
- 88 处 broad except
- 92 处 os.environ 修改

### 需运行态验证

- SPA 路径穿越能否实际读取 `/etc/passwd`
- 纯 MX 场景下搜索链路是否被误判为不可用
- 高并发下 `portfolio_busy` 触发频率
- Agent 路径与传统路径同输入输出差异
- `published_date` vs `pubdate` 是否仍丢结果
- `get_portfolio_snapshot` replay 耗时量化

---

*报告完成。如需针对任意 P0/P1 问题输出可直接应用的修复 diff，请告知。*
