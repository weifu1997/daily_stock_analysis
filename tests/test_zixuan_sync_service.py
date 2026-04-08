import unittest

from src.services.zixuan_sync_service import ZixuanSyncService


class FakeClient:
    def __init__(self, current):
        self.current = list(current)
        self.added = []
        self.deleted = []

    def list_codes(self):
        return list(self.current)

    def add_codes(self, codes):
        self.added.extend(codes)
        return {"added": list(codes), "failed": []}

    def delete_codes(self, codes):
        self.deleted.extend(codes)
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


if __name__ == "__main__":
    unittest.main()
