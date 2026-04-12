# 仓库导览

这份导览的目标很简单：让人快速知道仓库里每一块是干什么的，减少“上去看着乱”的感觉。

## 先说结论

- 当前本地仓库与 GitHub 远端是同步的
- 当前分支：`main`
- 当前提交：`8feac5f fix mx skill resolution fallbacks`
- 所以你现在看到的本地代码，和 GitHub 上的代码是一致的

## 一句话看懂项目

这是一个 A 股每日股票分析系统：
- Python 后端负责分析、复盘、定时任务、通知推送
- Web 前端负责首页、问股、持仓、回测、设置
- 还有 Electron 桌面壳

## 目录结构速览

| 目录/文件 | 作用 |
|---|---|
| `main.py` | 主入口，统一调度分析、复盘、定时任务、Web 服务 |
| `src/` | 核心业务代码：配置、分析引擎、数据源、通知、搜索、回测等 |
| `api/` | Web API 服务入口与路由 |
| `apps/dsa-web/` | Web 前端工程 |
| `strategies/` | 策略 skill，YAML 形式的交易策略文件 |
| `templates/` | 报告模板、通知模板、页面模板 |
| `scripts/` | 各类启动脚本、自动执行脚本、工具脚本 |
| `docs/` | 文档、部署说明、FAQ、变更记录、技能集成说明 |
| `tests/` | 单元测试、集成测试、回归测试 |
| `data/` | 运行时数据和数据库（通常不纳入 git） |
| `logs/` | 运行日志（通常不纳入 git） |
| `memory/` | 记忆/状态相关内容 |
| `patch/` | 补丁与修复逻辑 |

## 主要入口

### 1）单次分析主链路

- `main.py`
- 负责：
  - 读取配置
  - 初始化数据源
  - 构建候选池
  - 跑个股分析
  - 生成通知/报告

### 2）Web 服务

- `api/app.py`
- `webui.py`
- `apps/dsa-web/`

### 3）定时任务

- `scripts/run_daily_stock_analysis.sh`
- `scripts/run_daily_stock_analysis_tmux.sh`

### 4）测试

- `tests/`
- 建议先跑局部回归，再跑全量测试

## 关键业务模块

### 分析链路

- `src/core/pipeline.py`
  - 候选池构建
  - 并发调度
  - 个股分析执行

### 配置

- `src/config.py`
  - 所有运行时配置的主入口

### 搜索

- `src/search_service.py`
  - 新闻搜索、mx-search 路由、fallback 逻辑

### 妙想增强层

- `src/integrations/mx/`
  - `client.py`：妙想主客户端
  - `zixuan_client.py`：自选同步
  - `moni_client.py`：模拟仓执行/查询
  - `search_adapter.py`：搜索适配层

### 通知

- `src/notification.py`
- 负责飞书、邮箱等通知渠道

### 回测

- `src/services/backtest_service.py`
- `src/core/backtest.py`

## 代码阅读顺序

如果你是第一次看这个仓库，我建议按这个顺序：

1. `README.md`
2. `docs/REPO_GUIDE.md`
3. `main.py`
4. `src/config.py`
5. `src/core/pipeline.py`
6. `src/search_service.py`
7. `src/integrations/mx/`
8. `tests/` 里的对应回归用例

## 容易看乱的地方

### 1）运行时产物很多

`logs/`、`.pytest_cache/`、`.venv/`、`data/` 这些都是本地运行产物或环境产物。

你在 GitHub 页面上如果直接看目录，会感觉很多“东西”。
实际上它们不是核心代码，只是运行痕迹。

### 2）策略和 skill 的命名有历史包袱

文档里会同时出现：
- `strategy`
- `skill`

当前项目里，交易策略相关内容常常叫“策略”，代码字段里会保留 `skill` 作为兼容命名。
这不是重复实现，是历史兼容。

### 3）妙想相关代码分成两层

- 仓库内的适配层：可以直接跑
- 外部 skill 目录：如果机器上没装，就会降级或报明确错误

所以看到 `mx` 相关日志，不要默认是“仓库坏了”，先区分是：
- 适配层问题
- 外部 skill 缺失
- 真实 API 返回异常

## 当前仓库状态

- 本地与 GitHub 已同步
- 当前代码没有额外未提交改动
- 你现在看到的本地目录结构，就是远端仓库结构

## 如果你觉得“乱”，通常可以先看这三处

| 想看什么 | 先看哪里 |
|---|---|
| 主逻辑怎么跑 | `main.py` + `src/core/pipeline.py` |
| 配置怎么进来 | `src/config.py` |
| 结果怎么出去 | `src/notification.py` + `docs/` |

## 最后一句

如果你愿意，我可以继续把 README 首页再收一轮：
- 把“仓库导览”放到更显眼的位置
- 把运行时产物、核心代码、文档入口分成三块
- 让 GitHub 首页一眼就知道先看什么、后看什么
