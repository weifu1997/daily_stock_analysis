# -*- coding: utf-8 -*-
"""
Regression tests for Tavily news-mode date mapping (Issue #782).
"""

import sys
import unittest
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_search_config(**overrides) -> SimpleNamespace:
    base = {
        "mx_enabled": False,
        "mx_search_primary_provider": "mx",
        "mx_search_fallback_enabled": True,
        "mx_search_min_results": 3,
        "mx_search_route_timeout_seconds": 1.0,
        "mx_timeout_seconds": 1.0,
        "news_max_age_days": 3,
        "news_strategy_profile": "short",
    }
    base.update(overrides)
    return SimpleNamespace(**base)

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import SearchService, TavilySearchProvider


class _FakeTavilyClient:
    response_payload = {"results": []}
    init_api_keys = []
    search_calls = []

    def __init__(self, api_key=None, **_kwargs):
        type(self).init_api_keys.append(api_key)

    def search(self, **kwargs):
        type(self).search_calls.append(kwargs)
        return type(self).response_payload

    @classmethod
    def reset(cls) -> None:
        cls.response_payload = {"results": []}
        cls.init_api_keys = []
        cls.search_calls = []


def _fake_tavily_module() -> ModuleType:
    module = ModuleType("tavily")
    module.TavilyClient = _FakeTavilyClient
    return module


