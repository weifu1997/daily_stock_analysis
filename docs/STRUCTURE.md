# 项目结构图

这份文档只回答一件事：

> 这个仓库到底哪些是核心，哪些是辅助，哪些只是运行产物。

## 顶层结构

```text
daily_stock_analysis/
├── main.py                主调度入口
├── src/                   核心业务代码
├── api/                   Web API
├── apps/                  前端工程
├── scripts/               启动 / 执行脚本
├── strategies/            策略 skill
├── templates/             模板
├── docs/                  文档
├── tests/                 测试
├── data/                  数据库 / 运行数据
├── logs/                  日志
└── memory/                 记忆与状态
```

## 哪些是核心

优先级从高到低：

1. `main.py`
2. `src/`
3. `api/`
4. `scripts/`
5. `tests/`

## 哪些是阅读辅助

- `docs/`
- `strategies/`
- `templates/`
- `apps/`

这些目录很重要，但不是主业务流本身。

## 哪些通常不是代码问题

如果你在 GitHub 上看到下面这些，不要误判成仓库乱：

- `logs/`
- `data/`
- `.venv/`
- `.pytest_cache/`
- `__pycache__/`

它们大多是运行产物、缓存、或者本地环境。

## 妙想相关目录怎么理解

妙想相关代码分两层：

### 1）仓库内适配层

位于 `src/integrations/mx/`。

这部分是仓库里真正会跑的代码。

### 2）外部 skill 目录

某些封装会去找额外的外部 skill 目录。
如果那边没装齐，仓库内会降级或给出明确报错。

## 看仓库的推荐顺序

1. `README.md`
2. `docs/README.md`
3. `docs/REPO_GUIDE.md`
4. `docs/INDEX.md`
5. `docs/STRUCTURE.md`
6. `main.py`
7. `src/config.py`
8. `src/core/pipeline.py`
9. `src/search_service.py`

## 一句话总结

这个仓库不是“文件多所以乱”，而是“功能面广 + 历史兼容层多”。
先看结构图，再看导览页，阅读成本会低很多。