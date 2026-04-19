# -*- coding: utf-8 -*-

import unittest

import pandas as pd

from data_provider.base import BaseFetcher, DataFetcherManager, DataFetchError


class _EfinanceRemoteDisconnectFetcher(BaseFetcher):
    name = "EfinanceFetcher"
    priority = 0

    def __init__(self):
        self.calls = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls += 1
        raise DataFetchError(
            "efinance 获取数据失败: Eastmoney 历史K线接口失败: "
            "category=remote_disconnect, error_type=ConnectionError, detail=RemoteDisconnected"
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _EfinanceTimeoutFetcher(BaseFetcher):
    name = "EfinanceFetcher"
    priority = 0

    def __init__(self):
        self.calls = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls += 1
        raise DataFetchError(
            "efinance 获取数据失败: Eastmoney 历史K线接口失败: "
            "category=timeout, error_type=ReadTimeout, detail=read timed out"
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _SuccessFetcher(BaseFetcher):
    name = "TushareFetcher"
    priority = 1

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


class TestEfinanceDailyShortCircuit(unittest.TestCase):
    def test_manager_skips_efinance_after_remote_disconnect_in_same_run(self):
        efinance = _EfinanceRemoteDisconnectFetcher()
        tushare = _SuccessFetcher()
        manager = DataFetcherManager(fetchers=[efinance, tushare])

        df1, source1 = manager.get_daily_data("600519", days=5)
        df2, source2 = manager.get_daily_data("000001", days=5)

        self.assertFalse(df1.empty)
        self.assertFalse(df2.empty)
        self.assertEqual(source1, "TushareFetcher")
        self.assertEqual(source2, "TushareFetcher")
        self.assertEqual(efinance.calls, 1)
        self.assertEqual(tushare.calls, 2)

    def test_manager_skips_efinance_after_timeout_in_same_run(self):
        efinance = _EfinanceTimeoutFetcher()
        tushare = _SuccessFetcher()
        manager = DataFetcherManager(fetchers=[efinance, tushare])

        df1, source1 = manager.get_daily_data("600519", days=5)
        df2, source2 = manager.get_daily_data("000001", days=5)

        self.assertFalse(df1.empty)
        self.assertFalse(df2.empty)
        self.assertEqual(source1, "TushareFetcher")
        self.assertEqual(source2, "TushareFetcher")
        self.assertEqual(efinance.calls, 1)
        self.assertEqual(tushare.calls, 2)


if __name__ == "__main__":
    unittest.main()
