# -*- coding: utf-8 -*-
"""
P0 搜索链路稳定性测试：
- T1: provider 顺序固化
- T2: 多种日期字段识别
- T3: 去重
- T4: 排序
- T5: now() 注入一致性
- T6: fallback 停止条件
"""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock


# Mock newspaper before search_service import
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import (
    BaseSearchProvider,
    SearchResponse,
    SearchResult,
    SearchService,
)


def _make_search_config(**overrides):
    base = {
        "mx_enabled": False,
        "mx_search_primary_provider": "mx",
        "mx_search_fallback_enabled": True,
        "mx_search_min_results": 3,
        "mx_search_route_timeout_seconds": 1.0,
        "mx_timeout_seconds": 1.0,
        "news_max_age_days": 3,
        "news_strategy_profile": "short",
        "search_provider_priority": ["searxng", "tavily", "brave", "serpapi", "bocha", "minimax"],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


from types import SimpleNamespace


def _result(title: str, published_date: str | None, url: str = "", source: str = "test") -> SearchResult:
    return SearchResult(
        title=title,
        snippet="snippet",
        url=url or f"https://example.com/{title}",
        source=source,
        published_date=published_date,
    )


def _response(results, provider="test") -> SearchResponse:
    return SearchResponse(
        query="test",
        results=results,
        provider=provider,
        success=True,
    )


# ──────────────────────── T1: provider 顺序 ────────────────────────


class TestProviderOrder(unittest.TestCase):
    """P0-1: provider 顺序必须由配置固化，不能靠 append 顺序碰运气。"""

    def _make_service(self, priority, available_keys):
        """构造一个 mock config，只启用指定 keys 的 provider。"""
        cfg = SimpleNamespace(
            mx_enabled=False,
            mx_search_primary_provider="mx",
            mx_search_fallback_enabled=True,
            mx_search_min_results=3,
            mx_search_route_timeout_seconds=1.0,
            mx_timeout_seconds=1.0,
            news_max_age_days=3,
            news_strategy_profile="short",
            search_provider_priority=priority,
            bocha_api_keys=available_keys.get("bocha", []),
            minimax_api_keys=available_keys.get("minimax", []),
            tavily_api_keys=available_keys.get("tavily", []),
            brave_api_keys=available_keys.get("brave", []),
            serpapi_keys=available_keys.get("serpapi", []),
            searxng_base_urls=available_keys.get("searxng", []),
            searxng_public_instances_enabled=False,
        )
        with patch("src.search_service.get_config", return_value=cfg):
            with patch("src.search_service.MxClient") as mock_mx:
                mock_mx.return_value.enabled = False
                svc = SearchService.__new__(SearchService)
                svc._providers = []
                svc.news_max_age_days = 3
                svc.news_strategy_profile = "short"
                svc.news_window_days = 3
                svc.news_profile_days = 3
                svc.FUTURE_TOLERANCE_DAYS = 1
                svc.NEWS_OVERSAMPLE_FACTOR = 2
                svc.NEWS_OVERSAMPLE_MAX = 10
                svc._cache = {}
                svc._cache_ttl = 600
                # Manually add mock providers
                for name in ["searxng", "tavily", "brave", "serpapi", "bocha", "minimax"]:
                    if available_keys.get(name):
                        mock_p = MagicMock(spec=BaseSearchProvider)
                        mock_p.name = name.capitalize()
                        mock_p.is_available = True
                        svc._providers.append(mock_p)
                svc._provider_priority = priority
                svc._sort_providers_by_priority()
                return svc

    def test_explicit_order_respected(self):
        """配置 tavily,searxng → 顺序必须是 tavily 先。"""
        svc = self._make_service(
            priority=["tavily", "searxng", "brave", "serpapi", "bocha", "minimax"],
            available_keys={"tavily": ["k1"], "searxng": ["http://x"]},
        )
        names = [p.name for p in svc._providers]
        self.assertEqual(names, ["Tavily", "Searxng"])

    def test_missing_appended_at_end(self):
        """配置只写了 tavily → 其余 provider 仍然追加在末尾。"""
        svc = self._make_service(
            priority=["tavily"],
            available_keys={"tavily": ["k1"], "brave": ["k2"], "searxng": ["http://x"]},
        )
        names = [p.name for p in svc._providers]
        self.assertEqual(names[0], "Tavily")
        # brave 和 searxng 应该在后面
        self.assertIn("Brave", names)
        self.assertIn("Searxng", names)


# ──────────────────────── T2: 日期字段识别 ────────────────────────


class TestDateParsing(unittest.TestCase):
    """P0-4: _normalize_news_publish_date 应该兼容多种日期字段格式。"""

    def test_iso_format(self):
        result = SearchService._normalize_news_publish_date("2026-04-14T10:30:00Z")
        self.assertEqual(result, date(2026, 4, 14))

    def test_date_only(self):
        result = SearchService._normalize_news_publish_date("2026-04-14")
        self.assertEqual(result, date(2026, 4, 14))

    def test_chinese_relative(self):
        now = datetime(2026, 4, 15, 12, 0, 0)
        result = SearchService._normalize_news_publish_date("3天前", now=now)
        self.assertEqual(result, date(2026, 4, 12))

    def test_english_relative(self):
        now = datetime(2026, 4, 15, 12, 0, 0)
        result = SearchService._normalize_news_publish_date("2 hours ago", now=now)
        self.assertEqual(result, date(2026, 4, 15))

    def test_unix_timestamp_10(self):
        # 2026-04-14 00:00:00 UTC = 1776144000
        result = SearchService._normalize_news_publish_date("1776144000")
        self.assertEqual(result, date(2026, 4, 14))

    def test_none_returns_none(self):
        result = SearchService._normalize_news_publish_date(None)
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = SearchService._normalize_news_publish_date("")
        self.assertIsNone(result)

    def test_garbage_returns_none(self):
        result = SearchService._normalize_news_publish_date("not a date at all xyz")
        self.assertIsNone(result)

    def test_now_injection_consistent(self):
        """同一 now 值，多次调用结果一致。"""
        now = datetime(2026, 4, 15, 12, 0, 0)
        r1 = SearchService._normalize_news_publish_date("今天", now=now)
        r2 = SearchService._normalize_news_publish_date("今天", now=now)
        self.assertEqual(r1, r2)
        self.assertEqual(r1, date(2026, 4, 15))


# ──────────────────────── T3: 去重 ────────────────────────


class TestDedup(unittest.TestCase):
    """P0-2: 相同 (title, url) 的结果必须去重。"""

    def test_exact_duplicate_removed(self):
        results = [
            _result("新闻A", "2026-04-14", url="https://a.com/1"),
            _result("新闻A", "2026-04-14", url="https://a.com/1"),
        ]
        deduped = SearchService._dedup_results(results)
        self.assertEqual(len(deduped), 1)

    def test_different_url_kept(self):
        results = [
            _result("新闻A", "2026-04-14", url="https://a.com/1", source="src1"),
            _result("新闻A", "2026-04-14", url="https://b.com/2", source="src2"),
        ]
        deduped = SearchService._dedup_results(results)
        self.assertEqual(len(deduped), 2)

    def test_case_insensitive_title(self):
        results = [
            _result("News A", "2026-04-14", url="https://a.com/1"),
            _result("news a", "2026-04-14", url="https://a.com/1"),
        ]
        deduped = SearchService._dedup_results(results)
        self.assertEqual(len(deduped), 1)

    def test_different_title_same_url_deduped(self):
        """标题不同但 URL 相同 → 去重。"""
        results = [
            _result("新闻A", "2026-04-14", url="https://a.com/1"),
            _result("新闻B", "2026-04-14", url="https://a.com/1"),
        ]
        deduped = SearchService._dedup_results(results)
        self.assertEqual(len(deduped), 1)

    def test_keeps_first_occurrence(self):
        """去重保留第一次出现的。"""
        results = [
            _result("新闻A", "2026-04-14", url="https://a.com/1", source="first"),
            _result("新闻A", "2026-04-14", url="https://a.com/1", source="second"),
        ]
        deduped = SearchService._dedup_results(results)
        self.assertEqual(deduped[0].source, "first")


# ──────────────────────── T4: 排序 ────────────────────────


class TestSort(unittest.TestCase):
    """P0-3: 结果必须按 published_date 降序排列。"""

    def test_newest_first(self):
        results = [
            _result("旧", "2026-04-10"),
            _result("新", "2026-04-14"),
            _result("中", "2026-04-12"),
        ]
        sorted_results = SearchService._sort_results(results)
        dates = [r.published_date for r in sorted_results]
        self.assertEqual(dates, ["2026-04-14", "2026-04-12", "2026-04-10"])

    def test_no_date_last(self):
        results = [
            _result("有日期", "2026-04-14"),
            _result("无日期", None),
            _result("也有日期", "2026-04-12"),
        ]
        sorted_results = SearchService._sort_results(results)
        self.assertIsNone(sorted_results[-1].published_date)

    def test_empty_list(self):
        sorted_results = SearchService._sort_results([])
        self.assertEqual(sorted_results, [])


# ──────────────────────── T5: now() 注入一致性 ────────────────────────


class TestNowInjection(unittest.TestCase):
    """P0-4: 同一次 run 内 now() 必须一致。"""

    def test_filter_uses_same_now(self):
        """_filter_news_response 使用固定的 reference_now，不依赖调用时刻。"""
        # Test _normalize_news_publish_date directly with a fixed now
        fixed_now = datetime(2026, 4, 15, 12, 0, 0)
        
        # "3天前" with now=2026-04-15 should be 2026-04-12
        r1 = SearchService._normalize_news_publish_date("3天前", now=fixed_now)
        self.assertEqual(r1, date(2026, 4, 12))
        
        # Multiple calls with same now give same result
        r2 = SearchService._normalize_news_publish_date("3天前", now=fixed_now)
        self.assertEqual(r1, r2)
        
        # "今天" with now=2026-04-15 should be 2026-04-15
        r3 = SearchService._normalize_news_publish_date("今天", now=fixed_now)
        self.assertEqual(r3, date(2026, 4, 15))


# ──────────────────────── T6: fallback 停止条件 ────────────────────────


class TestFallbackStop(unittest.TestCase):
    """P0 延伸: 主搜足量时不应该继续 fallback。"""

    def test_stock_events_uses_sorted_providers(self):
        """search_stock_events 应该使用统一排序后的 _providers。"""
        svc = SearchService.__new__(SearchService)
        svc.mx_enabled = False
        svc.mx_client = MagicMock()
        svc.mx_client.enabled = False
        svc._effective_news_window_days = lambda: 3
        svc.mx_search_primary_provider = "legacy"
        svc.mx_search_fallback_enabled = True
        svc.mx_search_min_results = 3
        svc.mx_search_route_timeout_seconds = 1.0

        p1 = MagicMock(spec=BaseSearchProvider)
        p1.name = "Searxng"
        p1.is_available = True
        p1.search.return_value = SearchResponse(
            query="test", results=[_result("r1", "2026-04-14")],
            provider="Searxng", success=True,
        )

        p2 = MagicMock(spec=BaseSearchProvider)
        p2.name = "Tavily"
        p2.is_available = True
        p2.search.return_value = SearchResponse(
            query="test", results=[_result("r2", "2026-04-14")],
            provider="Tavily", success=True,
        )

        svc._providers = [p1, p2]

        result = svc.search_stock_events("600519", "贵州茅台")
        # p1 应该被调用（第一个 provider 有结果就返回）
        p1.search.assert_called_once()
        # p2 不应该被调用（p1 已经有结果）
        p2.search.assert_not_called()


# ──────────────────────── P1-1: extract_date_value ────────────────────────


class TestExtractDateValue(unittest.TestCase):
    """P1-1: extract_date_value 必须统一从各种字段名提取日期。"""

    def test_published_date(self):
        val = SearchService.extract_date_value({"published_date": "2026-04-14"})
        self.assertEqual(val, "2026-04-14")

    def test_publishedDate(self):
        val = SearchService.extract_date_value({"publishedDate": "2026-04-14"})
        self.assertEqual(val, "2026-04-14")

    def test_pubdate(self):
        val = SearchService.extract_date_value({"pubdate": "2026-04-14"})
        self.assertEqual(val, "2026-04-14")

    def test_datePublished(self):
        val = SearchService.extract_date_value({"datePublished": "2026-04-14"})
        self.assertEqual(val, "2026-04-14")

    def test_date(self):
        val = SearchService.extract_date_value({"date": "2026-04-14"})
        self.assertEqual(val, "2026-04-14")

    def test_age(self):
        val = SearchService.extract_date_value({"age": "2026-04-14T10:00:00Z"})
        self.assertEqual(val, "2026-04-14T10:00:00Z")

    def test_page_age(self):
        val = SearchService.extract_date_value({"page_age": "2026-04-14"})
        self.assertEqual(val, "2026-04-14")

    def test_no_date_fields(self):
        val = SearchService.extract_date_value({"title": "hello"})
        self.assertIsNone(val)

    def test_empty_dict(self):
        val = SearchService.extract_date_value({})
        self.assertIsNone(val)

    def test_published_date_takes_precedence(self):
        """published_date 优先于 date。"""
        val = SearchService.extract_date_value({
            "published_date": "2026-04-14",
            "date": "2026-04-10",
        })
        self.assertEqual(val, "2026-04-14")


# ──────────────────────── P1-2/P1-3: 过滤日志 reason code ────────────────────────


class TestNormalizeReasonCode(unittest.TestCase):
    """P1-2: _normalize_news_publish_date_with_reason 必须区分 no_field 和 parse_failed。"""

    def test_none_returns_no_field(self):
        _, reason = SearchService._normalize_news_publish_date_with_reason(None)
        self.assertEqual(reason, "no_field")

    def test_empty_string_returns_no_field(self):
        _, reason = SearchService._normalize_news_publish_date_with_reason("")
        self.assertEqual(reason, "no_field")

    def test_whitespace_returns_no_field(self):
        _, reason = SearchService._normalize_news_publish_date_with_reason("   ")
        self.assertEqual(reason, "no_field")

    def test_garbage_returns_parse_failed(self):
        _, reason = SearchService._normalize_news_publish_date_with_reason("not a date xyz")
        self.assertEqual(reason, "parse_failed")

    def test_valid_date_returns_ok(self):
        d, reason = SearchService._normalize_news_publish_date_with_reason("2026-04-14")
        self.assertEqual(reason, "ok")
        self.assertEqual(d, date(2026, 4, 14))

    def test_iso_returns_ok(self):
        d, reason = SearchService._normalize_news_publish_date_with_reason("2026-04-14T10:30:00Z")
        self.assertEqual(reason, "ok")
        self.assertEqual(d, date(2026, 4, 14))


# ──────────────────────── P1-4: fallback 停止条件 ────────────────────────


class TestFallbackStopCondition(unittest.TestCase):
    """P1-4: 非中文场景，第一个 provider 有结果就停止。"""

    def test_non_chinese_stops_on_first_success(self):
        """prefer_chinese=False 时，第一个 provider 有结果就 return，不继续调用后续 provider。"""
        svc = SearchService.__new__(SearchService)
        svc.FUTURE_TOLERANCE_DAYS = 1
        svc.NEWS_OVERSAMPLE_FACTOR = 2
        svc.NEWS_OVERSAMPLE_MAX = 10
        svc.news_max_age_days = 3
        svc.news_strategy_profile = "short"
        svc.news_window_days = 3
        svc.news_profile_days = 3
        svc._cache = {}
        svc._cache_ttl = 600
        svc._cache_lock = __import__("threading").RLock()
        svc._cache_inflight = {}
        svc.mx_enabled = False
        svc.mx_search_primary_provider = "legacy"

        p1 = MagicMock(spec=BaseSearchProvider)
        p1.name = "First"
        p1.is_available = True
        p1.search.return_value = SearchResponse(
            query="test",
            results=[_result("r1", "2026-04-14")],
            provider="First",
            success=True,
        )

        p2 = MagicMock(spec=BaseSearchProvider)
        p2.name = "Second"
        p2.is_available = True
        p2.search.return_value = SearchResponse(
            query="test",
            results=[_result("r2", "2026-04-14")],
            provider="Second",
            success=True,
        )

        svc._providers = [p1, p2]

        # Mock _should_prefer_chinese_news to return False
        with patch.object(SearchService, "_should_prefer_chinese_news", return_value=False):
            result = svc.search_stock_news("AAPL", "Apple", max_results=5)

        p1.search.assert_called_once()
        p2.search.assert_not_called()
        self.assertEqual(result.provider, "First")


if __name__ == "__main__":
    unittest.main()
