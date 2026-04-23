# Issue 3: history / backtest / usage 对象级权限绑定（Schema 变更）

## 背景
P1-8/P1-9 已为 portfolio 和 analysis task 引入了 `X-Owner-Id` 最小认证层，但以下端点仍缺少 owner 隔离：
- `api/v1/endpoints/history.py` — 5 个接口
- `api/v1/endpoints/backtest.py` — 4 个接口
- `api/v1/endpoints/usage.py` — 1 个接口
- `api/v1/endpoints/agent.py` 中 `research` 端点 — 1 个接口

## 根因
数据库表 `analysis_history`、`backtest_results`、`conversation_messages`、`llm_usage` 均无 `owner_id` 字段，导致无法在 repo 层做 owner 过滤。

## 目标
1. 给上述表增加 `owner_id` 列（可空，兼容现有数据）
2. 写入链路（analysis、backtest、agent chat、LLM usage）记录 owner
3. repo/service/API 三层增加 owner 过滤
4. 现有历史数据标记为 `owner_id = NULL`（不过滤）

## 具体任务清单

### Phase 1: Schema 变更
- [ ] `analysis_history` 表增加 `owner_id VARCHAR(64)` + 索引 `ix_analysis_owner_time`
- [ ] `backtest_results` 表增加 `owner_id VARCHAR(64)` + 索引
- [ ] `conversation_messages` 表增加 `owner_id VARCHAR(64)`（或利用 `session_id` 前缀做软隔离）
- [ ] `llm_usage` 表增加 `owner_id VARCHAR(64)`
- [ ] 生成 Alembic / 手动 migration 脚本
- [ ] 在 `DatabaseManager.__init__` 或启动时自动执行 migration（如果尚未应用）

### Phase 2: 写入链路改造
- [ ] `save_analysis_result` 增加 `owner_id` 参数，从调用方传入
- [ ] `run_backtest` 中 `BacktestResult` 写入时关联 `analysis_history.owner_id`
- [ ] `record_llm_usage` 增加 `owner_id` 参数
- [ ] `save_chat_message` / `add_conversation_message` 增加 `owner_id` 参数
- [ ] `api/v1/endpoints/analysis.py` `trigger_analysis` 注入 `owner_id` 到写入链路

### Phase 3: 查询链路改造
- [ ] `get_analysis_history_paginated` 增加 `owner_id` 过滤
- [ ] `get_analysis_history_by_id` 增加 `owner_id` 校验（查不到或 owner 不匹配返回 None）
- [ ] `delete_analysis_history_records` 增加 `owner_id` 过滤（只删自己的）
- [ ] `get_news_intel_by_query_id` 通过 `query_id` 关联 `analysis_history.owner_id`
- [ ] `get_llm_usage_summary` 增加 `owner_id` 聚合
- [ ] BacktestRepository 所有查询方法增加 `owner_id` 过滤

### Phase 4: API 层改造
- [ ] `history.py` 所有端点注入 `get_current_owner_id`
- [ ] `backtest.py` 所有端点注入 `get_current_owner_id`
- [ ] `usage.py` `get_usage_summary` 注入 `get_current_owner_id`
- [ ] `agent.py` `agent_research` 注入 `get_current_owner_id` 并校验 session

### Phase 5: 测试
- [ ] 新增 `test_history_owner_isolation.py`
- [ ] 新增 `test_backtest_owner_isolation.py`
- [ ] 新增 `test_usage_owner_isolation.py`
- [ ] 验证 `owner_id=NULL` 的历史数据仍能被查询（向后兼容）

## 验收标准
- [ ] 无 header 时所有端点行为不变（向后兼容）
- [ ] 有 `X-Owner-Id` 时，用户只能看到/操作自己的数据
- [ ] 尝试访问他人数据返回 403
- [ ] 现有测试全部通过

## 预估工时
8-12 小时（取决于 migration 工具选择和数据量）

## 优先级
**P2**（安全加固，当前单用户部署风险可控）
