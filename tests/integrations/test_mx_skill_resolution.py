# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import src.integrations.mx.moni_client as moni_client
import src.integrations.mx.zixuan_client as zixuan_client


def test_zixuan_uses_env_skill_dir_when_present(tmp_path):
    skill_dir = tmp_path / "mx-zixuan"
    skill_dir.mkdir()
    (skill_dir / "mx_zixuan.py").write_text("# fake skill\n", encoding="utf-8")

    with patch.dict("os.environ", {"MX_ZIXUAN_SKILL_DIR": str(skill_dir)}, clear=False):
        resolved = zixuan_client._resolve_skill_path()
        assert resolved == skill_dir


def test_zixuan_missing_skill_path_is_reported_cleanly(tmp_path):
    missing_dir = tmp_path / "not-exist"
    client = zixuan_client.MxZixuanClient.__new__(zixuan_client.MxZixuanClient)
    client.apikey = "k"
    client._skill_imported = False
    client._skill = None

    with patch.object(zixuan_client, "_resolve_skill_path", return_value=missing_dir):
        assert client._load_skill() is None


def test_moni_uses_env_skill_file_when_present(tmp_path):
    skill_file = tmp_path / "mx_moni.py"
    skill_file.write_text("OUTPUT_DIR='.'\n", encoding="utf-8")

    with patch.dict("os.environ", {"MX_MONI_SKILL_FILE": str(skill_file)}, clear=False):
        resolved = moni_client._resolve_skill_file()
        assert resolved == skill_file


def test_moni_assigns_apikey_safely(tmp_path):
    skill_file = tmp_path / "mx_moni.py"
    skill_file.write_text(
        "OUTPUT_DIR='.'\n"
        "def make_request(endpoint, body, output_prefix):\n"
        "    open(f'{output_prefix}_raw.json', 'w', encoding='utf-8').write('{\"ok\": true}')\n",
        encoding="utf-8",
    )

    def fake_make_request(endpoint, body, output_prefix):
        (tmp_path / f"{output_prefix}_raw.json").write_text('{"ok": true}', encoding='utf-8')

    fake_mod = SimpleNamespace(OUTPUT_DIR=str(tmp_path), make_request=fake_make_request)
    client = moni_client.MxMoniClient.__new__(moni_client.MxMoniClient)
    client.apikey = "k"
    client._module = fake_mod

    with patch.dict("os.environ", {"MX_APIKEY": "old"}, clear=False):
        result = client._call_request("/x", {}, "test_prefix")

    assert result == {"ok": True}
