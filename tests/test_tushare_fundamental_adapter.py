# -*- coding: utf-8 -*-
"""Tests for Tushare-based fundamental adapters."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestTushareFundamentalAdapter(unittest.TestCase):
    def test_tushare_adapter_builds_financial_report_and_event_summaries(self) -> None:
        from data_provider.tushare_fundamental_adapter import TushareFundamentalAdapter

        income_df = pd.DataFrame(
            [
                {
                    "end_date": "20251231",
                    "revenue": 1300.0,
                    "total_revenue": 1300.0,
                    "n_income_attr_p": 180.0,
                    "n_income": 185.0,
                    "basic_eps": 1.25,
                    "operate_profit": 210.0,
                    "total_profit": 215.0,
                    "rd_exp": 66.0,
                }
            ]
        )
        fina_df = pd.DataFrame(
            [
                {
                    "end_date": "20251231",
                    "roe": 12.6,
                    "roe_yearly": 12.6,
                    "tr_yoy": 28.4,
                    "netprofit_yoy": 20.1,
                    "grossprofit_margin": 18.3,
                }
            ]
        )
        cashflow_df = pd.DataFrame([
            {"end_date": "20251231", "n_cashflow_act": 320.0}
        ])
        forecast_df = pd.DataFrame(
            [
                {
                    "end_date": "20260331",
                    "type": "预增",
                    "p_change_min": 15.0,
                    "p_change_max": 25.0,
                    "summary": "预计净利润同比增长15%~25%",
                }
            ]
        )
        express_df = pd.DataFrame(
            [
                {
                    "end_date": "20251231",
                    "revenue": 1280.0,
                    "operate_profit": 200.0,
                    "total_profit": 208.0,
                    "n_income": 178.0,
                    "diluted_eps": 1.22,
                    "diluted_roe": 12.1,
                }
            ]
        )
        disclosure_df = pd.DataFrame(
            [
                {
                    "end_date": "20260331",
                    "pre_date": "20260425",
                    "ann_date": "20260425",
                    "actual_date": None,
                }
            ]
        )

        fetcher = MagicMock()
        fetcher.get_income_df.return_value = income_df
        fetcher.get_fina_indicator_df.return_value = fina_df
        fetcher.get_cashflow_df.return_value = cashflow_df
        fetcher.get_forecast_df.return_value = forecast_df
        fetcher.get_express_df.return_value = express_df
        fetcher.get_disclosure_date_df.return_value = disclosure_df

        adapter = TushareFundamentalAdapter(fetcher=fetcher)
        result = adapter.get_fundamental_bundle("002906")

        self.assertEqual(result["status"], "partial")
        financial_report = result["earnings"]["financial_report"]
        self.assertEqual(financial_report["report_date"], "2025-12-31")
        self.assertEqual(financial_report["revenue"], 1300.0)
        self.assertEqual(financial_report["net_profit_parent"], 180.0)
        self.assertEqual(financial_report["operating_cash_flow"], 320.0)
        self.assertEqual(financial_report["roe"], 12.6)
        self.assertEqual(result["growth"]["revenue_yoy"], 28.4)
        self.assertEqual(result["growth"]["net_profit_yoy"], 20.1)
        self.assertIn("预增", result["earnings"]["forecast_summary"])
        self.assertIn("2025-12-31", result["earnings"]["quick_report_summary"])
        self.assertEqual(
            result["earnings"]["disclosure_date"],
            {
                "report_date": "2026-03-31",
                "pre_date": "2026-04-25",
                "ann_date": "2026-04-25",
                "actual_date": None,
            },
        )
        self.assertIn("financial_report:tushare_income", result["source_chain"])
        self.assertIn("financial_metrics:tushare_fina_indicator", result["source_chain"])
        self.assertIn("earnings_forecast:tushare_forecast", result["source_chain"])
        self.assertIn("earnings_quick:tushare_express", result["source_chain"])
        self.assertIn("disclosure_date:tushare_disclosure_date", result["source_chain"])


class TestCompositeFundamentalAdapter(unittest.TestCase):
    def test_composite_adapter_prefers_tushare_financials_and_uses_secondary_for_institution(self) -> None:
        from data_provider.composite_fundamental_adapter import CompositeFundamentalAdapter

        primary = MagicMock()
        primary.get_fundamental_bundle.return_value = {
            "status": "partial",
            "growth": {"revenue_yoy": 28.4, "roe": 12.6},
            "earnings": {
                "financial_report": {"report_date": "20251231", "revenue": 1300.0},
                "financial_summary": {"report_date": "20251231", "roe": 12.6},
                "forecast_summary": "预增 15%~25%",
            },
            "institution": {},
            "source_chain": ["financial_report:tushare_income"],
            "errors": [],
        }
        secondary = MagicMock()
        secondary.get_fundamental_bundle.return_value = {
            "status": "partial",
            "growth": {"revenue_yoy": 9.9, "roe": 8.8},
            "earnings": {
                "financial_report": {"report_date": "20240930", "revenue": 999.0},
                "quick_report_summary": "AkShare 快报",
            },
            "institution": {"top10_holder_change": 1.2},
            "source_chain": ["institution:akshare_top10"],
            "errors": [],
        }

        adapter = CompositeFundamentalAdapter(primary=primary, secondary=secondary)
        result = adapter.get_fundamental_bundle("002906")

        self.assertEqual(result["earnings"]["financial_report"]["report_date"], "20251231")
        self.assertEqual(result["earnings"]["financial_report"]["revenue"], 1300.0)
        self.assertEqual(result["institution"]["top10_holder_change"], 1.2)
        self.assertEqual(result["earnings"]["quick_report_summary"], "AkShare 快报")
        self.assertIn("financial_report:tushare_income", result["source_chain"])
        self.assertIn("institution:akshare_top10", result["source_chain"])


class TestDataFetcherManagerFundamentalAdapterWiring(unittest.TestCase):
    def test_manager_uses_composite_fundamental_adapter_by_default(self) -> None:
        with patch("data_provider.base.CompositeFundamentalAdapter") as mock_composite, \
             patch("data_provider.base.TushareFundamentalAdapter") as mock_tushare, \
             patch("data_provider.base.AkshareFundamentalAdapter") as mock_akshare:
            from data_provider.base import DataFetcherManager

            manager = DataFetcherManager(fetchers=[])

        mock_tushare.assert_called_once()
        mock_akshare.assert_called_once()
        mock_composite.assert_called_once_with(
            primary=mock_tushare.return_value,
            secondary=mock_akshare.return_value,
            merge_secondary_bundle=False,
        )
        self.assertIs(manager._fundamental_adapter, mock_composite.return_value)