class TestTavilySearchProvider(unittest.TestCase):
    """Tests for Tavily provider-specific request and mapping behavior."""

    def _patch_tavily(self, payload):
        _FakeTavilyClient.reset()
        _FakeTavilyClient.response_payload = payload
        return patch.dict(sys.modules, {"tavily": _fake_tavily_module()})

    def test_provider_uses_news_topic_when_explicitly_requested(self) -> None:
        published_text = "2026-03-20T09:30:00Z"
        provider = TavilySearchProvider(["dummy_key"])

        with self._patch_tavily(
            {
                "results": [
                    {
                        "title": "Alibaba earnings beat",
                        "url": "https://example.com/alibaba-earnings",
                        "content": "Fresh coverage",
                        "published_date": published_text,
                    },
                ]
            }
        ):
            resp = provider.search("BABA latest news", max_results=5, days=3, topic="news")

        self.assertTrue(resp.success)
        self.assertEqual(_FakeTavilyClient.init_api_keys, ["dummy_key"])
        self.assertEqual(len(_FakeTavilyClient.search_calls), 1)
        self.assertEqual(_FakeTavilyClient.search_calls[0]["topic"], "news")
        self.assertEqual(_FakeTavilyClient.search_calls[0]["days"], 3)
        self.assertEqual(_FakeTavilyClient.search_calls[0]["max_results"], 5)
        self.assertEqual(_FakeTavilyClient.search_calls[0]["search_depth"], "advanced")
        self.assertEqual(len(resp.results), 1)
        self.assertEqual(resp.results[0].published_date, published_text)
        self.assertEqual(resp.results[0].url, "https://example.com/alibaba-earnings")

    def test_provider_opens_circuit_on_quota_error_and_skips_followup_requests(self) -> None:
        provider = TavilySearchProvider(["dummy_key"])

        def _quota_error(**kwargs):
            _FakeTavilyClient.search_calls.append(kwargs)
            raise Exception("This request exceeds your plan's set usage limit")

        with self._patch_tavily({"results": []}), patch.object(
            _FakeTavilyClient,
            "search",
            side_effect=_quota_error,
        ):
            first = provider.search("BABA latest news", max_results=5, days=3, topic="news")
            second = provider.search("BABA latest news", max_results=5, days=3, topic="news")

        self.assertFalse(first.success)
        self.assertIn("API 配额已用尽", first.error_message)
        self.assertFalse(provider.is_available)
        self.assertFalse(second.success)
        self.assertEqual(second.error_message, "tavily_circuit_open")
        self.assertEqual(len(_FakeTavilyClient.search_calls), 1)

    def test_provider_recovers_after_manual_cooldown_expiry(self) -> None:
        provider = TavilySearchProvider(["dummy_key"])
        provider._consecutive_failures = provider._CB_FAILURE_THRESHOLD
        provider._circuit_open_until = 0.0

        with self._patch_tavily(
            {
                "results": [
                    {
                        "title": "Recovered article",
                        "url": "https://example.com/recovered",
                        "content": "Fresh coverage",
                        "published_date": "2026-03-20T09:30:00Z",
                    }
                ]
            }
        ):
            resp = provider.search("BABA latest news", max_results=5, days=3, topic="news")

        self.assertTrue(provider.is_available)
        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)

    def test_non_news_search_paths_do_not_force_news_topic(self) -> None:
        provider = TavilySearchProvider(["dummy_key"])

        with self._patch_tavily(
            {
                "results": [
                    {
                        "title": "Alibaba price action",
                        "url": "https://example.com/alibaba-price",
                        "content": "General search result",
                    }
                ]
            }
        ):
            resp = provider.search("BABA stock price", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(len(_FakeTavilyClient.search_calls), 1)
        self.assertNotIn("topic", _FakeTavilyClient.search_calls[0])

    def test_search_stock_news_keeps_tavily_results_with_supported_date_fields(self) -> None:
        published_dt = datetime.now(timezone.utc).replace(microsecond=0)
        published_text = published_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        expected_date = published_dt.astimezone().date().isoformat()

        for field_name in ("published_date", "publishedDate"):
            with self.subTest(field_name=field_name):
                with self._patch_tavily(
                    {
                        "results": [
                            {
                                "title": f"Fresh article via {field_name}",
                                "url": "https://example.com/fresh-article",
                                "content": "Fresh coverage",
                                field_name: published_text,
                            }
                        ]
                    }
                ), patch("src.search_service.get_config", return_value=_make_search_config(news_max_age_days=3, news_strategy_profile="short")):
                    service = SearchService(
                        tavily_keys=["dummy_key"],
                        searxng_public_instances_enabled=False,
                        news_max_age_days=3,
                        news_strategy_profile="short",
                    )
                    resp = service.search_stock_news("BABA", "阿里巴巴", max_results=3)

                self.assertTrue(resp.success)
                self.assertEqual(len(resp.results), 1)
                self.assertEqual(resp.results[0].published_date, expected_date)
                self.assertEqual(_FakeTavilyClient.search_calls[0]["topic"], "news")

    def test_search_stock_events_does_not_force_news_topic(self) -> None:
        with self._patch_tavily(
            {
                "results": [
                    {
                        "title": "Alibaba quarterly results",
                        "url": "https://example.com/alibaba-event",
                        "content": "Event coverage",
                    }
                ]
            }
        ), patch("src.search_service.get_config", return_value=_make_search_config()):
            service = SearchService(
                tavily_keys=["dummy_key"],
                searxng_public_instances_enabled=False,
            )
            resp = service.search_stock_events("BABA", "阿里巴巴")

        self.assertTrue(resp.success)
        self.assertEqual(len(resp.results), 1)
        self.assertNotIn("topic", _FakeTavilyClient.search_calls[0])

    def test_search_comprehensive_intel_uses_dimension_specific_topic_for_tavily(self) -> None:
        published_dt = datetime.now(timezone.utc).replace(microsecond=0)
        published_text = published_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._patch_tavily(
            {
                "results": [
                    {
                        "title": "Alibaba intel article",
                        "url": "https://example.com/alibaba-intel",
                        "content": "Recent intel",
                        "published_date": published_text,
                    }
                ]
            }
        ), patch("src.search_service.get_config", return_value=_make_search_config(news_max_age_days=3, news_strategy_profile="short")):
            service = SearchService(
                tavily_keys=["dummy_key"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )
            intel = service.search_comprehensive_intel("BABA", "阿里巴巴", max_searches=2)

        self.assertIn("latest_news", intel)
        self.assertIn("market_analysis", intel)
        self.assertGreaterEqual(len(_FakeTavilyClient.search_calls), 2)
        self.assertEqual(_FakeTavilyClient.search_calls[0]["topic"], "news")
        self.assertNotIn("topic", _FakeTavilyClient.search_calls[1])

    def test_search_comprehensive_intel_etf_risk_check_does_not_force_news_topic(self) -> None:
        published_dt = datetime.now(timezone.utc).replace(microsecond=0)
        published_text = published_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._patch_tavily(
            {
                "results": [
                    {
                        "title": "ETF intel article",
                        "url": "https://example.com/etf-intel",
                        "content": "Recent ETF coverage",
                        "published_date": published_text,
                    }
                ]
            }
        ), patch("src.search_service.get_config", return_value=_make_search_config(news_max_age_days=3, news_strategy_profile="short")):
            service = SearchService(
                tavily_keys=["dummy_key"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )
            intel = service.search_comprehensive_intel("510300", "沪深300ETF", max_searches=3)

        self.assertIn("latest_news", intel)
        self.assertIn("market_analysis", intel)
        self.assertIn("risk_check", intel)
        self.assertGreaterEqual(len(_FakeTavilyClient.search_calls), 3)
        self.assertEqual(_FakeTavilyClient.search_calls[0]["topic"], "news")
        self.assertNotIn("topic", _FakeTavilyClient.search_calls[1])
        self.assertNotIn("topic", _FakeTavilyClient.search_calls[2])

    def test_search_comprehensive_intel_non_etf_risk_check_stays_in_news_topic(self) -> None:
        published_dt = datetime.now(timezone.utc).replace(microsecond=0)
        published_text = published_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._patch_tavily(
            {
                "results": [
                    {
                        "title": "Moutai intel article",
                        "url": "https://example.com/moutai-intel",
                        "content": "Recent non-ETF coverage",
                        "published_date": published_text,
                    }
                ]
            }
        ), patch("src.search_service.get_config", return_value=_make_search_config(news_max_age_days=3, news_strategy_profile="short")):
            service = SearchService(
                tavily_keys=["dummy_key"],
                searxng_public_instances_enabled=False,
                news_max_age_days=3,
                news_strategy_profile="short",
            )
            intel = service.search_comprehensive_intel("600519", "贵州茅台", max_searches=3)

        self.assertIn("latest_news", intel)
        self.assertIn("market_analysis", intel)
        self.assertIn("risk_check", intel)
        self.assertGreaterEqual(len(_FakeTavilyClient.search_calls), 3)
        self.assertEqual(_FakeTavilyClient.search_calls[0]["topic"], "news")
        self.assertNotIn("topic", _FakeTavilyClient.search_calls[1])
        self.assertEqual(_FakeTavilyClient.search_calls[2]["topic"], "news")


if __name__ == "__main__":
    unittest.main()
