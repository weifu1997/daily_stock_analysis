# -*- coding: utf-8 -*-

import unittest
from unittest.mock import patch

import pandas as pd

from data_provider.base import BaseFetcher, DataFetcherManager, RateLimitError


class _QuotaFailingFetcher(BaseFetcher):
    name = "TushareFetcher"
    priority = -1

    def __init__(self):
        self.calls = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls += 1
        raise RateLimitError("Tushare 配额超限: 抱歉，您每天最多访问该接口200000次")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _SuccessFetcher(BaseFetcher):
    name = "EfinanceFetcher"
    priority = 0

    def __init__(self):
        self.calls = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls += 1
        return pd.DataFrame(
            [
                {
                    "date": "2026-03-27",
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1,
                    "amount": 1.0,
                    "pct_chg": 0.0,
                }
            ]
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _QuotaThenSuccessFetcher(BaseFetcher):
    name = "TushareFetcher"
    priority = -1

    def __init__(self):
        self.calls = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls += 1
        if self.calls == 1:
            raise RateLimitError("Tushare 配额超限: 抱歉，您每天最多访问该接口200000次")
        return pd.DataFrame(
            [{"date": "2026-03-28", "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 2, "amount": 2.0, "pct_chg": 0.0}]
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _AlwaysSuccessTushareFetcher(BaseFetcher):
    name = "TushareFetcher"
    priority = -1

    def __init__(self):
        self.calls = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls += 1
        return pd.DataFrame(
            [{"date": "2026-03-28", "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 2, "amount": 2.0, "pct_chg": 0.0}]
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _QuotaFailingYfinanceFetcher(BaseFetcher):
    name = "YfinanceFetcher"
    priority = 0

    def __init__(self):
        self.calls = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls += 1
        raise RateLimitError("YFinance rate limit")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _SuccessLongbridgeFetcher(BaseFetcher):
    name = "LongbridgeFetcher"
    priority = 1

    def __init__(self):
        self.calls = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls += 1
        return pd.DataFrame(
            [{"date": "2026-03-28", "open": 3.0, "high": 3.0, "low": 3.0, "close": 3.0, "volume": 3, "amount": 3.0, "pct_chg": 0.0}]
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class TestDataFetcherRateLimitShortCircuit(unittest.TestCase):
    def test_manager_skips_fetcher_after_rate_limit_failure_in_same_run(self):
        tushare = _QuotaFailingFetcher()
        efinance = _SuccessFetcher()
        manager = DataFetcherManager(fetchers=[tushare, efinance])

        df1, source1 = manager.get_daily_data("600519", days=5)
        df2, source2 = manager.get_daily_data("600519", days=5)

        self.assertFalse(df1.empty)
        self.assertFalse(df2.empty)
        self.assertEqual(source1, "EfinanceFetcher")
        self.assertEqual(source2, "EfinanceFetcher")
        self.assertEqual(tushare.calls, 1)
        self.assertEqual(efinance.calls, 2)

    def test_manager_retries_fetcher_after_temporary_block_expires(self):
        tushare = _AlwaysSuccessTushareFetcher()
        efinance = _SuccessFetcher()
        manager = DataFetcherManager(fetchers=[tushare, efinance])
        manager._temporary_fetcher_method_blocks[("TushareFetcher", "get_daily_data")] = (
            "expired quota block",
            0.0,
        )

        with patch("data_provider.base.time.time", return_value=61.0):
            df, source = manager.get_daily_data("600519", days=5)

        self.assertFalse(df.empty)
        self.assertEqual(source, "TushareFetcher")
        self.assertEqual(tushare.calls, 1)

    def test_manager_us_route_skips_fetcher_after_rate_limit_failure_in_same_run(self):
        yfinance = _QuotaFailingYfinanceFetcher()
        longbridge = _SuccessLongbridgeFetcher()
        manager = DataFetcherManager(fetchers=[yfinance, longbridge])

        with patch.object(manager, "_longbridge_preferred", return_value=False):
            df1, source1 = manager.get_daily_data("AAPL", days=5)
            df2, source2 = manager.get_daily_data("AAPL", days=5)

        self.assertFalse(df1.empty)
        self.assertFalse(df2.empty)
        self.assertEqual(source1, "LongbridgeFetcher")
        self.assertEqual(source2, "LongbridgeFetcher")
        self.assertEqual(yfinance.calls, 1)
        self.assertEqual(longbridge.calls, 2)

    def test_manager_skips_all_symbols_after_tushare_daily_rate_limit_in_same_run(self):
        tushare = _QuotaFailingFetcher()
        efinance = _SuccessFetcher()
        manager = DataFetcherManager(fetchers=[tushare, efinance])

        df1, source1 = manager.get_daily_data("600519", days=5)
        df2, source2 = manager.get_daily_data("000001", days=5)

        self.assertFalse(df1.empty)
        self.assertFalse(df2.empty)
        self.assertEqual(source1, "EfinanceFetcher")
        self.assertEqual(source2, "EfinanceFetcher")
        self.assertEqual(tushare.calls, 1)
        self.assertEqual(efinance.calls, 2)


if __name__ == "__main__":
    unittest.main()
