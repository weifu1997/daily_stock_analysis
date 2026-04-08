import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from scripts import run_moni_execute as rme
from scripts.run_moni_execute import MoniExecutionResult, MoniPlanItem


class FakeClient:
    def __init__(self, apikey=None):
        self.apikey = apikey

    def query_account(self):
        return {"status": 0, "message": "ok"}

    def query_positions(self):
        return {"data": [{"code": "000001"}]}

    def query_orders(self):
        return {"data": [{"code": "000001"}, {"code": "000002"}]}


class TestRunMoniExecute(unittest.TestCase):
    def test_execute_latest_plan_reads_plan_and_writes_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan_dir = tmp_path / "moni_plans"
            result_dir = tmp_path / "moni_results"
            plan_dir.mkdir(parents=True, exist_ok=True)
            result_dir.mkdir(parents=True, exist_ok=True)

            plan_file = plan_dir / "moni_plan_20260408_180000.json"
            plan_file.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-04-08T18:00:00",
                        "report_date": "2026-04-08",
                        "items": [
                            asdict(MoniPlanItem(code="000001", action="BUY", reason="test", target_date="2026-04-09")),
                            asdict(MoniPlanItem(code="000002", action="SELL", reason="test2", target_date="2026-04-09")),
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(rme, "PLAN_DIR", plan_dir), \
                 patch.object(rme, "RESULT_DIR", result_dir), \
                 patch.object(rme, "MxMoniClient", FakeClient), \
                 patch.dict("os.environ", {"MX_APIKEY": "test-key"}, clear=False):
                result = rme.execute_latest_plan()

            self.assertIsInstance(result, MoniExecutionResult)
            self.assertEqual(len(result.executed_items), 2)
            self.assertEqual(result.executed_items[0]["code"], "000001")
            self.assertTrue(Path(result.plan_file).exists())
            saved = sorted(result_dir.glob("moni_result_*.json"))
            self.assertTrue(saved)
            payload = json.loads(saved[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["plan_file"], str(plan_file))
            self.assertEqual(len(payload["executed_items"]), 2)


if __name__ == "__main__":
    unittest.main()
