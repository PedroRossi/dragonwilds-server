import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

import app as joincode

SAMPLE_LOG = (
    '[2026.10.21-21.29.49:682][120]LogNetSessionSettings: Setting ["WorldSaveGuid"] '
    "written with key[xg] value[70D92269036347ECB3D5648A4A573FBC]\n"
    '[2026.10.21-21.29.50:001][121]LogNetSessionSettings: Setting ["JoinCode"] '
    "written with key[xz] value[W63Z-6YZ4]\n"
)


def test_reads_latest_code(tmp_path: Path):
    (tmp_path / "RSDragonwilds-backup-old.log").write_text(
        SAMPLE_LOG.replace("W63Z-6YZ4", "AAAA-1111")
    )
    newest = tmp_path / "RSDragonwilds.log"
    newest.write_text(SAMPLE_LOG)
    # Make the active log unambiguously newest.
    os.utime(newest, (time.time() + 5, time.time() + 5))
    assert joincode.read_latest_code(str(tmp_path)) == "W63Z-6YZ4"


def test_no_code_when_absent(tmp_path: Path):
    (tmp_path / "RSDragonwilds.log").write_text("no session yet\n")
    assert joincode.read_latest_code(str(tmp_path)) is None
    assert joincode.read_latest_code(str(tmp_path / "missing")) is None


def test_index_shows_code(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(joincode, "read_latest_code", lambda *a: "W63Z-6YZ4")
    monkeypatch.setattr(joincode, "_cache", (0.0, None))
    client = TestClient(joincode.app)
    body = client.get("/").text
    assert "W63Z-6YZ4" in body
    assert "usual password" in body
    assert client.get("/code").text == "W63Z-6YZ4"


def test_index_handles_starting_server(monkeypatch):
    monkeypatch.setattr(joincode, "read_latest_code", lambda *a: None)
    monkeypatch.setattr(joincode, "_cache", (0.0, None))
    client = TestClient(joincode.app)
    assert "starting up" in client.get("/").text
    assert client.get("/code").text == ""
