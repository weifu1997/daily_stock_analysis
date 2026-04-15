# -*- coding: utf-8 -*-
"""Regression tests for chip distribution fetcher name aliases."""

from dataclasses import dataclass
import unittest
from unittest.mock import MagicMock, patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from data_provider.base import DataFetcherManager
from data_provider.realtime_types import ChipDistribution


@dataclass
class _DummyFetcher:
    name: str
    chip: ChipDistribution
    calls: list

    def get_chip_distribution(self, stock_code: str) -> ChipDistribution:
        self.calls.append(stock_code)
        return self.chip


class TestChipDistributionManagerAlias(unittest.TestCase):
    def test_akshare_fetcher_name_alias_is_resolved(self) -> None:
        chip = ChipDistribution(code="600519", date="2026-03-14", source="akshare", profit_ratio=0.7)
        for fetcher_name in ("AkshareFetcher", "AkShareFetcher"):
            with self.subTest(fetcher_name=fetcher_name):
                manager = DataFetcherManager.__new__(DataFetcherManager)
                dummy = _DummyFetcher(name=fetcher_name, chip=chip, calls=[])
                manager._fetchers = [dummy]

                breaker = MagicMock()
                breaker.is_available.return_value = True
                with patch("data_provider.realtime_types.get_chip_circuit_breaker", return_value=breaker):
                    out = manager.get_chip_distribution("600519")

                self.assertIsNotNone(out)
                self.assertEqual(out.code, "600519")
                self.assertEqual(dummy.calls, ["600519"])
                breaker.record_success.assert_called_once_with("akshare_chip")


if __name__ == "__main__":
    unittest.main()
