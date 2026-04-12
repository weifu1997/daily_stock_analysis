import unittest

from src.integrations.mx.zixuan_client import MxZixuanClient
from src.services.zixuan_sync_service import ZixuanSyncService


class FakeClient:
    def __init__(self, current, delete_result=None):
        self.current = list(current)
        self.added = []
        self.deleted = []
        self.delete_result = delete_result

    def list_codes(self):
        return list(self.current)

    def add_codes(self, codes):
        self.added.extend(codes)
        return {"added": list(codes), "failed": []}

    def delete_codes(self, codes):
        self.deleted.extend(codes)
        if self.delete_result is not None:
            return self.delete_result
        return {"deleted": list(codes), "failed": []}


class TestZixuanSyncService(unittest.TestCase):
    def test_delete_when_current_has_old_codes(self):
        client = FakeClient(["000001", "000002", "600000"])
        service = ZixuanSyncService(client=client, allow_delete=True)

        result = service.sync(
            candidate_codes=["000001", "000003"],
            portfolio_codes=["000003"],
        )

        self.assertEqual(result.added, ["000003"])
        self.assertEqual(result.deleted, ["000002", "600000"])
        self.assertEqual(result.skipped, [])
        self.assertEqual(client.deleted, ["000002", "600000"])

    def test_keep_deleted_codes_when_delete_disabled(self):
        client = FakeClient(["000001", "000002"])
        service = ZixuanSyncService(client=client, allow_delete=False)

        result = service.sync(
            candidate_codes=["000001"],
            portfolio_codes=[],
        )

        self.assertEqual(result.added, [])
        self.assertEqual(result.deleted, [])
        self.assertEqual(result.skipped, ["000002"])
        self.assertEqual(client.deleted, [])


class TestZixuanSyncServiceSummary(unittest.TestCase):
    def test_summary_distinguishes_rate_limit_and_invalid_code_skips(self):
        client = FakeClient(
            ["000001", "000002", "100"],
            delete_result={
                "deleted": ["000002"],
                "failed": ["100", "000003"],
                "fail_details": {
                    "100": {"variants": [], "last_result": None, "last_error": "invalid_code"},
                    "000003": {"variants": ["000003"], "last_result": None, "last_error": "skipped_after_rate_limit"},
                },
            },
        )
        service = ZixuanSyncService(client=client, allow_delete=True)

        result = service.sync(candidate_codes=["000001"], portfolio_codes=[])

        self.assertIn("频控跳过 1", result.summary)
        self.assertIn("异常代码跳过 1", result.summary)
        self.assertIn("删除失败详情", result.diff_summary)
        self.assertIn("invalid_code", result.diff_summary)
        self.assertIn("skipped_after_rate_limit", result.diff_summary)


class TestMxZixuanClientDeleteRateLimit(unittest.TestCase):
    def test_delete_stops_after_rate_limit_and_marks_remaining_skipped(self):
        client = MxZixuanClient.__new__(MxZixuanClient)
        client.apikey = "k"
        client._skill_imported = True
        client._skill = None

        calls = []

        def fake_manage(query):
            calls.append(query)
            if len(calls) == 1:
                return {"code": 112, "message": "请求频率过高，请稍后再试"}
            return {"code": 0, "message": "ok"}

        client._manage_self_select = fake_manage
        client._with_rate_limit_retry = lambda action_desc, func: (func(), None)
        client._code_variants = lambda code: [code]
        client._normalize_sync_code = lambda code: code
        client.BETWEEN_CODES_SLEEP_SECONDS = 0
        client.BETWEEN_VARIANTS_SLEEP_SECONDS = 0

        result = client.delete_codes(["000001", "000002", "000003"])

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["failed"], ["000001", "000002", "000003"])
        self.assertEqual(result["fail_details"]["000001"]["last_result"]["code"], 112)
        self.assertEqual(result["fail_details"]["000002"]["last_error"], "skipped_after_rate_limit")
        self.assertEqual(result["fail_details"]["000003"]["last_error"], "skipped_after_rate_limit")


class TestMxZixuanClientCodeNormalization(unittest.TestCase):
    def test_list_codes_filters_invalid_and_deduplicates(self):
        client = MxZixuanClient.__new__(MxZixuanClient)
        client.apikey = "k"
        client._skill_imported = True
        client._skill = None
        client._query_self_select = lambda: {
            "data": {
                "allResults": {
                    "result": {
                        "dataList": [
                            {"SECURITY_CODE": "600519"},
                            {"code": "000001"},
                            {"code": "100"},
                            {"code": "000001"},
                        ]
                    }
                }
            }
        }

        self.assertEqual(client.list_codes(), ["600519", "000001"])

    def test_delete_codes_marks_invalid_input(self):
        client = MxZixuanClient.__new__(MxZixuanClient)
        client.apikey = "k"
        client._skill_imported = True
        client._skill = None
        client._manage_self_select = lambda query: {"code": 0, "message": "ok"}
        client._with_rate_limit_retry = lambda action_desc, func: (func(), None)
        client._code_variants = lambda code: [code]
        client.BETWEEN_CODES_SLEEP_SECONDS = 0
        client.BETWEEN_VARIANTS_SLEEP_SECONDS = 0

        result = client.delete_codes(["100", "600519"])

        self.assertEqual(result["deleted"], ["600519"])
        self.assertEqual(result["failed"], ["100"])
        self.assertEqual(result["fail_details"]["100"]["last_error"], "invalid_code")


if __name__ == "__main__":
    unittest.main()
