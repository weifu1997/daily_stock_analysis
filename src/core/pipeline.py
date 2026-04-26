# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 核心分析流水线
===================================

职责：
1. 管理整个分析流程
2. 协调数据获取、存储、搜索、分析、通知等模块
3. 实现并发控制和异常处理
4. 提供股票分析的核心功能
"""

import logging
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass

import pandas as pd

from src.config import get_config, Config
from src.storage import get_db
from data_provider import DataFetcherManager
from data_provider.base import normalize_stock_code
from data_provider.realtime_types import ChipDistribution
from src.analyzer import GeminiAnalyzer, AnalysisResult, fill_chip_structure_if_needed, fill_price_position_if_needed, fill_institution_structure_if_needed
from src.data.stock_mapping import STOCK_NAME_MAP
from src.notification import NotificationService, NotificationChannel
from src.report_language import (
    get_unknown_text,
    localize_confidence_level,
    normalize_report_language,
)
from src.search_service import SearchService
from src.services.social_sentiment_service import SocialSentimentService
from src.services.candidate_enrichment import CandidateEnrichmentService
from src.services.candidate_scoring_service import CandidateScoringService
from src.analysis.execution import build_execution_plan, build_execution_plan_map
from src.runtime.mx_preselect import MX_PRESELECT_PROFILES, MX_PRESELECT_EXCLUDE_TOKENS, MX_PRESELECT_REQUIRED_TOKENS, resolve_mx_profile_query, validate_preselect_query


@dataclass
class _AnalysisInputs:
    """analyze_stock 数据收集阶段的中间结果传递对象。"""
    stock_name: str
    realtime_quote: Optional[Any]
    current_price: Optional[float]
    daily_df: Optional[Any]
    daily_source: Optional[str]
    chip_data: Optional[Any]
    fundamental_context: Optional[Dict[str, Any]]
    trend_result: Optional[Any]
    portfolio_context: Optional[Any]
from src.services.mx_name_cache import get_cached_stock_name
from src.services.portfolio_service import PortfolioService
from src.analysis.context_models import PortfolioContext
from src.analysis.normalization import AnalysisNormalizationContext, normalize_analysis_result
from src.analysis.technical_factor_summary import summarize_stk_factor_snapshot
from src.integrations.mx.client import MxClient
from src.integrations.mx.search_adapter import MxSearchAdapter
from src.enums import ReportType
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
from src.core.trading_calendar import (
    get_effective_trading_date,
    get_market_for_stock,
    get_market_now,
    is_market_open,
)
from data_provider.us_index_mapping import is_us_stock_code
from bot.models import BotMessage


logger = logging.getLogger(__name__)

# 防御性 guard：当实例绕过 __init__（如测试中 __new__）构造时，
# double-check 初始化 _single_stock_notify_lock 仍然线程安全。
_SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD = threading.Lock()


class StockAnalysisPipeline:
    """
    股票分析主流程调度器
    
    职责：
    1. 管理整个分析流程
    2. 协调数据获取、存储、搜索、分析、通知等模块
    3. 实现并发控制和异常处理
    """
    
    def __init__(
        self,
        config: Optional[Config] = None,
        max_workers: Optional[int] = None,
        source_message: Optional[BotMessage] = None,
        query_id: Optional[str] = None,
        query_source: Optional[str] = None,
        save_context_snapshot: Optional[bool] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ):
        """
        初始化调度器
        
        Args:
            config: 配置对象（可选，默认使用全局配置）
            max_workers: 最大并发线程数（可选，默认从配置读取）
        """
        self.config = config or get_config()
        self.max_workers = max_workers or self.config.max_workers
        self.source_message = source_message
        self.query_id = query_id
        self.query_source = self._resolve_query_source(query_source)
        self.save_context_snapshot = (
            self.config.save_context_snapshot if save_context_snapshot is None else save_context_snapshot
        )
        self.progress_callback = progress_callback
        self._portfolio_snapshot_loaded = False
        self._portfolio_snapshot_cache = None
        self._prefetched_daily_data: Dict[str, Tuple[pd.DataFrame, str, date]] = {}
        
        # 初始化各模块
        self.db = get_db()
        self.fetcher_manager = DataFetcherManager()
        # 不再单独创建 akshare_fetcher，统一使用 fetcher_manager 获取增强数据
        self.trend_analyzer = StockTrendAnalyzer()  # 技术分析器
        self.candidate_scoring_service = CandidateScoringService()
        self.analyzer = GeminiAnalyzer(config=self.config)
        self.notifier = NotificationService(source_message=source_message)
        self._single_stock_notify_lock = threading.Lock()
        
        # 初始化搜索服务（可选，初始化失败不应阻断主分析流程）
        try:
            self.search_service = SearchService(
                bocha_keys=self.config.bocha_api_keys,
                tavily_keys=self.config.tavily_api_keys,
                brave_keys=self.config.brave_api_keys,
                serpapi_keys=self.config.serpapi_keys,
                minimax_keys=self.config.minimax_api_keys,
                searxng_base_urls=self.config.searxng_base_urls,
                searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                news_max_age_days=self.config.news_max_age_days,
                news_strategy_profile=getattr(self.config, "news_strategy_profile", "short"),
            )
        except Exception as exc:
            logger.warning("搜索服务初始化失败，将以无搜索模式运行: %s", exc, exc_info=True)
            self.search_service = None
        
        logger.info(f"调度器初始化完成，最大并发数: {self.max_workers}")
        logger.info("已启用技术分析引擎（均线/趋势/量价指标）")
        # 打印实时行情/筹码配置状态
        if self.config.enable_realtime_quote:
            logger.info(f"实时行情已启用 (优先级: {self.config.realtime_source_priority})")
        else:
            logger.info("实时行情已禁用，将使用历史收盘价")
        if self.config.enable_chip_distribution:
            logger.info("筹码分布分析已启用")
        else:
            logger.info("筹码分布分析已禁用")
        if self.search_service is None:
            logger.warning("搜索服务未启用（初始化失败或依赖缺失）")
        else:
            capability = self.search_service.get_capability_status()
            logger.info(
                "搜索服务能力状态: legacy=%s, mx_route=%s, comprehensive_intel=%s, reasons=%s",
                capability.legacy_available,
                capability.mx_route_available,
                capability.comprehensive_intel_available,
                capability.reasons,
            )
            if capability.comprehensive_intel_available:
                logger.info("搜索服务已启用")
            else:
                logger.warning("搜索服务未启用（无可用情报搜索能力）")

        # 初始化社交舆情服务（仅美股，可选）
        try:
            self.social_sentiment_service = SocialSentimentService(
                api_key=self.config.social_sentiment_api_key,
                api_url=self.config.social_sentiment_api_url,
            )
            if self.social_sentiment_service.is_available:
                logger.info("Social sentiment service enabled (Reddit/X/Polymarket, US stocks only)")
        except Exception as exc:
            logger.warning(
                "社交舆情服务初始化失败，将跳过舆情分析: %s",
                exc,
                exc_info=True,
            )
            self.social_sentiment_service = None

        try:
            self.mx_client = MxClient(
                base_url=getattr(self.config, 'mx_base_url', None),
                api_key=getattr(self.config, 'mx_apikey', None) or getattr(self.config, 'mx_api_key', None),
                timeout=getattr(self.config, 'mx_timeout_seconds', 8.0),
            )
            self.mx_search_adapter = MxSearchAdapter(self.mx_client)
            self.candidate_enrichment_service = CandidateEnrichmentService(self.mx_search_adapter, self.mx_client)
            if self.mx_client.enabled:
                logger.info("妙想增强层已启用")
        except Exception as exc:
            logger.warning("妙想增强层初始化失败，将以纯本地模式运行: %s", exc, exc_info=True)
            self.mx_client = None
            self.mx_search_adapter = None
            self.candidate_enrichment_service = CandidateEnrichmentService(None, None)

    def _emit_progress(self, progress: int, message: str) -> None:
        """Best-effort bridge from pipeline stages to task SSE progress."""
        callback = getattr(self, "progress_callback", None)
        if callback is None:
            return
        try:
            callback(progress, message)
        except Exception as exc:
            query_id = getattr(self, "query_id", None)
            logger.warning(
                "[pipeline] progress callback failed: %s (progress=%s, message=%r, query_id=%s)",
                exc,
                progress,
                message,
                query_id,
                extra={
                    "progress": progress,
                    "progress_message": message,
                    "query_id": query_id,
                },
            )

    def _ensure_runtime_caches(self) -> None:
        if not hasattr(self, "_prefetched_daily_data") or self._prefetched_daily_data is None:
            self._prefetched_daily_data = {}

    def fetch_and_save_stock_data(
        self, 
        code: str,
        force_refresh: bool = False,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        获取并保存单只股票数据
        
        断点续传逻辑：
        1. 检查数据库是否已有最新可复用交易日数据
        2. 如果有且不强制刷新，则跳过网络请求
        3. 否则从数据源获取并保存
        
        Args:
            code: 股票代码
            force_refresh: 是否强制刷新（忽略本地缓存）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断
            
        Returns:
            Tuple[是否成功, 错误信息]
        """
        stock_name = code
        try:
            # 首先获取股票名称
            stock_name = self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            target_date = self._resolve_resume_target_date(
                code, current_time=current_time
            )

            # 断点续传检查：如果最新可复用交易日的数据已存在，则跳过
            if not force_refresh and self.db.has_today_data(code, target_date):
                logger.info(
                    f"{stock_name}({code}) {target_date} 数据已存在，跳过获取（断点续传）"
                )
                return True, None

            # 从数据源获取数据
            logger.info(f"{stock_name}({code}) 开始从数据源获取数据...")
            df, source_name = self.fetcher_manager.get_daily_data(code, days=120)

            if df is None or df.empty:
                return False, "获取数据为空"

            self._ensure_runtime_caches()
            self._prefetched_daily_data[code] = (df.copy(), source_name, target_date)

            # 保存到数据库
            saved_count = self.db.save_daily_data(df, code, source_name)
            logger.info(f"{stock_name}({code}) 数据保存成功（来源: {source_name}，新增 {saved_count} 条）")

            # 最小闭环：仅当主日线就来自 Tushare 时，才补齐复权数据，避免在 Tushare 已配额退化时重复施压
            try:
                tushare_fetcher = self.fetcher_manager.get_fetcher("TushareFetcher")
                if (
                    source_name == "TushareFetcher"
                    and tushare_fetcher is not None
                    and getattr(tushare_fetcher, "is_available", lambda: False)()
                ):
                    tushare_source_name = getattr(tushare_fetcher, "name", "TushareFetcher")
                    date_series = None
                    if 'date' in df.columns:
                        date_series = pd.to_datetime(df['date'], errors='coerce')
                    elif 'trade_date' in df.columns:
                        date_series = pd.to_datetime(df['trade_date'], errors='coerce')

                    start_date = None
                    end_date = None
                    if date_series is not None and not date_series.dropna().empty:
                        start_date = date_series.min().strftime('%Y-%m-%d')
                        end_date = date_series.max().strftime('%Y-%m-%d')
                    else:
                        end_date = target_date.strftime('%Y-%m-%d') if hasattr(target_date, 'strftime') else str(target_date)
                        start_date = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=90)).strftime('%Y-%m-%d')

                    adj_daily_df = tushare_fetcher.get_daily_adj_data(
                        stock_code=code,
                        start_date=start_date,
                        end_date=end_date,
                        raw_df=df,
                    )
                    adj_factor_df = None
                    if adj_daily_df is not None and not adj_daily_df.empty:
                        adj_daily_saved = self.db.save_daily_adj_data(adj_daily_df, code, tushare_source_name)
                        logger.info(
                            f"{stock_name}({code}) 复权日线保存成功（新增 {adj_daily_saved} 条）"
                        )
                        if 'adj_factor' in adj_daily_df.columns:
                            adj_factor_df = adj_daily_df[['date', 'adj_factor']].copy()
                    if adj_factor_df is None or adj_factor_df.empty:
                        adj_factor_df = tushare_fetcher.get_adj_factor_data(
                            stock_code=code,
                            start_date=start_date,
                            end_date=end_date,
                        )
                    if adj_factor_df is not None and not adj_factor_df.empty:
                        adj_factor_saved = self.db.save_adj_factor_data(adj_factor_df, code, tushare_source_name)
                        logger.info(
                            f"{stock_name}({code}) 复权因子保存成功（新增 {adj_factor_saved} 条）"
                        )
            except Exception as adj_exc:
                logger.warning(f"{stock_name}({code}) 复权数据补齐失败，但不影响主流程: {adj_exc}")

            return True, None

        except Exception as e:
            error_msg = f"获取/保存数据失败: {str(e)}"
            logger.error(f"{stock_name}({code}) {error_msg}")
            return False, error_msg
    
    def _collect_analysis_inputs(
        self,
        code: str,
        query_id: str,
        current_time: Optional[datetime] = None,
    ) -> _AnalysisInputs:
        """数据收集阶段：名称、行情、日线、筹码、基本面、趋势、持仓。"""
        stock_name = get_cached_stock_name(code) or self.fetcher_manager.get_stock_name(code, allow_realtime=False)

        # Step 1: 实时行情
        realtime_quote = None
        current_price = None
        try:
            if self.config.enable_realtime_quote:
                realtime_quote = self.fetcher_manager.get_realtime_quote(code, log_final_failure=False)
                if realtime_quote:
                    if realtime_quote.name and not get_cached_stock_name(code):
                        stock_name = realtime_quote.name
                    current_price = getattr(realtime_quote, 'current_price', None) or getattr(realtime_quote, 'close', None) or getattr(realtime_quote, 'price', None)
                    volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
                    turnover_rate = getattr(realtime_quote, 'turnover_rate', None)
                    logger.info(f"{stock_name}({code}) 实时行情: 价格={realtime_quote.price}, 量比={volume_ratio}, 换手率={turnover_rate}%")
                else:
                    logger.warning(f"{stock_name}({code}) 所有实时行情数据源均不可用，已降级为历史收盘价继续分析")
            else:
                logger.info(f"{stock_name}({code}) 实时行情已禁用，使用历史收盘价继续分析")
        except Exception as e:
            logger.warning(f"{stock_name}({code}) 实时行情链路异常，已降级为历史收盘价继续分析: {e}")

        if not stock_name or stock_name == code:
            stock_name = get_cached_stock_name(code) or f'股票{code}'

        portfolio_context = self._build_portfolio_context_for_stock(code)

        # Step 2: 日线
        self._ensure_runtime_caches()
        daily_df = None
        daily_source = None
        try:
            cache_target_date = self._resolve_resume_target_date(code, current_time=current_time)
            cached_daily = self._prefetched_daily_data.get(code)
            if cached_daily is not None:
                cached_df, daily_source, cached_target_date = cached_daily
                if cached_target_date == cache_target_date:
                    daily_df = cached_df.copy() if cached_df is not None else None
                    if daily_df is not None and not daily_df.empty:
                        logger.info(f"{stock_name}({code}) 复用预取日线数据: rows={len(daily_df)}, source={daily_source}")
                else:
                    logger.info(f"{stock_name}({code}) 预取日线缓存已过期")
            if daily_df is None or daily_df.empty:
                daily_df, daily_source = self.fetcher_manager.get_daily_data(code, days=120)
                if daily_df is not None and not daily_df.empty:
                    self._prefetched_daily_data[code] = (daily_df.copy(), daily_source, cache_target_date)
                    logger.info(f"{stock_name}({code}) 日线数据预取成功: rows={len(daily_df)}, source={daily_source}")
        except Exception as e:
            logger.warning(f"{stock_name}({code}) 日线数据预取失败: {e}")
            daily_df = None

        # Step 2.1: 筹码
        chip_data = None
        try:
            chip_data = self.fetcher_manager.get_chip_distribution(code, current_price=current_price, daily_df=daily_df)
            if chip_data:
                logger.info(f"{stock_name}({code}) 筹码分布: 获利比例={chip_data.profit_ratio:.1%}, 90%集中度={chip_data.concentration_90:.2%}")
            else:
                logger.debug(f"{stock_name}({code}) 筹码分布获取失败或已禁用")
        except Exception as e:
            logger.warning(f"{stock_name}({code}) 获取筹码分布失败: {e}")

        # Step 2.5: 基本面
        fundamental_context = None
        try:
            fundamental_context = self.fetcher_manager.get_fundamental_context(
                code,
                budget_seconds=getattr(self.config, 'fundamental_stage_timeout_seconds', 1.5),
            )
        except Exception as e:
            logger.warning(f"{stock_name}({code}) 基本面聚合失败: {e}")
            fundamental_context = self.fetcher_manager.build_failed_fundamental_context(code, str(e))

        fundamental_context = self._attach_belong_boards_to_fundamental_context(code, fundamental_context)

        try:
            self.db.save_fundamental_snapshot(
                query_id=query_id,
                code=code,
                payload=fundamental_context,
                source_chain=fundamental_context.get("source_chain", []),
                coverage=fundamental_context.get("coverage", {}),
            )
        except Exception as e:
            logger.debug(f"{stock_name}({code}) 基本面快照写入失败: {e}")

        # Step 3: 趋势
        trend_result: Optional[TrendAnalysisResult] = None
        try:
            _mkt = get_market_for_stock(normalize_stock_code(code))
            end_date = get_market_now(_mkt).date()
            start_date = end_date - timedelta(days=89)
            historical_bars = self.db.get_data_range(code, start_date, end_date)
            if historical_bars:
                df = pd.DataFrame([bar.to_dict() for bar in historical_bars])
                if self.config.enable_realtime_quote and realtime_quote:
                    df = self._augment_historical_with_realtime(df, realtime_quote, code)
                trend_result = self.trend_analyzer.analyze(df, code)
                logger.info(f"{stock_name}({code}) 趋势分析: {trend_result.trend_status.value}, 买入信号={trend_result.buy_signal.value}, 评分={trend_result.signal_score}")
        except Exception as e:
            logger.warning(f"{stock_name}({code}) 趋势分析失败: {e}", exc_info=True)

        return _AnalysisInputs(
            stock_name=stock_name,
            realtime_quote=realtime_quote,
            current_price=current_price,
            daily_df=daily_df,
            daily_source=daily_source,
            chip_data=chip_data,
            fundamental_context=fundamental_context,
            trend_result=trend_result,
            portfolio_context=portfolio_context,
        )

    def _should_use_agent(self) -> bool:
        """判断是否启用 Agent 分析链路。"""
        use_agent = getattr(self.config, 'agent_mode', False)
        if not use_agent:
            configured_skills = getattr(self.config, 'agent_skills', [])
            if configured_skills and configured_skills != ['all']:
                use_agent = True
                logger.info(f"Auto-enabled agent mode due to configured skills: {configured_skills}")
        return use_agent

    def _run_traditional_analysis(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        inputs: _AnalysisInputs,
    ) -> Optional[AnalysisResult]:
        """传统 LLM 分析路径。"""
        stock_name = inputs.stock_name

        # Step 4: 多维度情报搜索
        news_context = None
        self._emit_progress(46, f"{stock_name}：正在检索新闻与舆情")
        if self.search_service is not None:
            capability = self.search_service.get_capability_status()
        else:
            capability = None
        if capability is not None and capability.comprehensive_intel_available:
            logger.info(
                "%s(%s) 开始多维度情报搜索 (legacy=%s, mx_route=%s)...",
                stock_name, code, capability.legacy_available, capability.mx_route_available,
            )
            intel_results = self.search_service.search_comprehensive_intel(
                stock_code=code, stock_name=stock_name, max_searches=5
            )
            if intel_results:
                news_context = self.search_service.format_intel_report(intel_results, stock_name)
                total_results = sum(len(r.results) for r in intel_results.values() if r.success)
                logger.info(f"{stock_name}({code}) 情报搜索完成: 共 {total_results} 条结果")
                try:
                    query_context = self._build_query_context(query_id=query_id)
                    for dim_name, response in intel_results.items():
                        if response and response.success and response.results:
                            self.db.save_news_intel(
                                code=code, name=stock_name, dimension=dim_name,
                                query=response.query, response=response, query_context=query_context
                            )
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) 保存新闻情报失败: {e}")
        else:
            reason_text = capability.reasons if capability is not None else ["search_service_missing"]
            logger.info(f"{stock_name}({code}) 搜索服务不可用，跳过情报搜索: reasons={reason_text}")

        # Step 4.5: Social sentiment
        if self.social_sentiment_service is not None and self.social_sentiment_service.is_available and is_us_stock_code(code):
            try:
                social_context = self.social_sentiment_service.get_social_context(code)
                if social_context:
                    logger.info(f"{stock_name}({code}) Social sentiment data retrieved")
                    news_context = (news_context or "") + "\n\n" + social_context if news_context else social_context
            except Exception as e:
                logger.warning(f"{stock_name}({code}) Social sentiment fetch failed: {e}")

        # Step 5: 获取分析上下文
        self._emit_progress(58, f"{stock_name}：正在整理分析上下文")
        context = self.db.get_analysis_context(code)
        if context is None:
            logger.warning(f"{stock_name}({code}) 无法获取历史行情数据，将仅基于新闻和实时行情分析")
            _mkt_date = get_market_now(get_market_for_stock(normalize_stock_code(code))).date()
            context = {
                'code': code, 'stock_name': stock_name, 'date': _mkt_date.isoformat(),
                'data_missing': True, 'today': {}, 'yesterday': {}
            }

        technical_factor_summary = self._build_technical_factor_summary_for_analysis(
            stock_code=code, daily_data=inputs.daily_df
        )

        # Step 6: 增强上下文
        enhanced_context = self._enhance_context(
            context, inputs.realtime_quote, inputs.chip_data, inputs.trend_result,
            stock_name, inputs.fundamental_context, inputs.portfolio_context,
        )
        if technical_factor_summary is not None:
            enhanced_context['technical_factor_summary'] = technical_factor_summary
        candidate_source = getattr(inputs, "candidate_source", None)
        if candidate_source is not None:
            enhanced_context['candidate_source'] = candidate_source

        candidate_layer_score = self._build_candidate_layer_score(
            code=code,
            stock_name=stock_name,
            inputs=inputs,
        )
        if candidate_layer_score:
            enhanced_context['candidate_layer_score'] = candidate_layer_score

        # Step 7: LLM 分析
        llm_progress_state = {"last_progress": 64}

        def _on_llm_stream(chars_received: int) -> None:
            dynamic_progress = min(92, 64 + min(chars_received // 80, 28))
            if dynamic_progress <= llm_progress_state["last_progress"]:
                return
            llm_progress_state["last_progress"] = dynamic_progress
            self._emit_progress(dynamic_progress, f"{stock_name}：LLM 正在生成分析结果（已接收 {chars_received} 字符）")

        self._emit_progress(64, f"{stock_name}：正在请求 LLM 生成报告")
        result = self.analyzer.analyze(
            enhanced_context,
            news_context=news_context,
            progress_callback=self._emit_progress,
            stream_progress_callback=_on_llm_stream,
        )
        if result is not None:
            result.candidate_layer_score = candidate_layer_score
        normalization_report = normalize_analysis_result(
            result,
            AnalysisNormalizationContext(
                portfolio_context=inputs.portfolio_context,
                require_candidate_layer_score=True,
            ),
        )
        if result is not None:
            result.normalization_report = normalization_report.to_dict()
            logger.info(
                "[%s] normalization summary: changed_rules=%d max_severity=%s reason_codes=%s",
                getattr(result, "code", code),
                normalization_report.changed_rule_count,
                normalization_report.max_severity,
                normalization_report.reason_codes,
            )

        # Step 7.5-7.8: post-processing
        if result:
            self._emit_progress(94, f"{stock_name}：正在校验并整理分析结果")
            result.query_id = query_id
            realtime_data = enhanced_context.get('realtime', {})
            result.current_price = realtime_data.get('price')
            result.change_pct = realtime_data.get('change_pct')

        if result and inputs.chip_data:
            fill_chip_structure_if_needed(result, inputs.chip_data)
        if result and inputs.fundamental_context:
            fill_institution_structure_if_needed(result, inputs.fundamental_context)
        if result:
            fill_price_position_if_needed(result, inputs.trend_result, inputs.realtime_quote)

        # earnings_outlook fallback
        dashboard = getattr(result, "dashboard", None) if result else None
        if result and dashboard and inputs.fundamental_context:
            intel = dashboard.get("intelligence", {})
            outlook = str(intel.get("earnings_outlook", "")).strip()
            if outlook and ("数据缺失" in outlook or "无法判断" in outlook):
                fc = inputs.fundamental_context if isinstance(inputs.fundamental_context, dict) else {}
                ed = fc.get("earnings", {}).get("data", {})
                fr = ed.get("financial_report", {}) if isinstance(ed, dict) else {}
                gd = fc.get("growth", {}).get("data", {})
                if not isinstance(gd, dict):
                    gd = fc.get("growth", {}) if isinstance(fc.get("growth", {}), dict) else {}
                parts = []
                if fr.get("revenue") is not None:
                    parts.append(f"营收{fr['revenue']}")
                if fr.get("net_profit_parent") is not None:
                    parts.append(f"归母净利{fr['net_profit_parent']}")
                if fr.get("roe") is not None:
                    parts.append(f"ROE{fr['roe']}%")
                if fr.get("operating_cash_flow") is not None:
                    parts.append(f"经营现金流{fr['operating_cash_flow']}")
                rev_yoy = gd.get("revenue_yoy")
                if rev_yoy is not None:
                    parts.append(f"营收同比{rev_yoy}%")
                profit_yoy = gd.get("net_profit_yoy") or gd.get("profit_yoy")
                if profit_yoy is not None:
                    parts.append(f"净利同比{profit_yoy}%")
                forecast = ed.get("forecast_summary", "") if isinstance(ed, dict) else ""
                if forecast:
                    parts.append(f"业绩预告: {forecast}")
                express = ed.get("quick_report_summary", "") if isinstance(ed, dict) else ""
                if express:
                    parts.append(f"业绩快报: {express}")
                if parts:
                    intel["earnings_outlook"] = "；".join(parts)
                    result.dashboard["intelligence"] = intel

        # Step 8: 保存历史
        if result and result.success:
            try:
                self._emit_progress(97, f"{stock_name}：正在保存分析报告")
                if result.execution_plan is None:
                    result.execution_plan = build_execution_plan(
                        result.candidate_layer_score,
                        portfolio_snapshot=self._get_cached_portfolio_snapshot(),
                        stock_code=result.code,
                        current_price=result.current_price,
                    )
                context_snapshot = self._build_context_snapshot(
                    enhanced_context=enhanced_context,
                    news_content=news_context,
                    realtime_quote=inputs.realtime_quote,
                    chip_data=inputs.chip_data,
                )
                if result.execution_plan is not None:
                    context_snapshot["execution_plan"] = result.execution_plan
                self.db.save_analysis_history(
                    result=result,
                    query_id=query_id,
                    report_type=report_type.value,
                    news_content=news_context,
                    context_snapshot=context_snapshot,
                    save_snapshot=self.save_context_snapshot,
                )
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 保存分析历史失败: {e}")

        return result

    def analyze_stock(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        current_time: Optional[datetime] = None,
    ) -> Optional[AnalysisResult]:
        """
        分析单只股票（增强版：含量比、换手率、筹码分析、多维度情报）
        
        Returns:
            AnalysisResult 或 None（如果分析失败）
        """
        stock_name = code
        try:
            self._emit_progress(18, f"{code}：正在获取行情与筹码数据")
            inputs = self._collect_analysis_inputs(code, query_id=query_id, current_time=current_time)
            candidate_source_map = getattr(self, "_candidate_source_map", {}) or {}
            candidate_source = candidate_source_map.get(normalize_stock_code(code)) or candidate_source_map.get(code)
            if candidate_source is not None:
                try:
                    setattr(inputs, "candidate_source", candidate_source)
                except Exception:
                    logger.debug("无法设置 candidate_source，跳过")
            stock_name = inputs.stock_name

            self._emit_progress(32, f"{stock_name}：正在聚合基本面与趋势数据")

            if self._should_use_agent():
                logger.info(f"{stock_name}({code}) 启用 Agent 模式进行分析")
                self._emit_progress(58, f"{stock_name}：正在切换 Agent 分析链路")
                candidate_layer_score = self._build_candidate_layer_score(
                    code=code,
                    stock_name=stock_name,
                    inputs=inputs,
                )
                return self._analyze_with_agent(
                    code, report_type, query_id, stock_name,
                    inputs.realtime_quote, inputs.chip_data,
                    inputs.fundamental_context, inputs.trend_result,
                    inputs.portfolio_context,
                    candidate_layer_score=candidate_layer_score,
                    candidate_source=candidate_source,
                )

            return self._run_traditional_analysis(code, report_type, query_id, inputs)

        except Exception as e:
            logger.error(f"{stock_name}({code}) 分析失败: {e}")
            logger.exception(f"{stock_name}({code}) 详细错误信息:")
            return None
    
    def _build_candidate_layer_score(
        self,
        *,
        code: str,
        stock_name: str,
        inputs: _AnalysisInputs,
    ) -> Optional[Dict[str, Any]]:
        """Build deterministic L2 candidate score for report/context only."""
        try:
            scorer = getattr(self, "candidate_scoring_service", None)
            if scorer is None:
                scorer = CandidateScoringService()
                self.candidate_scoring_service = scorer
            score = scorer.score_candidate(
                code=code,
                name=stock_name,
                daily_df=inputs.daily_df,
                trend_result=inputs.trend_result,
                fundamental_context=inputs.fundamental_context,
                realtime_quote=inputs.realtime_quote,
                portfolio_context=inputs.portfolio_context,
            )
            score.assert_required_fields()
            return score.to_dict()
        except Exception as e:
            logger.warning("[%s] 构建 candidate_layer_score 失败（fail-open）: %s", code, e, exc_info=True)
            return None

    def _build_technical_factor_summary_for_analysis(
        self,
        stock_code: str,
        daily_data: Optional[pd.DataFrame] = None,
        target_date: Optional[date] = None,
    ) -> Optional[Dict[str, Any]]:
        """基于 stk_factor_pro 构建单股分析用的技术状态摘要。"""
        try:
            if daily_data is None or daily_data.empty:
                return None

            latest_row = daily_data.iloc[-1]
            close_price = None
            if "close" in latest_row:
                try:
                    close_price = float(latest_row["close"])
                except (TypeError, ValueError):
                    close_price = None

            trade_date_str = None
            if "trade_date" in latest_row:
                raw_trade_date = str(latest_row["trade_date"]).strip()
                trade_date_str = raw_trade_date.replace("-", "")
            elif "date" in latest_row:
                raw_trade_date = str(latest_row["date"]).strip()
                trade_date_str = raw_trade_date.replace("-", "")[:8]

            if target_date is not None and not trade_date_str:
                trade_date_str = target_date.strftime("%Y%m%d")

            if not trade_date_str:
                return None

            tushare_fetcher = self.fetcher_manager.get_fetcher("TushareFetcher")
            if tushare_fetcher is None or not tushare_fetcher.is_available():
                return None

            factor_df = tushare_fetcher.get_stock_factor_snapshot(
                stock_code=stock_code,
                start_date=trade_date_str,
                end_date=trade_date_str,
            )
            if factor_df is None or factor_df.empty:
                return None

            factor_snapshot = factor_df.iloc[-1].to_dict()
            return summarize_stk_factor_snapshot(
                snapshot=factor_snapshot,
                close_price=close_price,
            )
        except Exception as e:
            logger.warning(
                "[%s] 构建 technical_factor_summary 失败（fail-open）: %s",
                stock_code,
                e,
            )
            return None

    def _enhance_context(
        self,
        context: Dict[str, Any],
        realtime_quote,
        chip_data: Optional[ChipDistribution],
        trend_result: Optional[TrendAnalysisResult],
        stock_name: str = "",
        fundamental_context: Optional[Dict[str, Any]] = None,
        portfolio_context: Optional[PortfolioContext] = None,
    ) -> Dict[str, Any]:
        """
        增强分析上下文
        
        将实时行情、筹码分布、趋势分析结果、股票名称添加到上下文中
        
        Args:
            context: 原始上下文
            realtime_quote: 实时行情数据（UnifiedRealtimeQuote 或 None）
            chip_data: 筹码分布数据
            trend_result: 趋势分析结果
            stock_name: 股票名称
            
        Returns:
            增强后的上下文
        """
        enhanced = context.copy()
        enhanced["report_language"] = normalize_report_language(getattr(self.config, "report_language", "zh"))
        if portfolio_context is not None:
            enhanced["portfolio_context"] = portfolio_context.to_dict()
        
        # 添加股票名称
        if stock_name:
            enhanced['stock_name'] = stock_name
        elif realtime_quote and getattr(realtime_quote, 'name', None):
            enhanced['stock_name'] = realtime_quote.name
        elif context.get('stock_name'):
            enhanced['stock_name'] = context.get('stock_name')

        # 将运行时搜索窗口透传给 analyzer，避免与全局配置重新读取产生窗口不一致
        enhanced['news_window_days'] = getattr(self.search_service, "news_window_days", 3)
        
        # 添加实时行情（兼容不同数据源的字段差异）
        if realtime_quote:
            # 使用 getattr 安全获取字段，缺失字段返回 None 或默认值
            volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
            enhanced['realtime'] = {
                'name': getattr(realtime_quote, 'name', ''),
                'price': getattr(realtime_quote, 'price', None),
                'change_pct': getattr(realtime_quote, 'change_pct', None),
                'volume_ratio': volume_ratio,
                'volume_ratio_desc': self._describe_volume_ratio(volume_ratio) if volume_ratio else '无数据',
                'turnover_rate': getattr(realtime_quote, 'turnover_rate', None),
                'pe_ratio': getattr(realtime_quote, 'pe_ratio', None),
                'pb_ratio': getattr(realtime_quote, 'pb_ratio', None),
                'total_mv': getattr(realtime_quote, 'total_mv', None),
                'circ_mv': getattr(realtime_quote, 'circ_mv', None),
                'change_60d': getattr(realtime_quote, 'change_60d', None),
                'source': getattr(realtime_quote, 'source', None),
            }
            # 移除 None 值以减少上下文大小
            enhanced['realtime'] = {k: v for k, v in enhanced['realtime'].items() if v is not None}
        
        # 添加筹码分布（同时写入 data_perspective.chip_structure，供报告层直接消费）
        if chip_data:
            try:
                current_price = getattr(realtime_quote, 'price', 0) if realtime_quote else 0
                chip_status = chip_data.get_chip_status(current_price or 0)
                enhanced['chip'] = {
                    'profit_ratio': chip_data.profit_ratio,
                    'avg_cost': chip_data.avg_cost,
                    'concentration_90': chip_data.concentration_90,
                    'concentration_70': chip_data.concentration_70,
                    'chip_status': chip_status,
                }
                enhanced.setdefault('data_perspective', {})
                enhanced['data_perspective']['chip_structure'] = {
                    'profit_ratio': chip_data.profit_ratio,
                    'avg_cost': chip_data.avg_cost,
                    'concentration': chip_data.concentration_90,
                    'chip_health': chip_status,
                    'source': getattr(chip_data, 'source', 'estimated_ohlcv'),
                    'source_category': 'estimated' if str(getattr(chip_data, 'source', 'estimated_ohlcv')).startswith('estimated') else 'real',
                    'is_estimated': str(getattr(chip_data, 'source', 'estimated_ohlcv')).startswith('estimated'),
                    'data_reliability': 'fallback_estimated' if str(getattr(chip_data, 'source', 'estimated_ohlcv')).startswith('estimated') else 'real_chip',
                    'confidence': getattr(chip_data, 'confidence', None),
                    'method': getattr(chip_data, 'method', 'truncated_gaussian'),
                }
            except Exception as e:
                logger.warning(
                    "[%s] 筹码上下文增强失败，已降级跳过: %s",
                    enhanced.get('code', '-'),
                    e,
                )
        
        # 添加复权数据（最近复权日线 / 复权因子快照）
        adj_snapshot = context.get('adj_snapshot') or {}
        if adj_snapshot:
            enhanced.setdefault('data_perspective', {})
            enhanced['data_perspective']['adj_structure'] = {
                'latest_adj': adj_snapshot.get('latest_adj', {}),
                'latest_adj_factor': adj_snapshot.get('latest_adj_factor'),
                'rows': adj_snapshot.get('rows', 0),
                'adj_source': adj_snapshot.get('adj_source', 'TushareFetcher'),
                'adj_type': adj_snapshot.get('adj_type', 'qfq'),
            }

        mx_summary = None
        try:
            if getattr(self.config, 'mx_enabled', False) and getattr(self, 'candidate_enrichment_service', None):
                mx_summary = self.candidate_enrichment_service.build_report_summary(
                    enhanced.get('code', ''),
                    enhanced.get('stock_name', stock_name),
                )
        except Exception as exc:
            logger.warning("妙想增强层拉取失败: %s", exc)
            mx_summary = None
        if mx_summary:
            enhanced['mx_enrichment'] = mx_summary
            mx_data_summary = mx_summary.get('mx_data_summary') if isinstance(mx_summary, dict) else None
            if mx_data_summary:
                logger.info("[mx-data] %s(%s) summary=%s", enhanced.get('stock_name', stock_name), enhanced.get('code', ''), mx_data_summary)

        # Issue #234: Override today with realtime OHLC + trend MA for intraday analysis
        # Guard: trend_result.ma5 > 0 ensures MA calculation succeeded (data sufficient)
        if realtime_quote and trend_result and trend_result.ma5 > 0:
            price = getattr(realtime_quote, 'price', None)
            if price is not None and price > 0:
                yesterday_close = None
                if enhanced.get('yesterday') and isinstance(enhanced['yesterday'], dict):
                    yesterday_close = enhanced['yesterday'].get('close')
                orig_today = enhanced.get('today') or {}
                open_p = getattr(realtime_quote, 'open_price', None) or getattr(
                    realtime_quote, 'pre_close', None
                ) or yesterday_close or orig_today.get('open') or price
                high_p = getattr(realtime_quote, 'high', None) or price
                low_p = getattr(realtime_quote, 'low', None) or price
                vol = getattr(realtime_quote, 'volume', None)
                amt = getattr(realtime_quote, 'amount', None)
                pct = getattr(realtime_quote, 'change_pct', None)
                realtime_today = {
                    'close': price,
                    'open': open_p,
                    'high': high_p,
                    'low': low_p,
                    'ma5': trend_result.ma5,
                    'ma10': trend_result.ma10,
                    'ma20': trend_result.ma20,
                }
                if vol is not None:
                    realtime_today['volume'] = vol
                if amt is not None:
                    realtime_today['amount'] = amt
                if pct is not None:
                    realtime_today['pct_chg'] = pct
                for k, v in orig_today.items():
                    if k not in realtime_today and v is not None:
                        realtime_today[k] = v
                enhanced['today'] = realtime_today
                enhanced['ma_status'] = self._compute_ma_status(
                    price, trend_result.ma5, trend_result.ma10, trend_result.ma20
                )
                enhanced['date'] = get_market_now(
                    get_market_for_stock(normalize_stock_code(enhanced.get('code', '')))
                ).date().isoformat()
                if yesterday_close is not None:
                    try:
                        yc = float(yesterday_close)
                        if yc > 0:
                            enhanced['price_change_ratio'] = round(
                                (price - yc) / yc * 100, 2
                            )
                    except (TypeError, ValueError):
                        pass
                if vol is not None and enhanced.get('yesterday'):
                    yest_vol = enhanced['yesterday'].get('volume') if isinstance(
                        enhanced['yesterday'], dict
                    ) else None
                    if yest_vol is not None:
                        try:
                            yv = float(yest_vol)
                            if yv > 0:
                                enhanced['volume_change_ratio'] = round(
                                    float(vol) / yv, 2
                                )
                        except (TypeError, ValueError):
                            pass

        # ETF/index flag for analyzer prompt (Fixes #274)
        enhanced['is_index_etf'] = SearchService.is_index_or_etf(
            context.get('code', ''), enhanced.get('stock_name', stock_name)
        )

        # P0: append unified fundamental block; keep as additional context only
        enhanced["fundamental_context"] = (
            fundamental_context
            if isinstance(fundamental_context, dict)
            else self.fetcher_manager.build_failed_fundamental_context(
                context.get("code", ""),
                "invalid fundamental context",
            )
        )
        enhanced["fundamental_quality"] = self._summarize_fundamental_quality(enhanced["fundamental_context"])

        return enhanced

    @staticmethod
    def _summarize_fundamental_quality(fundamental_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarize basic fundamental data availability without changing the raw payload."""
        ctx = fundamental_context if isinstance(fundamental_context, dict) else {}
        coverage = ctx.get("coverage") if isinstance(ctx.get("coverage"), dict) else {}
        quality = {
            "fundamental_data_unavailable": bool(
                ctx.get("status") in {"failed", "not_supported"}
                or coverage.get("valuation") in {"failed", "not_supported"}
            ),
            "earnings_expectation_unavailable": bool(
                ctx.get("status") in {"failed", "not_supported"}
                or coverage.get("earnings") in {"failed", "not_supported"}
            ),
        }
        return quality

    def _attach_belong_boards_to_fundamental_context(
        self,
        code: str,
        fundamental_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Attach A-share board membership as a top-level supplemental field.

        Keep this as a shallow copy so cached fundamental contexts are not
        mutated in place after retrieval.
        """
        if isinstance(fundamental_context, dict):
            enriched_context = dict(fundamental_context)
        else:
            enriched_context = self.fetcher_manager.build_failed_fundamental_context(
                code,
                "invalid fundamental context",
            )

        existing_boards = enriched_context.get("belong_boards")
        if isinstance(existing_boards, list):
            enriched_context["belong_boards"] = list(existing_boards)
            return enriched_context

        boards_block = enriched_context.get("boards")
        boards_status = boards_block.get("status") if isinstance(boards_block, dict) else None
        coverage = enriched_context.get("coverage")
        boards_coverage = coverage.get("boards") if isinstance(coverage, dict) else None
        market = enriched_context.get("market")
        if not isinstance(market, str) or not market.strip():
            market = get_market_for_stock(normalize_stock_code(code))

        if (
            market != "cn"
            or boards_status == "not_supported"
            or boards_coverage == "not_supported"
        ):
            enriched_context["belong_boards"] = []
            return enriched_context

        boards: List[Dict[str, Any]] = []
        try:
            raw_boards = self.fetcher_manager.get_belong_boards(code)
            if isinstance(raw_boards, list):
                boards = raw_boards
        except Exception as e:
            logger.debug("%s attach belong_boards failed (fail-open): %s", code, e)

        enriched_context["belong_boards"] = boards
        return enriched_context

    def _analyze_with_agent(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        stock_name: str,
        realtime_quote,
        chip_data: Optional[ChipDistribution],
        fundamental_context: Optional[Dict[str, Any]] = None,
        trend_result: Optional[TrendAnalysisResult] = None,
        portfolio_context: Optional[PortfolioContext] = None,
        candidate_layer_score: Optional[Dict[str, Any]] = None,
        candidate_source: Optional[Dict[str, Any]] = None,
    ) -> Optional[AnalysisResult]:
        """
        使用 Agent 模式分析单只股票。
        """
        try:
            from src.agent.factory import build_agent_executor
            report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))

            # Build executor from shared factory (ToolRegistry and SkillManager prototype are cached)
            executor = build_agent_executor(self.config, getattr(self.config, 'agent_skills', None) or None)

            # Build initial context to avoid redundant tool calls
            initial_context = {
                "stock_code": code,
                "stock_name": stock_name,
                "report_type": report_type.value,
                "report_language": report_language,
                "fundamental_context": fundamental_context,
            }
            if portfolio_context is not None:
                initial_context["portfolio_context"] = portfolio_context.to_dict()
            if candidate_layer_score is not None:
                initial_context["candidate_layer_score"] = candidate_layer_score
            if candidate_source is not None:
                initial_context["candidate_source"] = candidate_source
            
            if realtime_quote:
                initial_context["realtime_quote"] = self._safe_to_dict(realtime_quote)
            if chip_data:
                initial_context["chip_distribution"] = self._safe_to_dict(chip_data)
            if trend_result:
                initial_context["trend_result"] = self._safe_to_dict(trend_result)

            # Agent path: inject social sentiment as news_context so both
            # executor (_build_user_message) and orchestrator (ctx.set_data)
            # can consume it through the existing news_context channel
            if self.social_sentiment_service is not None and self.social_sentiment_service.is_available and is_us_stock_code(code):
                try:
                    social_context = self.social_sentiment_service.get_social_context(code)
                    if social_context:
                        existing = initial_context.get("news_context")
                        if existing:
                            initial_context["news_context"] = existing + "\n\n" + social_context
                        else:
                            initial_context["news_context"] = social_context
                        logger.info(f"[{code}] Agent mode: social sentiment data injected into news_context")
                except Exception as e:
                    logger.warning(f"[{code}] Agent mode: social sentiment fetch failed: {e}")

            # 运行 Agent
            if report_language == "en":
                message = f"Analyze stock {code} ({stock_name}) and return the full decision dashboard JSON in English."
            else:
                message = f"请分析股票 {code} ({stock_name})，并生成决策仪表盘报告。"
            agent_result = executor.run(message, context=initial_context)

            # 转换为 AnalysisResult
            result = self._agent_result_to_analysis_result(agent_result, code, stock_name, report_type, query_id)
            if result is not None:
                result.candidate_layer_score = candidate_layer_score
            normalization_report = normalize_analysis_result(
                result,
                AnalysisNormalizationContext(
                    portfolio_context=portfolio_context,
                    require_candidate_layer_score=True,
                ),
            )
            if result:
                result.normalization_report = normalization_report.to_dict()
                logger.info(
                    "[%s] normalization summary: changed_rules=%d max_severity=%s reason_codes=%s",
                    result.code,
                    normalization_report.changed_rule_count,
                    normalization_report.max_severity,
                    normalization_report.reason_codes,
                )
                result.query_id = query_id
            # Agent weak integrity: placeholder fill only, no LLM retry
            if result and getattr(self.config, "report_integrity_enabled", False):
                from src.analyzer import check_content_integrity, apply_placeholder_fill

                pass_integrity, missing = check_content_integrity(result)
                if not pass_integrity:
                    apply_placeholder_fill(result, missing)
                    logger.info(
                        "[LLM完整性] integrity_mode=agent_weak 必填字段缺失 %s，已占位补全",
                        missing,
                    )
            # chip_structure fallback (Issue #589), before save_analysis_history
            if result and chip_data:
                fill_chip_structure_if_needed(result, chip_data)

            # institution_structure fallback from fundamental_context
            if result and fundamental_context:
                fill_institution_structure_if_needed(result, fundamental_context)

            # price_position fallback (same as non-agent path Step 7.7)
            if result:
                fill_price_position_if_needed(result, trend_result, realtime_quote)

            resolved_stock_name = result.name if result and result.name else stock_name

            # 保存新闻情报到数据库（Agent 工具结果仅用于 LLM 上下文，未持久化，Fixes #396）
            # 使用 search_stock_news（与 Agent 工具调用逻辑一致），仅 1 次 API 调用，无额外延迟
            if self.search_service is not None and self.search_service.can_search_stock_news(code):
                try:
                    news_response = self.search_service.search_stock_news(
                        stock_code=code,
                        stock_name=resolved_stock_name,
                        max_results=5
                    )
                    if news_response.success and news_response.results:
                        query_context = self._build_query_context(query_id=query_id)
                        self.db.save_news_intel(
                            code=code,
                            name=resolved_stock_name,
                            dimension="latest_news",
                            query=news_response.query,
                            response=news_response,
                            query_context=query_context
                        )
                        logger.info(f"[{code}] Agent 模式: 新闻情报已保存 {len(news_response.results)} 条")
                except Exception as e:
                    logger.warning(f"[{code}] Agent 模式保存新闻情报失败: {e}")

            # 保存分析历史记录
            if result and result.success:
                try:
                    initial_context["stock_name"] = resolved_stock_name
                    if result.execution_plan is None:
                        result.execution_plan = build_execution_plan(
                            result.candidate_layer_score,
                            portfolio_snapshot=self._get_cached_portfolio_snapshot(),
                            stock_code=result.code,
                            current_price=result.current_price,
                        )
                    if result.execution_plan is not None:
                        initial_context["execution_plan"] = result.execution_plan
                    self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=None,
                        context_snapshot=initial_context,
                        save_snapshot=self.save_context_snapshot
                    )
                except Exception as e:
                    logger.warning(f"[{code}] 保存 Agent 分析历史失败: {e}")

            return result

        except Exception as e:
            logger.error(f"[{code}] Agent 分析失败: {e}")
            logger.exception(f"[{code}] Agent 详细错误信息:")
            return None

    def _agent_result_to_analysis_result(
        self, agent_result, code: str, stock_name: str, report_type: ReportType, query_id: str
    ) -> AnalysisResult:
        """
        将 AgentResult 转换为 AnalysisResult。
        """
        report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))
        result = AnalysisResult(
            code=code,
            name=stock_name,
            sentiment_score=50,
            trend_prediction="Unknown" if report_language == "en" else "未知",
            operation_advice="Watch" if report_language == "en" else "观望",
            confidence_level=localize_confidence_level("medium", report_language),
            report_language=report_language,
            success=agent_result.success,
            error_message=agent_result.error or None,
            data_sources=f"agent:{agent_result.provider}",
            model_used=agent_result.model or None,
        )

        if agent_result.success and agent_result.dashboard:
            dash = agent_result.dashboard
            ai_stock_name = str(dash.get("stock_name", "")).strip()
            if ai_stock_name and self._is_placeholder_stock_name(stock_name, code):
                result.name = ai_stock_name
            result.sentiment_score = self._safe_int(dash.get("sentiment_score"), 50)
            result.trend_prediction = dash.get("trend_prediction", "Unknown" if report_language == "en" else "未知")
            raw_advice = dash.get("operation_advice", "Watch" if report_language == "en" else "观望")
            if isinstance(raw_advice, dict):
                # LLM may return {"no_position": "...", "has_position": "..."}
                # Derive a short string from decision_type for the scalar field
                _signal_to_advice = {
                    "buy": "Buy" if report_language == "en" else "买入",
                    "sell": "Sell" if report_language == "en" else "卖出",
                    "hold": "Hold" if report_language == "en" else "持有",
                    "strong_buy": "Strong Buy" if report_language == "en" else "强烈买入",
                    "strong_sell": "Strong Sell" if report_language == "en" else "强烈卖出",
                }
                # Normalize decision_type (strip/lower) before lookup so
                # variants like "BUY" or " Buy " map correctly.
                raw_dt = str(dash.get("decision_type") or "hold").strip().lower()
                result.operation_advice = _signal_to_advice.get(raw_dt, "Watch" if report_language == "en" else "观望")
            else:
                result.operation_advice = str(raw_advice) if raw_advice else ("Watch" if report_language == "en" else "观望")
            from src.agent.protocols import normalize_decision_signal

            result.decision_type = normalize_decision_signal(
                dash.get("decision_type", "hold")
            )
            result.confidence_level = localize_confidence_level(
                dash.get("confidence_level", result.confidence_level),
                report_language,
            )
            result.analysis_summary = dash.get("analysis_summary", "")
            # The AI returns a top-level dict that contains a nested 'dashboard' sub-key
            # with core_conclusion / battle_plan / intelligence.  AnalysisResult's helper
            # methods (get_sniper_points, get_core_conclusion, etc.) expect that inner
            # structure, so we unwrap it here.
            result.dashboard = dash.get("dashboard") or dash
        else:
            result.sentiment_score = 50
            result.operation_advice = "Watch" if report_language == "en" else "观望"
            if not result.error_message:
                result.error_message = "Agent failed to generate a valid decision dashboard" if report_language == "en" else "Agent 未能生成有效的决策仪表盘"

        return result

    @staticmethod
    def _is_placeholder_stock_name(name: str, code: str) -> bool:
        """Return True when the stock name is missing or placeholder-like."""
        if not name:
            return True
        normalized = str(name).strip()
        if not normalized:
            return True
        if normalized == code:
            return True
        if normalized.startswith("股票"):
            return True
        if "Unknown" in normalized:
            return True
        return False

    @staticmethod
    def _safe_int(value: Any, default: int = 50) -> int:
        """安全地将值转换为整数。"""
        if value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            import re
            match = re.search(r'-?\d+', value)
            if match:
                return int(match.group())
        return default
    
    def _describe_volume_ratio(self, volume_ratio: float) -> str:
        """
        量比描述
        
        量比 = 当前成交量 / 过去5日平均成交量
        """
        if volume_ratio < 0.5:
            return "极度萎缩"
        elif volume_ratio < 0.8:
            return "明显萎缩"
        elif volume_ratio < 1.2:
            return "正常"
        elif volume_ratio < 2.0:
            return "温和放量"
        elif volume_ratio < 3.0:
            return "明显放量"
        else:
            return "巨量"

    @staticmethod
    def _compute_ma_status(close: float, ma5: float, ma10: float, ma20: float) -> str:
        """
        Compute MA alignment status from price and MA values.
        Logic mirrors storage._analyze_ma_status (Issue #234).
        """
        close = close or 0
        ma5 = ma5 or 0
        ma10 = ma10 or 0
        ma20 = ma20 or 0
        if close > ma5 > ma10 > ma20 > 0:
            return "多头排列 📈"
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            return "空头排列 📉"
        elif close > ma5 and ma5 > ma10:
            return "短期向好 🔼"
        elif close < ma5 and ma5 < ma10:
            return "短期走弱 🔽"
        else:
            return "震荡整理 ↔️"

    def _augment_historical_with_realtime(
        self, df: pd.DataFrame, realtime_quote: Any, code: str
    ) -> pd.DataFrame:
        """
        Augment historical OHLCV with today's realtime quote for intraday MA calculation.
        Issue #234: Use realtime price instead of yesterday's close for technical indicators.
        """
        if df is None or df.empty or 'close' not in df.columns:
            return df
        if realtime_quote is None:
            return df
        price = getattr(realtime_quote, 'price', None)
        if price is None or not (isinstance(price, (int, float)) and price > 0):
            return df

        # Optional: skip augmentation on non-trading days (fail-open)
        enable_realtime_tech = getattr(
            self.config, 'enable_realtime_technical_indicators', True
        )
        if not enable_realtime_tech:
            return df
        market = get_market_for_stock(code)
        market_today = get_market_now(market).date()
        if market and not is_market_open(market, market_today):
            return df

        last_val = df['date'].max()
        last_date = (
            last_val.date() if hasattr(last_val, 'date') else
            (last_val if isinstance(last_val, date) else pd.Timestamp(last_val).date())
        )
        yesterday_close = float(df.iloc[-1]['close']) if len(df) > 0 else price
        open_p = getattr(realtime_quote, 'open_price', None) or getattr(
            realtime_quote, 'pre_close', None
        ) or yesterday_close
        high_p = getattr(realtime_quote, 'high', None) or price
        low_p = getattr(realtime_quote, 'low', None) or price
        vol = getattr(realtime_quote, 'volume', None) or 0
        amt = getattr(realtime_quote, 'amount', None)
        pct = getattr(realtime_quote, 'change_pct', None)

        if last_date >= market_today:
            # Update last row with realtime close (copy to avoid mutating caller's df)
            df = df.copy()
            idx = df.index[-1]
            df.loc[idx, 'close'] = price
            if open_p is not None:
                df.loc[idx, 'open'] = open_p
            if high_p is not None:
                df.loc[idx, 'high'] = high_p
            if low_p is not None:
                df.loc[idx, 'low'] = low_p
            if vol:
                df.loc[idx, 'volume'] = vol
            if amt is not None:
                df.loc[idx, 'amount'] = amt
            if pct is not None:
                df.loc[idx, 'pct_chg'] = pct
        else:
            # Append virtual today row
            new_row = {
                'code': code,
                'date': market_today,
                'open': open_p,
                'high': high_p,
                'low': low_p,
                'close': price,
                'volume': vol,
                'amount': amt if amt is not None else 0,
                'pct_chg': pct if pct is not None else 0,
            }
            new_df = pd.DataFrame([new_row])
            df = pd.concat([df, new_df], ignore_index=True)
        return df

    def _build_context_snapshot(
        self,
        enhanced_context: Dict[str, Any],
        news_content: Optional[str],
        realtime_quote: Any,
        chip_data: Optional[ChipDistribution]
    ) -> Dict[str, Any]:
        """
        构建分析上下文快照
        """
        snapshot = {
            "enhanced_context": enhanced_context,
            "news_content": news_content,
            "realtime_quote_raw": self._safe_to_dict(realtime_quote),
            "chip_distribution_raw": self._safe_to_dict(chip_data),
        }
        candidate_source = enhanced_context.get("candidate_source") if isinstance(enhanced_context, dict) else None
        if candidate_source is not None:
            snapshot["candidate_source"] = candidate_source
        candidate_source_map = getattr(self, "_candidate_source_map", None)
        if isinstance(candidate_source_map, dict) and candidate_source_map:
            snapshot["candidate_source_map"] = candidate_source_map
            snapshot["l1_candidate_source_map"] = candidate_source_map
        return snapshot

    @staticmethod
    def _resolve_resume_target_date(
        code: str, current_time: Optional[datetime] = None
    ) -> date:
        """
        Resolve the trading date used by checkpoint/resume checks.
        """
        market = get_market_for_stock(normalize_stock_code(code))
        return get_effective_trading_date(market, current_time=current_time)

    @staticmethod
    def _safe_to_dict(value: Any) -> Optional[Dict[str, Any]]:
        """
        安全转换为字典
        """
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            try:
                return value.to_dict()
            except Exception:
                logger.debug("value.to_dict() 失败，返回 None")
                return None
        if hasattr(value, "__dict__"):
            try:
                return dict(value.__dict__)
            except Exception:
                logger.debug("dict(value.__dict__) 失败，返回 None")
                return None
        return None

    def _resolve_query_source(self, query_source: Optional[str]) -> str:
        """
        解析请求来源。

        优先级（从高到低）：
        1. 显式传入的 query_source：调用方明确指定时优先使用，便于覆盖推断结果或兼容未来 source_message 来自非 bot 的场景
        2. 存在 source_message 时推断为 "bot"：当前约定为机器人会话上下文
        3. 存在 query_id 时推断为 "web"：Web 触发的请求会带上 query_id
        4. 默认 "system"：定时任务或 CLI 等无上述上下文时

        Args:
            query_source: 调用方显式指定的来源，如 "bot" / "web" / "cli" / "system"

        Returns:
            归一化后的来源标识字符串，如 "bot" / "web" / "cli" / "system"
        """
        if query_source:
            return query_source
        if self.source_message:
            return "bot"
        if self.query_id:
            return "web"
        return "system"

    def _build_query_context(self, query_id: Optional[str] = None) -> Dict[str, str]:
        """
        生成用户查询关联信息
        """
        effective_query_id = query_id or self.query_id or ""

        context: Dict[str, str] = {
            "query_id": effective_query_id,
            "query_source": self.query_source or "",
        }

        if self.source_message:
            context.update({
                "requester_platform": self.source_message.platform or "",
                "requester_user_id": self.source_message.user_id or "",
                "requester_user_name": self.source_message.user_name or "",
                "requester_chat_id": self.source_message.chat_id or "",
                "requester_message_id": self.source_message.message_id or "",
                "requester_query": self.source_message.content or "",
            })

        return context
    
    def process_single_stock(
        self,
        code: str,
        skip_analysis: bool = False,
        single_stock_notify: bool = False,
        report_type: ReportType = ReportType.SIMPLE,
        analysis_query_id: Optional[str] = None,
        current_time: Optional[datetime] = None,
    ) -> Optional[AnalysisResult]:
        """
        处理单只股票的完整流程

        包括：
        1. 获取数据
        2. 保存数据
        3. AI 分析
        4. 单股推送（可选，#55）

        此方法会被线程池调用，需要处理好异常

        Args:
            analysis_query_id: 查询链路关联 id
            code: 股票代码
            skip_analysis: 是否跳过 AI 分析
            single_stock_notify: 是否启用单股推送模式（每分析完一只立即推送）
            report_type: 报告类型枚举（从配置读取，Issue #119）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断

        Returns:
            AnalysisResult 或 None
        """
        logger.info(f"========== 开始处理 {code} ==========")
        
        try:
            self._emit_progress(12, f"{code}：正在准备分析任务")
            # Step 1: 获取并保存数据
            success, error = self.fetch_and_save_stock_data(
                code, current_time=current_time
            )
            
            if not success:
                logger.warning(f"[{code}] 数据获取失败: {error}")
                # 即使获取失败，也尝试用已有数据分析
            else:
                self._emit_progress(16, f"{code}：行情数据准备完成")
            
            # Step 2: AI 分析
            if skip_analysis:
                logger.info(f"[{code}] 跳过 AI 分析（dry-run 模式）")
                return None
            
            effective_query_id = analysis_query_id or self.query_id or uuid.uuid4().hex
            result = self.analyze_stock(
                code,
                report_type,
                query_id=effective_query_id,
                current_time=current_time,
            )
            
            if result and result.success:
                logger.info(
                    f"[{code}] 分析完成: {result.operation_advice}, "
                    f"评分 {result.sentiment_score}"
                )
                
                # 单股推送模式（#55）：每分析完一只股票立即推送
                if single_stock_notify:
                    self._send_single_stock_notification(
                        result,
                        report_type=report_type,
                        fallback_code=code,
                    )
            elif result:
                logger.warning(
                    f"[{code}] 分析未成功: {result.error_message or '未知错误'}"
                )
            
            return result
            
        except Exception as e:
            # 捕获所有异常，确保单股失败不影响整体
            logger.exception(f"[{code}] 处理过程发生未知异常: {e}")
            return None
    
    def _get_cached_portfolio_snapshot(self) -> Optional[Dict[str, Any]]:
        if getattr(self, "_portfolio_snapshot_loaded", False):
            return self._portfolio_snapshot_cache

        try:
            portfolio_service = PortfolioService()
            snapshot = portfolio_service.get_portfolio_snapshot()
        except Exception as exc:
            logger.info("[pipeline] portfolio snapshot unavailable: %s", exc)
            snapshot = None

        self._portfolio_snapshot_loaded = True
        self._portfolio_snapshot_cache = snapshot
        return snapshot

    def _build_portfolio_context_for_stock(self, code: str) -> PortfolioContext:
        snapshot = self._get_cached_portfolio_snapshot()
        if not isinstance(snapshot, dict):
            return PortfolioContext(has_position=None, source="portfolio_snapshot_unavailable")

        normalized_code = normalize_stock_code(code)
        for account in snapshot.get("accounts", []) or []:
            if not isinstance(account, dict):
                continue
            for position in account.get("positions", []) or []:
                if not isinstance(position, dict):
                    continue
                raw_code = position.get("symbol") or position.get("code")
                if normalize_stock_code(raw_code) != normalized_code:
                    continue
                return PortfolioContext(
                    has_position=True,
                    quantity=position.get("quantity"),
                    cost_basis=position.get("avg_cost"),
                    unrealized_pnl=position.get("unrealized_pnl_base"),
                    valuation_currency=position.get("valuation_currency") or account.get("base_currency"),
                    source="portfolio_snapshot",
                )

        return PortfolioContext(has_position=False, source="portfolio_snapshot")

    def _build_portfolio_context_map(self, results: List[AnalysisResult]) -> Dict[str, Dict[str, Any]]:
        snapshot = self._get_cached_portfolio_snapshot()
        if not isinstance(snapshot, dict):
            return {}

        contexts: Dict[str, Dict[str, Any]] = {}
        for account in snapshot.get("accounts", []) or []:
            if not isinstance(account, dict):
                continue
            for position in account.get("positions", []) or []:
                if not isinstance(position, dict):
                    continue
                raw_code = position.get("symbol") or position.get("code")
                normalized_code = normalize_stock_code(raw_code)
                if not normalized_code or normalized_code in contexts:
                    continue
                contexts[normalized_code] = PortfolioContext(
                    has_position=True,
                    quantity=position.get("quantity"),
                    cost_basis=position.get("avg_cost"),
                    unrealized_pnl=position.get("unrealized_pnl_base"),
                    valuation_currency=position.get("valuation_currency") or account.get("base_currency"),
                    source="portfolio_snapshot",
                ).to_dict()
        return contexts

    def _build_report_quality_context_map(self, results: List[AnalysisResult]) -> Dict[str, Dict[str, Any]]:
        """Build per-stock report quality context for display templates."""
        quality_map: Dict[str, Dict[str, Any]] = {}
        if not results:
            return quality_map

        for result in results:
            dashboard = result.dashboard if isinstance(getattr(result, "dashboard", None), dict) else {}
            data_persp = dashboard.get("data_perspective") or {}
            chip_data = data_persp.get("chip_structure") or {}
            intel = dashboard.get("intelligence") or {}
            market_snapshot = getattr(result, "market_snapshot", None)
            has_real_chip = (
                chip_data.get("source_category") == "real"
                or chip_data.get("data_reliability") == "real_chip"
                or (chip_data.get("source") and not str(chip_data.get("source")).startswith("estimated"))
            )
            has_valid_news = bool(
                getattr(result, "search_performed", False)
                and (
                    getattr(result, "news_summary", None)
                    or getattr(result, "market_sentiment", None)
                    or getattr(result, "hot_topics", None)
                    or intel.get("latest_news")
                )
            )
            has_market_snapshot = bool(market_snapshot)
            fallback_used = bool(
                chip_data.get("is_estimated")
                or chip_data.get("data_reliability") == "fallback_estimated"
                or not has_valid_news
                or not has_market_snapshot
            )
            if has_real_chip and has_valid_news and has_market_snapshot and not fallback_used:
                report_reliability = "high"
            elif has_real_chip or has_valid_news or has_market_snapshot:
                report_reliability = "medium"
            else:
                report_reliability = "low"

            quality_map[result.code] = {
                "report_reliability": report_reliability,
                "fallback_used": fallback_used,
                "has_real_chip": has_real_chip,
                "has_valid_news": has_valid_news,
                "has_market_snapshot": has_market_snapshot,
                "chip_source_category": chip_data.get("source_category"),
                "chip_data_reliability": chip_data.get("data_reliability"),
                "news_source_available": bool(getattr(result, "search_performed", False)),
            }

        return quality_map

    def _build_candidate_score_context_map(self, results: List[AnalysisResult]) -> Dict[str, Dict[str, Any]]:
        """Build per-stock L2 candidate score map for report rendering."""
        score_map: Dict[str, Dict[str, Any]] = {}
        for result in results or []:
            payload = getattr(result, "candidate_layer_score", None)
            if isinstance(payload, dict):
                score_map[result.code] = payload
        return score_map

    def _build_report_decision_context_map(self, results: List[AnalysisResult]) -> Dict[str, Dict[str, Any]]:
        """Build per-stock decision context from existing runtime data.

        This is intentionally derived from runtime/dashboard fields instead of
        extending the LLM schema. The schema remains focused on the analysis
        payload; execution fields live in extra_context for rendering.
        """
        decision_map: Dict[str, Dict[str, Any]] = {}
        if not results:
            return decision_map

        for result in results:
            dashboard = result.dashboard if isinstance(getattr(result, "dashboard", None), dict) else {}
            core = dashboard.get("core_conclusion") or {}
            battle = dashboard.get("battle_plan") or {}
            intel = dashboard.get("intelligence") or {}
            sniper = battle.get("sniper_points") or {}
            position = core.get("position_advice") or {}
            checklist = battle.get("action_checklist") or []
            risk_alerts = intel.get("risk_alerts") or []
            observation_item = checklist[0] if checklist else core.get("time_sensitivity")
            if not observation_item:
                observation_item = "等待确认" if getattr(result, "report_language", "zh") != "en" else "Wait for confirmation"

            decision_map[result.code] = {
                "direction": getattr(result, "trend_prediction", None),
                "action": getattr(result, "operation_advice", None),
                "position_no": position.get("no_position"),
                "position_has": position.get("has_position"),
                "invalidation_condition": sniper.get("stop_loss") or getattr(result, "risk_warning", "") or core.get("one_sentence") or getattr(result, "analysis_summary", ""),
                "right_side_trigger": checklist[0] if checklist else core.get("time_sensitivity") or "",
                "no_trade_reason": getattr(result, "risk_warning", "") or (risk_alerts[0] if risk_alerts else ""),
                "risk_summary": "；".join(str(item) for item in risk_alerts[:2]) if risk_alerts else "",
                "observation_item": observation_item,
                "stop_loss": sniper.get("stop_loss"),
                "take_profit": sniper.get("take_profit"),
                "ideal_buy": sniper.get("ideal_buy"),
                "secondary_buy": sniper.get("secondary_buy"),
            }

        return decision_map

    def _build_execution_plan_context_map(self, results: List[AnalysisResult]) -> Dict[str, Dict[str, Any]]:
        """Build per-stock L3 execution plan map for report rendering."""
        return build_execution_plan_map(results, portfolio_snapshot=self._get_cached_portfolio_snapshot())

    def _build_report_extra_context(self, results: List[AnalysisResult]) -> Dict[str, Any]:
        """Build extra_context shared by render/save/push paths."""
        persisted_execution_plan_map = {
            result.code: result.execution_plan
            for result in (results or [])
            if isinstance(getattr(result, "execution_plan", None), dict)
            and getattr(result, "execution_plan", {}).get("eligible_for_l3")
        }
        return {
            "portfolio_contexts": self._build_portfolio_context_map(results),
            "report_quality_map": self._build_report_quality_context_map(results),
            "candidate_score_map": self._build_candidate_score_context_map(results),
            "execution_plan_map": persisted_execution_plan_map or self._build_execution_plan_context_map(results),
            "report_decision_map": self._build_report_decision_context_map(results),
        }

    def _extract_portfolio_stock_codes(self) -> List[str]:
        """Extract unique stock codes from active portfolio positions."""
        snapshot = self._get_cached_portfolio_snapshot()
        if not isinstance(snapshot, dict):
            return []

        portfolio_codes: List[str] = []
        for account in snapshot.get("accounts", []) or []:
            if not isinstance(account, dict):
                continue
            for position in account.get("positions", []) or []:
                if not isinstance(position, dict):
                    continue
                raw_code = position.get("symbol") or position.get("code")
                normalized = normalize_stock_code(raw_code)
                if normalized and normalized not in portfolio_codes:
                    portfolio_codes.append(normalized)
        return portfolio_codes

    def _build_candidate_pool(
        self,
        stock_codes: List[str],
        *,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Build the final analysis candidate pool.

        Phase 1 keeps the original list intact and only prepares a structured
        envelope so we can safely add mx_xuangu / portfolio pooling later.
        """
        normalized_original: List[str] = []
        for code in stock_codes or []:
            normalized = normalize_stock_code(code)
            if normalized and normalized not in normalized_original:
                normalized_original.append(normalized)

        portfolio_pool = self._extract_portfolio_stock_codes()
        mx_enabled = bool(
            getattr(self.config, "mx_preselect_priority", False)
            and self.mx_client is not None
            and getattr(self.mx_client, "enabled", False)
        )
        mx_reason = "mx_xuangu disabled or unavailable"
        mx_source = "fallback_original"
        mx_candidate_pool = list(normalized_original)
        fallback_used = True
        query_text = (getattr(self.config, "mx_preselect_query", None) or "").strip()
        profile = (getattr(self.config, "mx_preselect_profile", None) or "").strip().lower()
        profile_query = MX_PRESELECT_PROFILES.get(profile) if profile else None

        effective_query = query_text or profile_query or ""
        if effective_query:
            violations = validate_preselect_query(effective_query)
            if violations:
                logger.warning("[pipeline] preselect query validation failed: %s", violations)

        mx_xuangu_pool: List[str] = []
        if mx_enabled and normalized_original:
            try:
                preselect_limit = int(getattr(self.config, "mx_preselect_limit", 50) or 50)
                mx_input_rows = [{"code": code, "name": ""} for code in normalized_original]
                enriched_rows = self.candidate_enrichment_service.enrich_candidates(mx_input_rows)

                hint_rank = {"reject": 0, "neutral": 1, "positive": 2, "strong_positive": 3}
                ranked_rows = []
                for row in enriched_rows:
                    fin = row.get("financial_filter_summary") if isinstance(row, dict) else {}
                    hint = str(fin.get("decision_hint") or "neutral").strip().lower() if isinstance(fin, dict) else "neutral"
                    score = float(row.get("mx_event_score") or 0.0)
                    fin_score = float(fin.get("score") or 0.0) if isinstance(fin, dict) else 0.0
                    valuation = fin.get("valuation_summary") if isinstance(fin, dict) else {}
                    val_score = 0.0
                    if isinstance(valuation, dict):
                        val_score = float(valuation.get("score") or 0.0)
                    combined = score + fin_score * 5.0 + val_score * 2.0
                    if hint == "reject":
                        combined -= 100.0
                    ranked_rows.append((hint_rank.get(hint, 1), combined, row))

                ranked_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)

                for hint_level, combined_score, row in ranked_rows:
                    fin = row.get("financial_filter_summary") if isinstance(row, dict) else {}
                    hint = str(fin.get("decision_hint") or "neutral").strip().lower() if isinstance(fin, dict) else "neutral"
                    if hint == "reject":
                        continue
                    code = normalize_stock_code(row.get("code"))
                    if code and code not in mx_xuangu_pool:
                        mx_xuangu_pool.append(code)
                        if preselect_limit > 0 and len(mx_xuangu_pool) >= preselect_limit:
                            break

                if mx_xuangu_pool:
                    mx_candidate_pool = list(mx_xuangu_pool)
                    mx_reason = (
                        f"mx_xuangu applied with profile={profile or 'default'}; "
                        f"seed={len(normalized_original)}, selected={len(mx_xuangu_pool)}, limit={preselect_limit}"
                    )
                    if profile_query:
                        mx_reason += f"; profile_query={profile_query}"
                    mx_source = "mx_preselect"
                    fallback_used = False
                else:
                    mx_candidate_pool = list(normalized_original)
                    for code in portfolio_pool:
                        if code and code not in mx_candidate_pool:
                            mx_candidate_pool.append(code)
                    mx_reason = f"mx_xuangu empty result; keep original pool, seed={len(normalized_original)}"
                    if profile_query:
                        mx_reason += f"; profile_query={profile_query}"
                    mx_source = "mx_empty_result"
                    fallback_used = False
            except Exception as exc:
                logger.warning("[pipeline] mx_xuangu preselect failed, fallback to original pool: %s", exc)
                mx_candidate_pool = list(normalized_original)
                mx_reason = f"mx_xuangu failed: {exc}"
                mx_source = "fallback_original"
                fallback_used = True

        final_pool: List[str] = []

        for code in mx_candidate_pool + portfolio_pool:
            if code and code not in final_pool:
                final_pool.append(code)

        candidate_source_map: Dict[str, Dict[str, Any]] = {}
        source_profile = "env_query" if query_text else (f"env_profile:{profile}" if profile else "")
        original_rank = {code: idx + 1 for idx, code in enumerate(normalized_original)}
        mx_rank = {code: idx + 1 for idx, code in enumerate(mx_xuangu_pool or mx_candidate_pool)}
        for code in final_pool:
            forced_by_portfolio = code in portfolio_pool
            if forced_by_portfolio:
                candidate_source = "portfolio"
                pool_reason = "portfolio_forced_include"
                rank = None
            elif mx_source == "mx_preselect" and code in mx_xuangu_pool:
                candidate_source = "mx_preselect"
                pool_reason = "mx_xuangu_selected"
                rank = mx_rank.get(code)
            elif mx_source == "mx_empty_result":
                candidate_source = "fallback_original"
                pool_reason = "mx_empty_result_keep_original"
                rank = original_rank.get(code)
            else:
                candidate_source = "fallback_original"
                pool_reason = "mx_unavailable_or_failed"
                rank = original_rank.get(code)
            candidate_source_map[code] = {
                "code": code,
                "candidate_source": candidate_source,
                "source_query": query_text if candidate_source == "mx_preselect" else "",
                "source_profile": source_profile if candidate_source == "mx_preselect" else "",
                "source_rank": rank,
                "pool_reason": pool_reason,
                "forced_by_portfolio": forced_by_portfolio,
                "fallback_used": fallback_used,
                "preselect_rule_set": {
                    "query_text": query_text,
                    "profile": profile,
                    "profile_query": profile_query,
                    "required_tokens": list(MX_PRESELECT_REQUIRED_TOKENS),
                    "exclude_tokens": list(MX_PRESELECT_EXCLUDE_TOKENS),
                } if candidate_source == "mx_preselect" else {},
            }

        logger.info(
            "[pipeline] candidate pool built: original=%d, mx_xuangu=%d, portfolio=%d, final=%d, mx_enabled=%s, fallback=%s, reason=%s",
            len(normalized_original),
            len(mx_candidate_pool),
            len(portfolio_pool),
            len(final_pool),
            mx_enabled,
            fallback_used,
            mx_reason,
        )
        logger.info("[pipeline] original pool: %s", ", ".join(normalized_original) or "<empty>")
        logger.info("[pipeline] mx_xuangu pool: %s", ", ".join(mx_candidate_pool) or "<empty>")
        logger.info("[pipeline] portfolio pool: %s", ", ".join(portfolio_pool) or "<empty>")
        logger.info("[pipeline] final pool: %s", ", ".join(final_pool) or "<empty>")

        return {
            "original_stock_codes": list(normalized_original),
            "mx_xuangu_pool": list(mx_candidate_pool),
            "mx_candidate_pool": list(final_pool),
            "mx_candidate_pool_raw": list(mx_candidate_pool),
            "portfolio_pool": list(portfolio_pool),
            "final_candidate_pool": list(final_pool),
            "mx_xuangu_enabled": mx_enabled,
            "mx_xuangu_reason": mx_reason,
            "mx_xuangu_source": mx_source,
            "portfolio_forced_included": bool(portfolio_pool),
            "candidate_source_map": candidate_source_map,
            "candidate_source_rows": [candidate_source_map[code] for code in final_pool if code in candidate_source_map],
            "fallback_used": fallback_used,
            "dry_run": dry_run,
        }

    def run(
        self,
        stock_codes: Optional[List[str]] = None,
        dry_run: bool = False,
        send_notification: bool = True,
        merge_notification: bool = False
    ) -> List[AnalysisResult]:
        """
        运行完整的分析流程

        流程：
        1. 获取待分析的股票列表
        2. 使用线程池并发处理
        3. 收集分析结果
        4. 发送通知

        Args:
            stock_codes: 股票代码列表（可选，默认使用配置中的自选股）
            dry_run: 是否仅获取数据不分析
            send_notification: 是否发送推送通知
            merge_notification: 是否合并推送（跳过本次推送，由 main 层合并个股+大盘后统一发送，Issue #190）

        Returns:
            分析结果列表
        """
        start_time = time.time()
        
        # 使用配置中的股票列表
        if stock_codes is None:
            self.config.refresh_stock_list()
            stock_codes = self.config.stock_list
        
        if not stock_codes:
            logger.error("未配置自选股列表，请在 .env 文件中设置 STOCK_LIST")
            return []

        candidate_bundle = self._build_candidate_pool(stock_codes, dry_run=dry_run)
        self._candidate_source_map = candidate_bundle.get("candidate_source_map") or {}
        stock_codes = candidate_bundle.get("final_candidate_pool") or []

        if not stock_codes:
            logger.error("候选池为空，请检查 STOCK_LIST / mx-xuangu / 持仓池配置")
            return []
        
        logger.info(f"===== 开始分析 {len(stock_codes)} 只股票 =====")
        logger.info(f"股票列表: {', '.join(stock_codes)}")
        logger.info(f"并发数: {self.max_workers}, 模式: {'仅获取数据' if dry_run else '完整分析'}")
        logger.info(
            "候选池结构: original=%d, mx_xuangu=%d, portfolio=%d, final=%d, fallback=%s",
            len(candidate_bundle.get("original_stock_codes") or []),
            len(candidate_bundle.get("mx_xuangu_pool") or []),
            len(candidate_bundle.get("portfolio_pool") or []),
            len(candidate_bundle.get("final_candidate_pool") or []),
            candidate_bundle.get("fallback_used", False),
        )

        # 冻结本轮运行的统一参考时间，避免跨市场收盘边界时同批股票使用不同目标交易日。
        resume_reference_time = datetime.now(timezone.utc)
        
        # === 批量预取实时行情（优化：避免每只股票都触发全量拉取）===
        # 只有股票数量 >= 5 时才进行预取，少量股票直接逐个查询更高效
        if len(stock_codes) >= 5:
            prefetch_count = self.fetcher_manager.prefetch_realtime_quotes(stock_codes)
            if prefetch_count > 0:
                logger.info(f"已启用批量预取架构：一次拉取全市场数据，{len(stock_codes)} 只股票共享缓存")

        # Issue #455: 预取股票名称，避免并发分析时显示「股票xxxxx」
        # dry_run 仅做数据拉取，不需要名称预取，避免额外网络开销
        if not dry_run:
            self.fetcher_manager.prefetch_stock_names(stock_codes, use_bulk=False)

        # 单股推送模式（#55）：从配置读取
        single_stock_notify = getattr(self.config, 'single_stock_notify', False)
        # Issue #119: 从配置读取报告类型
        report_type_str = getattr(self.config, 'report_type', 'simple').lower()
        if report_type_str == 'brief':
            report_type = ReportType.BRIEF
        elif report_type_str == 'full':
            report_type = ReportType.FULL
        else:
            report_type = ReportType.SIMPLE
        # Issue #128: 从配置读取分析间隔
        analysis_delay = getattr(self.config, 'analysis_delay', 0)

        if single_stock_notify:
            logger.info(
                "已启用单股推送模式：分析仍并发执行，通知改为在结果收集侧串行发送（报告类型: %s）",
                report_type_str,
            )
        
        results: List[AnalysisResult] = []
        
        # 使用线程池并发处理
        # 注意：max_workers 设置较低（默认3）以避免触发反爬
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交任务
            future_to_code = {
                executor.submit(
                    self.process_single_stock,
                    code,
                    skip_analysis=dry_run,
                    single_stock_notify=False,
                    report_type=report_type,  # Issue #119: 传递报告类型
                    analysis_query_id=uuid.uuid4().hex,
                    current_time=resume_reference_time,
                ): code
                for code in stock_codes
            }
            
            # 收集结果
            for idx, future in enumerate(as_completed(future_to_code)):
                code = future_to_code[future]
                try:
                    result = future.result()
                    if result and result.success:
                        results.append(result)
                        if single_stock_notify and send_notification and not dry_run:
                            self._send_single_stock_notification(
                                result,
                                report_type=report_type,
                                fallback_code=code,
                            )
                    elif result and not result.success:
                        logger.warning(
                            f"[{code}] 分析结果标记为失败，不计入汇总: "
                            f"{result.error_message or '未知原因'}"
                        )

                    # Issue #128: 分析间隔 - 在个股分析和大盘分析之间添加延迟
                    if idx < len(stock_codes) - 1 and analysis_delay > 0:
                        # 注意：此 sleep 发生在“主线程收集 future 的循环”中，
                        # 并不会阻止线程池中的任务同时发起网络请求。
                        # 因此它对降低并发请求峰值的效果有限；真正的峰值主要由 max_workers 决定。
                        # 该行为目前保留（按需求不改逻辑）。
                        logger.debug(f"等待 {analysis_delay} 秒后继续下一只股票...")
                        time.sleep(analysis_delay)

                except Exception as e:
                    logger.error(f"[{code}] 任务执行失败: {e}")
        
        # 统计
        elapsed_time = time.time() - start_time
        
        # dry-run 模式下，数据获取成功即视为成功
        if dry_run:
            # 检查哪些股票的最新可复用交易日数据已存在
            success_count = sum(
                1
                for code in stock_codes
                if self.db.has_today_data(
                    code,
                    self._resolve_resume_target_date(
                        code, current_time=resume_reference_time
                    ),
                )
            )
            fail_count = len(stock_codes) - success_count
        else:
            success_count = len(results)
            fail_count = len(stock_codes) - success_count
        
        logger.info("===== 分析完成 =====")
        logger.info(f"成功: {success_count}, 失败: {fail_count}, 耗时: {elapsed_time:.2f} 秒")
        
        # 保存报告到本地文件（无论是否推送通知都保存）
        if results and not dry_run:
            self._save_local_report(
                results,
                report_type,
                extra_context=self._build_report_extra_context(results),
            )

        # 发送通知（单股推送模式下跳过汇总推送，避免重复）
        if results and send_notification and not dry_run:
            if single_stock_notify:
                # 单股推送模式：只保存汇总报告，不再重复推送
                logger.info("单股推送模式：跳过汇总推送，仅保存报告到本地")
                self._send_notifications(results, report_type, skip_push=True)
            elif merge_notification:
                # 合并模式（Issue #190）：仅保存，不推送，由 main 层合并个股+大盘后统一发送
                logger.info("合并推送模式：跳过本次推送，将在个股+大盘复盘后统一发送")
                self._send_notifications(results, report_type, skip_push=True)
            else:
                self._send_notifications(results, report_type)
        
        return results

    def _send_single_stock_notification(
        self,
        result: AnalysisResult,
        report_type: ReportType = ReportType.SIMPLE,
        fallback_code: Optional[str] = None,
    ) -> None:
        """发送单股通知，供直接单股入口和批量串行推送共用。"""
        if not self.notifier.is_available():
            return

        stock_code = getattr(result, "code", None) or fallback_code or "unknown"
        notify_lock = getattr(self, "_single_stock_notify_lock", None)
        if notify_lock is None:
            with _SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD:
                notify_lock = getattr(self, "_single_stock_notify_lock", None)
                if notify_lock is None:
                    notify_lock = threading.Lock()
                    setattr(self, "_single_stock_notify_lock", notify_lock)

        with notify_lock:
            try:
                if report_type == ReportType.FULL:
                    report_content = self.notifier.generate_dashboard_report([result])
                    logger.info(f"[{stock_code}] 使用完整报告格式")
                elif report_type == ReportType.BRIEF:
                    report_content = self.notifier.generate_brief_report([result])
                    logger.info(f"[{stock_code}] 使用简洁报告格式")
                else:
                    report_content = self.notifier.generate_single_stock_report(result)
                    logger.info(f"[{stock_code}] 使用精简报告格式")

                if self.notifier.send(report_content, email_stock_codes=[stock_code]):
                    logger.info(f"[{stock_code}] 单股推送成功")
                else:
                    logger.warning(f"[{stock_code}] 单股推送失败")
            except Exception as e:
                logger.error(f"[{stock_code}] 单股推送异常: {e}")

    def _save_local_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """保存分析报告到本地文件（与通知推送解耦）"""
        try:
            report = self._generate_aggregate_report(results, report_type, extra_context=extra_context)
            filepath = self.notifier.save_report_to_file(report)
            logger.info(f"决策仪表盘日报已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存本地报告失败: {e}")

    def _send_notifications(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
        skip_push: bool = False,
    ) -> None:
        """
        发送分析结果通知
        
        生成决策仪表盘格式的报告
        
        Args:
            results: 分析结果列表
            skip_push: 是否跳过推送（仅保存到本地，用于单股推送模式）
        """
        try:
            logger.info("生成决策仪表盘日报...")
            report = self._generate_aggregate_report(results, report_type, extra_context=self._build_report_extra_context(results))
            
            # 跳过推送（单股推送模式 / 合并模式：报告已由 _save_local_report 保存）
            if skip_push:
                return
            
            # 推送通知
            if self.notifier.is_available():
                channels = self.notifier.get_available_channels()
                context_success = self.notifier.send_to_context(report)

                # Issue #455: Markdown 转图片（与 notification.send 逻辑一致）
                from src.md2img import markdown_to_image

                channels_needing_image = {
                    ch for ch in channels
                    if ch.value in self.notifier._markdown_to_image_channels
                }
                non_wechat_channels_needing_image = {
                    ch for ch in channels_needing_image if ch != NotificationChannel.WECHAT
                }

                def _get_md2img_hint() -> str:
                    try:
                        engine = getattr(get_config(), "md2img_engine", "wkhtmltoimage")
                    except Exception:
                        logger.warning("Broad exception caught", exc_info=True)
                        engine = "wkhtmltoimage"
                    return (
                        "npm i -g markdown-to-file" if engine == "markdown-to-file"
                        else "wkhtmltopdf (apt install wkhtmltopdf / brew install wkhtmltopdf)"
                    )

                image_bytes = None
                if non_wechat_channels_needing_image:
                    image_bytes = markdown_to_image(
                        report, max_chars=self.notifier._markdown_to_image_max_chars
                    )
                    if image_bytes:
                        logger.info(
                            "Markdown 已转换为图片，将向 %s 发送图片",
                            [ch.value for ch in non_wechat_channels_needing_image],
                        )
                    else:
                        logger.warning(
                            "Markdown 转图片失败，将回退为文本发送。请检查 MARKDOWN_TO_IMAGE_CHANNELS 配置并安装 %s",
                            _get_md2img_hint(),
                        )

                # 企业微信：只发精简版（平台限制）
                wechat_success = False
                if NotificationChannel.WECHAT in channels:
                    if report_type == ReportType.BRIEF:
                        dashboard_content = self.notifier.generate_brief_report(results)
                    else:
                        dashboard_content = self.notifier.generate_wechat_dashboard(results)
                    logger.info(f"企业微信仪表盘长度: {len(dashboard_content)} 字符")
                    logger.debug(f"企业微信推送内容:\n{dashboard_content}")
                    wechat_image_bytes = None
                    if NotificationChannel.WECHAT in channels_needing_image:
                        wechat_image_bytes = markdown_to_image(
                            dashboard_content,
                            max_chars=self.notifier._markdown_to_image_max_chars,
                        )
                        if wechat_image_bytes is None:
                            logger.warning(
                                "企业微信 Markdown 转图片失败，将回退为文本发送。请检查 MARKDOWN_TO_IMAGE_CHANNELS 配置并安装 %s",
                                _get_md2img_hint(),
                            )
                    use_image = self.notifier._should_use_image_for_channel(
                        NotificationChannel.WECHAT, wechat_image_bytes
                    )
                    if use_image:
                        wechat_success = self.notifier._send_wechat_image(wechat_image_bytes)
                    else:
                        wechat_success = self.notifier.send_to_wechat(dashboard_content)

                # 其他渠道：发完整报告（避免自定义 Webhook 被 wechat 截断逻辑污染）
                non_wechat_success = False
                stock_email_groups = getattr(self.config, 'stock_email_groups', []) or []
                for channel in channels:
                    if channel == NotificationChannel.WECHAT:
                        continue
                    if channel == NotificationChannel.FEISHU:
                        non_wechat_success = self.notifier.send_to_feishu(report) or non_wechat_success
                    elif channel == NotificationChannel.EMAIL:
                        if stock_email_groups:
                            code_to_emails: Dict[str, Optional[List[str]]] = {}
                            for r in results:
                                if r.code not in code_to_emails:
                                    canonical = normalize_stock_code(r.code)
                                    emails = []
                                    for stocks, emails_list in stock_email_groups:
                                        if canonical in stocks:
                                            emails.extend(emails_list)
                                    code_to_emails[r.code] = list(dict.fromkeys(emails)) if emails else None
                            emails_to_results: Dict[Optional[Tuple], List] = defaultdict(list)
                            for r in results:
                                recs = code_to_emails.get(r.code)
                                key = tuple(recs) if recs else None
                                emails_to_results[key].append(r)
                            for key, group_results in emails_to_results.items():
                                grp_report = self._generate_aggregate_report(group_results, report_type, extra_context=self._build_report_extra_context(group_results))
                                grp_image_bytes = None
                                if channel.value in self.notifier._markdown_to_image_channels:
                                    grp_image_bytes = markdown_to_image(
                                        grp_report,
                                        max_chars=self.notifier._markdown_to_image_max_chars,
                                    )
                                use_image = self.notifier._should_use_image_for_channel(
                                    channel, grp_image_bytes
                                )
                                receivers = list(key) if key is not None else None
                                if use_image:
                                    result = self.notifier._send_email_with_inline_image(
                                        grp_image_bytes, receivers=receivers
                                    )
                                else:
                                    result = self.notifier.send_to_email(
                                        grp_report, receivers=receivers
                                    )
                                non_wechat_success = result or non_wechat_success
                        else:
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, image_bytes
                            )
                            if use_image:
                                result = self.notifier._send_email_with_inline_image(image_bytes)
                            else:
                                result = self.notifier.send_to_email(report)
                            non_wechat_success = result or non_wechat_success
                    elif channel == NotificationChannel.CUSTOM:
                        use_image = self.notifier._should_use_image_for_channel(
                            channel, image_bytes
                        )
                        if use_image:
                            result = self.notifier._send_custom_webhook_image(
                                image_bytes, fallback_content=report
                            )
                        else:
                            result = self.notifier.send_to_custom(report)
                        non_wechat_success = result or non_wechat_success
                    elif channel == NotificationChannel.PUSHPLUS:
                        non_wechat_success = self.notifier.send_to_pushplus(report) or non_wechat_success
                    elif channel == NotificationChannel.SERVERCHAN3:
                        non_wechat_success = self.notifier.send_to_serverchan3(report) or non_wechat_success
                    elif channel == NotificationChannel.PUSHOVER:
                        non_wechat_success = self.notifier.send_to_pushover(report) or non_wechat_success
                    elif channel == NotificationChannel.ASTRBOT:
                        non_wechat_success = self.notifier.send_to_astrbot(report) or non_wechat_success
                    else:
                        logger.warning(f"未知通知渠道: {channel}")

                success = wechat_success or non_wechat_success or context_success
                if success:
                    logger.info("决策仪表盘推送成功")
                else:
                    logger.warning("决策仪表盘推送失败")
            else:
                logger.info("通知渠道未配置，跳过推送")
                
        except Exception as e:
            import traceback
            logger.error(f"发送通知失败: {e}\n{traceback.format_exc()}")

    def _generate_aggregate_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate aggregate report with backward-compatible notifier fallback."""
        generator = getattr(self.notifier, "generate_aggregate_report", None)
        if callable(generator):
            try:
                return generator(results, report_type, extra_context=extra_context)
            except TypeError as exc:
                if "extra_context" not in str(exc):
                    raise
                return generator(results, report_type)
        if report_type == ReportType.BRIEF and hasattr(self.notifier, "generate_brief_report"):
            try:
                return self.notifier.generate_brief_report(results, extra_context=extra_context)
            except TypeError as exc:
                if "extra_context" not in str(exc):
                    raise
                return self.notifier.generate_brief_report(results)
        try:
            return self.notifier.generate_dashboard_report(results, extra_context=extra_context)
        except TypeError as exc:
            if "extra_context" not in str(exc):
                raise
            return self.notifier.generate_dashboard_report(results)
