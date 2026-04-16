# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.config import Config
from src.services.portfolio_service import PortfolioService
from src.storage import DatabaseManager


class TestPortfolioSnapshotSemantics:
    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.db_path = Path(self.temp_dir.name) / "portfolio_semantics.db"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test-key",
                    f"DATABASE_PATH={self.db_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.service = PortfolioService()

    def teardown_method(self):
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def test_build_snapshot_payload_does_not_persist_snapshot(self):
        with patch.object(self.service.repo, "replace_positions_lots_and_snapshot") as persist_mock:
            payload = self.service._build_snapshot_payload(account_id=None, as_of=date(2026, 1, 5), cost_method="fifo")

        assert isinstance(payload, dict)
        assert payload["public"]["as_of"] == "2026-01-05"
        persist_mock.assert_not_called()

    def test_get_portfolio_snapshot_keeps_existing_persist_side_effect(self):
        with patch.object(self.service.repo, "replace_positions_lots_and_snapshot") as persist_mock:
            self.service.get_portfolio_snapshot(account_id=None, as_of=date(2026, 1, 5), cost_method="fifo")

        persist_mock.assert_not_called()

    def test_get_portfolio_snapshot_persists_when_accounts_exist(self):
        account = self.service.create_account(name="Main", broker="Demo", market="cn", base_currency="CNY")
        aid = account["id"]
        self.service.record_cash_ledger(
            account_id=aid,
            event_date=date(2026, 1, 1),
            direction="in",
            amount=1000,
            currency="CNY",
        )

        with patch.object(self.service.repo, "replace_positions_lots_and_snapshot") as persist_mock:
            payload = self.service.get_portfolio_snapshot(account_id=aid, as_of=date(2026, 1, 5), cost_method="fifo")

        assert payload["account_count"] == 1
        persist_mock.assert_called_once()
