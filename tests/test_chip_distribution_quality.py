# -*- coding: utf-8 -*-
"""Regression tests for chip distribution source quality and short-circuit behavior."""

import sys
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from data_provider.base import DataFetcherManager
from data_provider.realtime_types import ChipDistribution
from src.analyzer import _build_chip_structure_from_data


@dataclass
class _DummyChipFetcher:
    name: str
    chip: ChipDistribution | None = None
    error: Exception | None = None
    calls: int = 0

    def get_chip_distribution(self, stock_code: str):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.chip


class TestChipStructureQuality(unittest.TestCase):
    def test_build_chip_structure_marks_estimated_source_category(self) -> None:
        chip = ChipDistribution(
            code="600519",
            source="estimated_ohlcv",
            profit_ratio=0.42,
            avg_cost=1700.0,
            concentration_90=0.19,
        )

        out = _build_chip_structure_from_data(chip)

        self.assertEqual(out["source"], "estimated_ohlcv")
        self.assertEqual(out["source_category"], "estimated")
        self.assertTrue(out["is_estimated"])
        self.assertEqual(out["data_reliability"], "fallback_estimated")

    def test_build_chip_structure_marks_real_source_category(self) -> None:
        chip = ChipDistribution(
            code="600519",
            source="tushare_cyq_perf",
            profit_ratio=0.67,
            avg_cost=1850.0,
            concentration_90=0.11,
        )

        out = _build_chip_structure_from_data(chip)

        self.assertEqual(out["source"], "tushare_cyq_perf")
        self.assertEqual(out["source_category"], "real")
        self.assertFalse(out["is_estimated"])
        self.assertEqual(out["data_reliability"], "real_chip")


class TestChipDistributionManagerShortCircuit(unittest.TestCase):
    def _make_manager(self, fetchers):
        manager = DataFetcherManager.__new__(DataFetcherManager)
        manager._fetchers = list(fetchers)
        manager._fetchers_lock = None
        manager._fetcher_call_locks = {}
        manager._fetcher_call_locks_lock = None
        manager._stock_name_cache = {}
        manager._stock_name_cache_lock = None
        manager._temporary_fetcher_method_blocks = {}
        manager._temporary_fetcher_method_blocks_lock = None
        manager._temporary_fetcher_block_seconds = 600.0
        manager._fundamental_cache = {}
        manager._fundamental_cache_lock = None
        manager._fundamental_timeout_worker_limit = 8
        manager._fundamental_timeout_slots = None
        return manager

    def test_akshare_chip_network_failure_short_circuits_subsequent_calls(self) -> None:
        tushare = _DummyChipFetcher(name="TushareFetcher", chip=None)
        akshare = _DummyChipFetcher(name="AkshareFetcher", error=RuntimeError("RemoteDisconnected: closed"))
        manager = self._make_manager([tushare, akshare])

        breaker = MagicMock()
        breaker.is_available.return_value = True
        breaker.record_inconclusive = MagicMock()
        breaker.record_failure = MagicMock()

        with patch("data_provider.realtime_types.get_chip_circuit_breaker", return_value=breaker), patch(
            "src.config.get_config", return_value=MagicMock(enable_chip_distribution=True)
        ), patch("data_provider.base.estimate_chip_distribution", return_value=None), patch.object(
            manager, "get_realtime_quote", return_value=None
        ):
            first = manager.get_chip_distribution("600519")
            second = manager.get_chip_distribution("000001")

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(akshare.calls, 1)
        self.assertIn(("AkshareFetcher", "get_chip_distribution"), manager._temporary_fetcher_method_blocks)

    def test_manager_returns_real_chip_without_estimator_when_tushare_succeeds(self) -> None:
        chip = ChipDistribution(code="600519", source="tushare_cyq_perf", profit_ratio=0.7)
        tushare = _DummyChipFetcher(name="TushareFetcher", chip=chip)
        akshare = _DummyChipFetcher(name="AkshareFetcher", chip=None)
        manager = self._make_manager([tushare, akshare])

        breaker = MagicMock()
        breaker.is_available.return_value = True

        with patch("data_provider.realtime_types.get_chip_circuit_breaker", return_value=breaker), patch(
            "src.config.get_config", return_value=MagicMock(enable_chip_distribution=True)
        ), patch("data_provider.base.estimate_chip_distribution", side_effect=AssertionError("estimator should not run")):
            out = manager.get_chip_distribution("600519")

        self.assertIsNotNone(out)
        self.assertEqual(out.source, "tushare_cyq_perf")
        self.assertEqual(tushare.calls, 1)
        self.assertEqual(akshare.calls, 0)


if __name__ == "__main__":
    unittest.main()
