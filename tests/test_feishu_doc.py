import types

from src.feishu_doc import FeishuDocManager


class _FakeDocument:
    def __init__(self, document_id: str):
        self.document_id = document_id


class _FakeDocumentResponse:
    def __init__(self, *, success: bool, code: int = 0, msg: str = "success", document_id: str = "doc_ok"):
        self.code = code
        self.msg = msg
        self.error = {"code": code, "msg": msg}
        self.data = types.SimpleNamespace(document=_FakeDocument(document_id)) if success else None
        self._success = success

    def success(self):
        return self._success


class _FakeBlockChildrenResponse:
    code = 0
    msg = "success"

    def success(self):
        return True


class _FakeDocumentApi:
    def __init__(self, responses):
        self.responses = list(responses)
        self.create_requests = []

    def create(self, request):
        self.create_requests.append(request)
        return self.responses.pop(0)


class _FakeChildrenApi:
    def __init__(self):
        self.create_requests = []

    def create(self, request):
        self.create_requests.append(request)
        return _FakeBlockChildrenResponse()


class _FakeClient:
    def __init__(self, responses):
        self.document_api = _FakeDocumentApi(responses)
        self.children_api = _FakeChildrenApi()
        self.docx = types.SimpleNamespace(
            v1=types.SimpleNamespace(
                document=self.document_api,
                document_block_children=self.children_api,
            )
        )


def _manager_with_client(fake_client, folder_token="folder_xxx"):
    manager = FeishuDocManager.__new__(FeishuDocManager)
    manager.app_id = "cli_xxx"
    manager.app_secret = "secret_xxx"
    manager.folder_token = folder_token
    manager.client = fake_client
    return manager


def test_create_daily_doc_falls_back_to_default_location_when_folder_denies_permission():
    fake_client = _FakeClient(
        [
            _FakeDocumentResponse(success=False, code=1770040, msg="no folder permission"),
            _FakeDocumentResponse(success=True, document_id="doc_fallback"),
        ]
    )
    manager = _manager_with_client(fake_client)

    url = manager.create_daily_doc("日报", "# 标题\n\n内容")

    assert url == "https://feishu.cn/docx/doc_fallback"
    assert len(fake_client.document_api.create_requests) == 2
    assert len(fake_client.children_api.create_requests) == 1


def test_create_daily_doc_does_not_fallback_for_non_folder_permission_errors():
    fake_client = _FakeClient(
        [_FakeDocumentResponse(success=False, code=99991672, msg="scope missing")]
    )
    manager = _manager_with_client(fake_client)

    url = manager.create_daily_doc("日报", "# 标题")

    assert url is None
    assert len(fake_client.document_api.create_requests) == 1
    assert len(fake_client.children_api.create_requests) == 0
