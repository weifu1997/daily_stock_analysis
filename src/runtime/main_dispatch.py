# -*- coding: utf-8 -*-
"""Mode dispatch helpers for the top-level main entrypoint.

These helpers keep ``main.py`` thin while preserving the existing CLI/runtime
behavior and the current test patch points that matter.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from data_provider.base import canonical_stock_code
from src.config import Config
from src.runtime.mx_preselect import resolve_mx_profile_query
from src.services.mx_name_cache import cache_stock_name

logger = logging.getLogger(__name__)


def _resolve_scheduled_stock_codes(stock_codes: Optional[List[str]]) -> Optional[List[str]]:
    """Scheduled runs should always read the latest persisted watchlist."""
    if stock_codes is not None:
        try:
            from main import logger as main_logger
            main_logger.warning(
                "定时模式下检测到 --stocks 参数；计划执行将忽略启动时股票快照，并在每次运行前重新读取最新的 STOCK_LIST。"
            )
        except Exception:
            logger.warning(
                "定时模式下检测到 --stocks 参数；计划执行将忽略启动时股票快照，并在每次运行前重新读取最新的 STOCK_LIST。"
            )
    return None


def _resolve_mx_preselect_settings(config: Config) -> tuple[Optional[str], Optional[str]]:
    """方案A：生产主链只认 .env / 持久化配置。"""
    env_query = (getattr(config, 'mx_preselect_query', None) or '').strip()
    if env_query:
        return env_query, 'env_query'

    env_profile = (getattr(config, 'mx_preselect_profile', None) or '').strip().lower()
    if env_profile:
        profile_query = resolve_mx_profile_query(env_profile)
        if profile_query:
            return profile_query, f'env_profile:{env_profile}'

    return None, None


def _resolve_mx_preselect_stock_codes(config: Config) -> Optional[List[str]]:
    """Use the MX smart selection skill to derive stock codes."""
    preselect_query, query_source = _resolve_mx_preselect_settings(config)
    if not preselect_query:
        logger.info('妙想预选池未启用：未在 .env / 持久化配置中配置 MX_PRESELECT_QUERY 或 MX_PRESELECT_PROFILE')
        return None

    skill_path = Path('/root/.hermes/skills/mx-xuangu')
    if not skill_path.exists():
        logger.warning('妙想智能选股 skill 未安装，回退到 STOCK_LIST')
        return None

    try:
        if str(skill_path) not in sys.path:
            sys.path.insert(0, str(skill_path))
        from mx_xuangu import MXSelectStock  # type: ignore

        selector = MXSelectStock()
        result = selector.search(preselect_query)
        rows, data_source, err = selector.extract_data(result)
        if err:
            logger.warning('妙想预选股失败（%s），回退到 STOCK_LIST', err)
            return None
        limit = getattr(config, 'mx_preselect_limit', 50) or 50
        codes: List[str] = []
        name_count = 0
        names_by_code: Dict[str, str] = {}
        for row in rows[:limit]:
            code = str(
                row.get('SECURITY_CODE')
                or row.get('代码')
                or row.get('symbol')
                or row.get('code')
                or ''
            ).strip()
            name = str(
                row.get('SECURITY_SHORT_NAME')
                or row.get('名称')
                or row.get('股票名称')
                or row.get('name')
                or row.get('stock_name')
                or ''
            ).strip()
            market = str(
                row.get('MARKET_SHORT_NAME')
                or row.get('市场代码简称')
                or row.get('market')
                or ''
            ).strip().upper()
            if not code:
                continue
            if market == 'SH' and len(code) == 6:
                normalized = f'{code}.SH'
            elif market == 'SZ' and len(code) == 6:
                normalized = f'{code}.SZ'
            else:
                normalized = code
            codes.append(normalized)
            if name:
                names_by_code[normalized] = name
                cached = cache_stock_name(normalized, name)
                if cached:
                    name_count += 1
        if codes:
            logger.info('妙想预选股成功：source=%s query=%r, data_source=%s, count=%d, name_cached=%d', query_source, preselect_query, data_source, len(codes), name_count)
            logger.info('妙想预选股返回代码列表：%s', ', '.join(codes))
            if names_by_code:
                logger.debug('妙想预选名称样本：%s', list(names_by_code.items())[:5])
            return codes
        logger.warning('妙想预选股返回为空，回退到 STOCK_LIST')
        return None
    except Exception as exc:
        logger.warning('妙想预选股调用失败，回退到 STOCK_LIST: %s', exc, exc_info=True)
        return None


def _resolve_portfolio_stock_codes() -> List[str]:
    """Resolve current portfolio holding symbols for daily analysis."""
    try:
        from src.services.portfolio_service import PortfolioService

        service = PortfolioService()
        snapshot = service.get_portfolio_snapshot()
        codes: List[str] = []
        from src.services.mx_name_cache import cache_stock_name

        for account in snapshot.get('accounts', []) or []:
            for pos in account.get('positions', []) or []:
                symbol = str(pos.get('symbol') or pos.get('code') or '').strip()
                if not symbol:
                    continue
                code = canonical_stock_code(symbol)
                if not code:
                    continue
                name = str(
                    pos.get('name')
                    or pos.get('stock_name')
                    or pos.get('SECURITY_SHORT_NAME')
                    or pos.get('short_name')
                    or ''
                ).strip()
                if name:
                    cache_stock_name(code, name)
                codes.append(code)
        unique_codes = list(dict.fromkeys(code for code in codes if code))
        if unique_codes:
            logger.info('已合并持仓股到每日分析池：count=%d', len(unique_codes))
        return unique_codes
    except Exception as exc:
        logger.warning('读取持仓快照失败，跳过持仓并入：%s', exc, exc_info=True)
        return []


def _merge_stock_code_pools(*pools: Optional[List[str]]) -> List[str]:
    """Merge stock pools with order preserved and duplicates removed."""
    merged: List[str] = []
    seen: set = set()
    for pool in pools:
        if not pool:
            continue
        for code in pool:
            normalized = canonical_stock_code(str(code))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


def prepare_startup_stock_codes(args: argparse.Namespace, config: Config) -> Optional[List[str]]:
    """Resolve the initial stock universe before mode dispatch."""
    stock_codes: Optional[List[str]] = None
    if args.stocks:
        stock_codes = [canonical_stock_code(c) for c in args.stocks.split(',') if (c or "").strip()]
        logger.info(f"使用命令行指定的股票列表: {stock_codes}")
    elif getattr(args, 'mx_query', None) or getattr(args, 'mx_profile', None):
        logger.warning('方案A已启用：生产主链妙想预选只认 .env / 持久化配置，忽略 CLI 的 --mx-query/--mx-profile')
        stock_codes = _resolve_mx_preselect_stock_codes(config)
        if stock_codes:
            logger.info(f"使用妙想预选池股票列表: {stock_codes}")
    elif getattr(args, 'mx_preselect', False) or getattr(config, 'mx_preselect_priority', False) or getattr(config, 'mx_preselect_query', None) or getattr(config, 'mx_preselect_profile', None):
        stock_codes = _resolve_mx_preselect_stock_codes(config)
        if stock_codes:
            logger.info(f"使用妙想预选池股票列表: {stock_codes}")

    portfolio_stock_codes = _resolve_portfolio_stock_codes()
    if portfolio_stock_codes:
        stock_codes = _merge_stock_code_pools(stock_codes, portfolio_stock_codes)
        logger.info(f"每日分析合并持仓后股票列表: {stock_codes}")
    return stock_codes


def normalize_service_mode_args(args: argparse.Namespace, config: Config) -> bool:
    """Apply CLI/config compatibility for web/service startup and return start flag."""
    if args.webui:
        args.serve = True
    if args.webui_only:
        args.serve_only = True
    if config.webui_enabled and not (args.serve or args.serve_only):
        args.serve = True

    start_serve = (args.serve or args.serve_only) and os.getenv("GITHUB_ACTIONS") != "true"
    if start_serve:
        if args.host == '0.0.0.0' and os.getenv('WEBUI_HOST'):
            args.host = os.getenv('WEBUI_HOST')
        if args.port == 8000 and os.getenv('WEBUI_PORT'):
            args.port = int(os.getenv('WEBUI_PORT'))
    return start_serve


def start_service_runtime(
    args: argparse.Namespace,
    config: Config,
    start_serve: bool,
    start_api_server=None,
    prepare_webui_frontend_assets=None,
    start_bot_stream_clients=None,
) -> bool:
    """Start web service and bot clients if requested."""
    if not start_serve:
        return False
    if start_api_server is None:
        from main import start_api_server as start_api_server  # avoid hard dependency at import time
    if prepare_webui_frontend_assets is None:
        from src.webui_frontend import prepare_webui_frontend_assets as prepare_webui_frontend_assets
    if start_bot_stream_clients is None:
        from main import start_bot_stream_clients as start_bot_stream_clients  # avoid hard dependency at import time

    if not prepare_webui_frontend_assets():
        logger.warning("前端静态资源未就绪，继续启动 FastAPI 服务（Web 页面可能不可用）")
    try:
        start_api_server(host=args.host, port=args.port, config=config)
    except Exception as exc:
        logger.error(f"启动 FastAPI 服务失败: {exc}")
        return False
    start_bot_stream_clients(config)
    return True


def run_serve_only_mode(args: argparse.Namespace) -> int:
    logger.info("模式: 仅 Web 服务")
    logger.info(f"Web 服务运行中: http://{args.host}:{args.port}")
    logger.info("通过 /api/v1/analysis/analyze 接口触发分析")
    logger.info(f"API 文档: http://{args.host}:{args.port}/docs")
    logger.info("按 Ctrl+C 退出...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n用户中断，程序退出")
    return 0


def run_backtest_mode(args: argparse.Namespace) -> bool:
    if not getattr(args, 'backtest', False):
        return False
    logger.info("模式: 回测")
    from src.services.backtest_service import BacktestService

    service = BacktestService()
    stats = service.run_backtest(
        code=getattr(args, 'backtest_code', None),
        force=getattr(args, 'backtest_force', False),
        eval_window_days=getattr(args, 'backtest_days', None),
    )
    logger.info(
        f"回测完成: processed={stats.get('processed')} saved={stats.get('saved')} "
        f"completed={stats.get('completed')} insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
    )
    return True


def run_moni_execute_mode(args: argparse.Namespace) -> bool:
    if not getattr(args, 'moni_execute', False):
        return False
    logger.info("模式: 次日模拟交易执行")
    from scripts.run_moni_execute import execute_latest_plan

    execute_latest_plan()
    return True


def run_market_review_mode(args: argparse.Namespace, config: Config) -> bool:
    if not args.market_review:
        return False

    from src.analyzer import GeminiAnalyzer
    from src.core.market_review import run_market_review
    from src.notification import NotificationService
    from src.search_service import SearchService

    effective_region = None
    if not getattr(args, 'force_run', False) and getattr(config, 'trading_day_check_enabled', True):
        from src.core.trading_calendar import get_open_markets_today, compute_effective_region as _compute_region
        open_markets = get_open_markets_today()
        effective_region = _compute_region('cn', open_markets)
        if effective_region == '':
            logger.info("今日大盘复盘相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。")
            return True

    logger.info("模式: 仅大盘复盘")
    notifier = NotificationService()
    search_service = None
    analyzer = None

    if config.has_search_capability_enabled():
        search_service = SearchService(
            bocha_keys=config.bocha_api_keys,
            tavily_keys=config.tavily_api_keys,
            brave_keys=config.brave_api_keys,
            serpapi_keys=config.serpapi_keys,
            minimax_keys=config.minimax_api_keys,
            searxng_base_urls=config.searxng_base_urls,
            searxng_public_instances_enabled=config.searxng_public_instances_enabled,
            news_max_age_days=config.news_max_age_days,
            news_strategy_profile=getattr(config, "news_strategy_profile", "short"),
        )

    if config.gemini_api_key or config.openai_api_key:
        analyzer = GeminiAnalyzer(api_key=config.gemini_api_key)
        if not analyzer.is_available():
            logger.warning("AI 分析器初始化后不可用，请检查 API Key 配置")
            analyzer = None
    else:
        logger.warning("未检测到 API Key (Gemini/OpenAI)，将仅使用模板生成报告")

    run_market_review(
        notifier=notifier,
        analyzer=analyzer,
        search_service=search_service,
        send_notification=True,
        override_region=effective_region,
    )
    return True


def run_schedule_mode(
    args: argparse.Namespace,
    config: Config,
    stock_codes: Optional[List[str]],
    reload_runtime_config,
    build_schedule_time_provider,
    run_full_analysis,
) -> bool:
    if not (args.schedule or config.schedule_enabled):
        return False

    logger.info("模式: 定时任务")
    logger.info(f"每日执行时间: {config.schedule_time}")

    should_run_immediately = config.schedule_run_immediately
    if getattr(args, 'no_run_immediately', False):
        should_run_immediately = False

    logger.info(f"启动时立即执行: {should_run_immediately}")

    from src.scheduler import run_with_schedule
    scheduled_stock_codes = _resolve_scheduled_stock_codes(stock_codes)
    schedule_time_provider = build_schedule_time_provider(config.schedule_time)

    def scheduled_task():
        runtime_config = reload_runtime_config()
        run_full_analysis(runtime_config, args, scheduled_stock_codes)

    background_tasks = []
    if getattr(config, 'agent_event_monitor_enabled', False):
        from src.agent.events import build_event_monitor_from_config, run_event_monitor_once

        monitor = build_event_monitor_from_config(config)
        if monitor is not None:
            interval_minutes = max(1, getattr(config, 'agent_event_monitor_interval_minutes', 5))

            def event_monitor_task():
                triggered = run_event_monitor_once(monitor)
                if triggered:
                    logger.info("[EventMonitor] 本轮触发 %d 条提醒", len(triggered))

            background_tasks.append({
                "task": event_monitor_task,
                "interval_seconds": interval_minutes * 60,
                "run_immediately": True,
                "name": "agent_event_monitor",
            })
        else:
            logger.info("EventMonitor 已启用，但未加载到有效规则，跳过后台提醒任务")

    run_with_schedule(
        task=scheduled_task,
        schedule_time=config.schedule_time,
        run_immediately=should_run_immediately,
        background_tasks=background_tasks,
        schedule_time_provider=schedule_time_provider,
    )
    return True


def run_single_analysis_mode(config: Config, args: argparse.Namespace, stock_codes: Optional[List[str]], start_serve: bool, run_full_analysis=None) -> int:
    if run_full_analysis is None:
        from main import run_full_analysis as run_full_analysis

    if config.run_immediately:
        report = run_full_analysis(config, args, stock_codes)
        if report is not None and not getattr(report, "success", False):
            return 1
    else:
        logger.info("配置为不立即运行分析 (RUN_IMMEDIATELY=false)")

    logger.info("\n程序执行完成")

    if start_serve and not (args.schedule or config.schedule_enabled):
        logger.info("API 服务运行中 (按 Ctrl+C 退出)...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    return 0
