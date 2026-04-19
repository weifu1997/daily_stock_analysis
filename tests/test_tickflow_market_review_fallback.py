# -*- coding: utf-8 -*-
"""Regression tests for TickFlow market-review manager fallback."""

import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.base import DataFetcherManager


class _DummyFetcher:
    def __init__(self, name, indices=None, stats=None, delay_seconds=0.0):
        self.name = name
        self.priority = 1
        self.indices = indices
        self.stats = stats
        self.delay_seconds = delay_seconds
        self.index_calls = 0
        self.stats_calls = 0

    def get_main_indices(self, region="cn"):
        self.index_calls += 1
        return self.indices

    def get_market_stats(self):
        self.stats_calls += 1
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        return self.stats


class _DummyTickFlowFetcher:
    def __init__(self, indices=None, stats=None, error=None):
        self.indices = indices
        self.stats = stats
        self.error = error
        self.closed = False

    def get_main_indices(self, region="cn"):
        if self.error is not None:
            raise self.error
        return self.indices

    def get_market_stats(self):
        if self.error is not None:
            raise self.error
        return self.stats

    def close(self):
        self.closed = True


class TestTickFlowMarketReviewFallback(unittest.TestCase):
    def test_manager_prefers_tickflow_indices_when_available(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        fallback = _DummyFetcher("AkshareFetcher", indices=[{"code": "fallback"}])
        manager._fetchers = [fallback]
        manager._get_tickflow_fetcher = lambda: _DummyTickFlowFetcher(
            indices=[{"code": "000001"}]
        )

        data = DataFetcherManager.get_main_indices(manager, region="cn")

        self.assertEqual(data, [{"code": "000001"}])
        self.assertEqual(fallback.index_calls, 0)

    def test_manager_falls_back_when_tickflow_indices_fail(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        fallback = _DummyFetcher("AkshareFetcher", indices=[{"code": "fallback"}])
        manager._fetchers = [fallback]
        manager._get_tickflow_fetcher = lambda: _DummyTickFlowFetcher(
            error=RuntimeError("tickflow down")
        )

        data = DataFetcherManager.get_main_indices(manager, region="cn")

        self.assertEqual(data, [{"code": "fallback"}])
        self.assertEqual(fallback.index_calls, 1)

    def test_manager_falls_back_when_tickflow_indices_missing(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        fallback = _DummyFetcher("AkshareFetcher", indices=[{"code": "fallback"}])
        manager._fetchers = [fallback]
        manager._get_tickflow_fetcher = lambda: _DummyTickFlowFetcher(
            indices=None
        )

        data = DataFetcherManager.get_main_indices(manager, region="cn")

        self.assertEqual(data, [{"code": "fallback"}])
        self.assertEqual(fallback.index_calls, 1)

    def test_manager_skips_tickflow_for_non_cn_indices(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        fallback = _DummyFetcher("YfinanceFetcher", indices=[{"code": "^GSPC"}])
        manager._fetchers = [fallback]
        manager._get_tickflow_fetcher = lambda: self.fail(
            "TickFlow should not be called for non-CN indices"
        )

        data = DataFetcherManager.get_main_indices(manager, region="us")

        self.assertEqual(data, [{"code": "^GSPC"}])
        self.assertEqual(fallback.index_calls, 1)

    def test_manager_falls_back_when_tickflow_market_stats_fails(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        fallback = _DummyFetcher(
            "AkshareFetcher",
            stats={"up_count": 1, "down_count": 2, "flat_count": 3},
        )
        manager._fetchers = [fallback]
        manager._get_tickflow_fetcher = lambda: _DummyTickFlowFetcher(
            error=RuntimeError("tickflow down")
        )

        data = DataFetcherManager.get_market_stats(manager)

        self.assertEqual(data["up_count"], 1)
        self.assertEqual(data["down_count"], 2)
        self.assertEqual(data["flat_count"], 3)
        self.assertEqual(fallback.stats_calls, 1)

    @patch("src.config.get_config")
    def test_manager_skips_tickflow_without_api_key(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(tickflow_api_key=None)

        manager = DataFetcherManager.__new__(DataFetcherManager)
        fallback = _DummyFetcher(
            "AkshareFetcher",
            stats={"up_count": 2, "down_count": 1, "flat_count": 0},
        )
        manager._fetchers = [fallback]

        data = DataFetcherManager.get_market_stats(manager)

        self.assertEqual(data["up_count"], 2)
        self.assertEqual(fallback.stats_calls, 1)

    def test_manager_prefers_non_tushare_before_tushare_for_market_stats(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        efinance = _DummyFetcher(
            "EfinanceFetcher",
            stats={"up_count": 7, "down_count": 1, "flat_count": 2},
        )
        tushare = _DummyFetcher(
            "TushareFetcher",
            stats={"up_count": 1, "down_count": 9, "flat_count": 0},
        )
        manager._fetchers = [tushare, efinance]
        manager._get_tickflow_fetcher = lambda: None

        data = DataFetcherManager.get_market_stats(manager)

        self.assertEqual(data["up_count"], 7)
        self.assertEqual(data["down_count"], 1)
        self.assertEqual(data["flat_count"], 2)
        self.assertEqual(efinance.stats_calls, 1)
        self.assertEqual(tushare.stats_calls, 0)

    @patch("src.config.get_config")
    def test_manager_market_stats_timeout_falls_through_to_next_fetcher(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(
            tickflow_api_key=None,
            market_stats_fetch_timeout_seconds=0.01,
            market_stats_cache_ttl_seconds=0,
        )

        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._ensure_concurrency_guards()
        slow_efinance = _DummyFetcher(
            "EfinanceFetcher",
            stats={"up_count": 99, "down_count": 0, "flat_count": 0},
            delay_seconds=0.05,
        )
        fast_akshare = _DummyFetcher(
            "AkshareFetcher",
            stats={"up_count": 3, "down_count": 4, "flat_count": 5},
        )
        manager._fetchers = [slow_efinance, fast_akshare]
        manager._get_tickflow_fetcher = lambda: None

        data = DataFetcherManager.get_market_stats(manager)

        self.assertEqual(data["up_count"], 3)
        self.assertEqual(data["down_count"], 4)
        self.assertEqual(data["flat_count"], 5)
        self.assertEqual(slow_efinance.stats_calls, 1)
        self.assertEqual(fast_akshare.stats_calls, 1)

    @patch("src.config.get_config")
    def test_manager_market_stats_result_uses_short_ttl_cache(self, mock_get_config):
        mock_get_config.return_value = SimpleNamespace(
            tickflow_api_key=None,
            market_stats_fetch_timeout_seconds=0.5,
            market_stats_cache_ttl_seconds=120,
        )

        manager = DataFetcherManager.__new__(DataFetcherManager)
        first = _DummyFetcher(
            "EfinanceFetcher",
            stats={"up_count": 8, "down_count": 2, "flat_count": 1},
        )
        manager._fetchers = [first]
        manager._get_tickflow_fetcher = lambda: None

        data1 = DataFetcherManager.get_market_stats(manager)
        data2 = DataFetcherManager.get_market_stats(manager)

        self.assertEqual(data1["up_count"], 8)
        self.assertEqual(data2["up_count"], 8)
        self.assertEqual(first.stats_calls, 1)

    def test_manager_close_releases_tickflow_fetcher(self):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        tickflow_fetcher = _DummyTickFlowFetcher(indices=[{"code": "000001"}])
        manager._tickflow_fetcher = tickflow_fetcher
        manager._tickflow_api_key = "tf-secret"
        manager._tickflow_lock = None

        DataFetcherManager.close(manager)

        self.assertTrue(tickflow_fetcher.closed)
        self.assertIsNone(manager._tickflow_fetcher)
        self.assertIsNone(manager._tickflow_api_key)


if __name__ == "__main__":
    unittest.main()
