# -*- coding: utf-8 -*-
"""Regression tests for post-merge Tushare follow-up fixes."""

import importlib.util
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    json_repair_available = importlib.util.find_spec("json_repair") is not None
except ValueError:
    json_repair_available = "json_repair" in sys.modules

if not json_repair_available and "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.tushare_fetcher import TushareFetcher


class TestTushareFetcherFollowUps(unittest.TestCase):
    """Cover rate limiting and cross-day trade-calendar refresh behavior."""

    @staticmethod
    def _make_fetcher() -> TushareFetcher:
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = 2
        return fetcher

    def test_get_trade_time_refreshes_trade_calendar_when_day_changes(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.side_effect = [
            pd.DataFrame({"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}),
            pd.DataFrame({"cal_date": ["20260318", "20260317"], "is_open": [1, 1]}),
        ]

        with patch.object(
            fetcher,
            "_get_china_now",
            side_effect=[
                datetime(2026, 3, 17, 20, 0),
                datetime(2026, 3, 17, 20, 0),
                datetime(2026, 3, 18, 20, 0),
                datetime(2026, 3, 18, 20, 0),
            ],
        ), patch.object(fetcher, "_check_rate_limit") as rate_limit_mock:
            self.assertEqual(fetcher.get_trade_time(early_time="00:00", late_time="19:00"), "20260317")
            self.assertEqual(fetcher.get_trade_time(early_time="00:00", late_time="19:00"), "20260318")

        self.assertEqual(fetcher._api.trade_cal.call_count, 2)
        self.assertEqual(rate_limit_mock.call_count, 2)

    def test_daily_data_short_circuits_after_quota_error_for_same_method(self) -> None:
        fetcher = self._make_fetcher()
        quota_error = Exception("抱歉，您每天最多访问该接口200000次，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。")
        fetcher._api.daily.side_effect = quota_error

        with self.assertRaises(Exception) as first_exc:
            fetcher._fetch_raw_data("600519", "2026-03-01", "2026-03-17")
        self.assertIn("配额超限", str(first_exc.exception))
        self.assertEqual(fetcher._api.daily.call_count, 1)

        with self.assertRaises(Exception) as second_exc:
            fetcher._fetch_raw_data("600519", "2026-03-01", "2026-03-17")
        self.assertIn("temporary quota block", str(second_exc.exception).lower())
        self.assertEqual(fetcher._api.daily.call_count, 1)

    def test_daily_data_retries_after_temporary_quota_block_expires(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._temporary_quota_blocks["daily"] = (
            "expired quota block",
            0.0,
        )
        fetcher._api.daily.return_value = pd.DataFrame({"trade_date": ["20260317"], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "vol": [1.0], "amount": [1.0], "pct_chg": [0.0]})

        with patch("data_provider.tushare_fetcher.time.time", return_value=61.0):
            df = fetcher._fetch_raw_data("600519", "2026-03-01", "2026-03-17")

        self.assertIsNotNone(df)
        self.assertEqual(fetcher._api.daily.call_count, 1)

    def test_get_sector_rankings_rate_limits_calendar_and_rankings_api(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.moneyflow_ind_ths.return_value = pd.DataFrame(
            {
                "industry": ["AI", "消费"],
                "pct_change": [1.8, -0.6],
            }
        )

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 16, 0)), patch.object(
            fetcher, "_check_rate_limit"
        ) as rate_limit_mock:
            top, bottom = fetcher.get_sector_rankings(n=1)

        self.assertEqual(top, [{"name": "AI", "change_pct": 1.8}])
        self.assertEqual(bottom, [{"name": "消费", "change_pct": -0.6}])
        self.assertEqual(rate_limit_mock.call_count, 2)

    def test_get_chip_distribution_rate_limits_all_tushare_calls(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.cyq_perf.return_value = pd.DataFrame(
            {
                "trade_date": ["20260317"],
                "cost_5pct": [9.0],
                "cost_15pct": [9.0],
                "cost_85pct": [11.0],
                "cost_95pct": [11.0],
                "weight_avg": [10.1],
                "winner_rate": [70.0],
            }
        )
        fetcher._api.cyq_chips.return_value = pd.DataFrame(
            {
                "price": [9.0, 10.0, 11.0],
                "percent": [20.0, 50.0, 30.0],
            }
        )
        fetcher._api.daily.return_value = pd.DataFrame({"close": [10.5]})

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 20, 0)), patch.object(
            fetcher, "_check_rate_limit"
        ) as rate_limit_mock:
            chip = fetcher.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected chip distribution data")
        self.assertEqual(chip.date, "2026-03-17")
        self.assertAlmostEqual(chip.profit_ratio, 0.7)
        self.assertAlmostEqual(chip.avg_cost, 10.1)
        self.assertAlmostEqual(chip.concentration_90, 0.1)
        self.assertAlmostEqual(chip.concentration_70, 0.1)
        self.assertEqual(chip.source, "tushare_cyq_perf")
        self.assertEqual(rate_limit_mock.call_count, 2)

    def test_get_chip_distribution_prefers_cyq_perf_over_cyq_chips(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.cyq_perf.return_value = pd.DataFrame(
            {
                "trade_date": ["20260317"],
                "cost_5pct": [9.0],
                "cost_15pct": [9.0],
                "cost_85pct": [11.0],
                "cost_95pct": [11.0],
                "weight_avg": [10.1],
                "winner_rate": [70.0],
            }
        )
        fetcher._api.cyq_chips.side_effect = AssertionError("cyq_chips should not be called when cyq_perf has data")
        fetcher._api.daily.return_value = pd.DataFrame({"close": [10.5]})

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 20, 0)):
            chip = fetcher.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected chip distribution data")
        self.assertEqual(chip.source, "tushare_cyq_perf")
        self.assertAlmostEqual(chip.profit_ratio, 0.7)
        self.assertAlmostEqual(chip.avg_cost, 10.1)
        self.assertAlmostEqual(chip.cost_90_low, 9.0)
        self.assertAlmostEqual(chip.cost_90_high, 11.0)
        self.assertAlmostEqual(chip.cost_70_low, 9.0)
        self.assertAlmostEqual(chip.cost_70_high, 11.0)

    def test_get_chip_distribution_falls_back_to_cyq_chips_when_cyq_perf_empty(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.cyq_perf.return_value = pd.DataFrame()
        fetcher._api.cyq_chips.return_value = pd.DataFrame(
            {
                "price": [9.0, 10.0, 11.0],
                "percent": [20.0, 50.0, 30.0],
            }
        )
        fetcher._api.daily.return_value = pd.DataFrame({"close": [10.5]})

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 20, 0)):
            chip = fetcher.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected chip distribution data")
        self.assertEqual(chip.source, "tushare_cyq_chips")
        self.assertAlmostEqual(chip.profit_ratio, 0.7)

    def test_get_chip_distribution_falls_back_to_cyq_chips_when_cyq_perf_missing_fields(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.cyq_perf.return_value = pd.DataFrame(
            {
                "trade_date": ["20260317"],
                "cost_5pct": [9.0],
                "cost_15pct": [9.0],
                "cost_85pct": [11.0],
                "cost_95pct": [11.0],
                "weight_avg": [None],
                "winner_rate": [70.0],
            }
        )
        fetcher._api.cyq_chips.return_value = pd.DataFrame(
            {
                "price": [9.0, 10.0, 11.0],
                "percent": [20.0, 50.0, 30.0],
            }
        )
        fetcher._api.daily.return_value = pd.DataFrame({"close": [10.5]})

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 20, 0)):
            chip = fetcher.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected chip distribution data")
        self.assertEqual(chip.source, "tushare_cyq_chips")
        self.assertAlmostEqual(chip.avg_cost, 10.1)

    def test_get_chip_distribution_falls_back_to_cyq_chips_when_cyq_perf_raises(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.cyq_perf.side_effect = RuntimeError("cyq_perf permission denied")
        fetcher._api.cyq_chips.return_value = pd.DataFrame(
            {
                "price": [9.0, 10.0, 11.0],
                "percent": [20.0, 50.0, 30.0],
            }
        )
        fetcher._api.daily.return_value = pd.DataFrame({"close": [10.5]})

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 20, 0)):
            chip = fetcher.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected chip distribution data")
        self.assertEqual(chip.source, "tushare_cyq_chips")
        self.assertAlmostEqual(chip.profit_ratio, 0.7)

    def test_get_chip_distribution_falls_back_to_cyq_chips_when_cyq_perf_has_invalid_numeric_values(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.cyq_perf.return_value = pd.DataFrame(
            {
                "trade_date": ["20260317"],
                "cost_5pct": ["bad"],
                "cost_15pct": [9.0],
                "cost_85pct": [11.0],
                "cost_95pct": [11.0],
                "weight_avg": [10.1],
                "winner_rate": [70.0],
            }
        )
        fetcher._api.cyq_chips.return_value = pd.DataFrame(
            {
                "price": [9.0, 10.0, 11.0],
                "percent": [20.0, 50.0, 30.0],
            }
        )
        fetcher._api.daily.return_value = pd.DataFrame({"close": [10.5]})

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 20, 0)):
            chip = fetcher.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected chip distribution data")
        self.assertEqual(chip.source, "tushare_cyq_chips")
        self.assertAlmostEqual(chip.avg_cost, 10.1)

    def test_get_chip_distribution_uses_previous_trade_day_before_close(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.cyq_perf.return_value = pd.DataFrame(
            {
                "trade_date": ["20260314"],
                "cost_5pct": [9.0],
                "cost_15pct": [9.0],
                "cost_85pct": [11.0],
                "cost_95pct": [11.0],
                "weight_avg": [10.1],
                "winner_rate": [70.0],
            }
        )
        fetcher._api.cyq_chips.return_value = pd.DataFrame(
            {
                "price": [9.0, 10.0, 11.0],
                "percent": [20.0, 50.0, 30.0],
            }
        )
        fetcher._api.daily.return_value = pd.DataFrame({"close": [10.5]})

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 12, 0)), patch.object(
            fetcher, "_check_rate_limit"
        ) as rate_limit_mock:
            chip = fetcher.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected chip distribution data")
        self.assertEqual(chip.date, "2026-03-14")
        self.assertAlmostEqual(chip.profit_ratio, 0.7)
        self.assertAlmostEqual(chip.avg_cost, 10.1)
        self.assertAlmostEqual(chip.concentration_90, 0.1)
        self.assertAlmostEqual(chip.concentration_70, 0.1)
        self.assertEqual(fetcher._api.cyq_perf.call_args.kwargs["trade_date"], "20260314")
        self.assertEqual(rate_limit_mock.call_count, 2)

    def test_get_market_stats_skips_rt_k_during_intraday(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.daily.reset_mock()
        fetcher._api.stock_basic.reset_mock()
        fetcher._api.rt_k = MagicMock(side_effect=AssertionError("rt_k should not be called intraday"))
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 12, 0)):
            self.assertIsNone(fetcher.get_market_stats())

        fetcher._api.rt_k.assert_not_called()
        fetcher._api.daily.assert_not_called()
        fetcher._api.stock_basic.assert_not_called()

    def test_get_market_stats_uses_daily_after_close(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.daily.return_value = pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "close": [10.5],
                "pre_close": [10.0],
                "amount": [1.0],
            }
        )
        fetcher._api.stock_basic.return_value = pd.DataFrame(
            {"ts_code": ["600519.SH"], "name": ["贵州茅台"]}
        )

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 20, 0)):
            stats = fetcher.get_market_stats()

        self.assertIsNotNone(stats)
        self.assertEqual(fetcher._api.daily.call_args.kwargs["start_date"], "20260317")
        self.assertEqual(fetcher._api.daily.call_args.kwargs["end_date"], "20260317")

    def test_convert_stock_code_accepts_exchange_prefixed_a_share(self) -> None:
        fetcher = self._make_fetcher()

        self.assertEqual(fetcher._convert_stock_code("SZ000001"), "000001.SZ")
        self.assertEqual(fetcher._convert_stock_code("SH600519"), "600519.SH")
        self.assertEqual(fetcher._convert_stock_code("600519.SS"), "600519.SH")

    def test_realtime_quote_returns_none_when_http_quotation_unavailable(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.quotation.side_effect = Exception("quota")

        quote = fetcher.get_realtime_quote("SZ000001")

        self.assertIsNone(quote)
