from pathlib import Path

import pytest
from typer.testing import CliRunner

from dragonwilds import cli
from dragonwilds.server import Backup, Server

RUN_LOG = (
    "LogDomServerGameMode: Loading configured world [MyWorld] from save slot [MyWorld]\n"
    'LogNetSessionSettings: Setting ["WorldSaveGuid"] written with key[xg] value[ABCD]\n'
    'LogNetSessionSettings: Setting ["JoinCode"] written with key[xz] value[AAAA-1111]\n'
)


def dw(docker: Path, *args: str):
    options = ["--host", "local", "--data-dir", str(docker / "data")]
    return CliRunner().invoke(cli.app, [*options, *args])


def test_key_prints_the_invite_code(docker: Path):
    (docker / "log").write_text(RUN_LOG)
    result = dw(docker, "key")
    assert result.exit_code == 0
    assert result.stdout == "AAAA-1111\n"


def test_key_refuses_a_stale_code_from_a_stopped_server(docker: Path):
    (docker / "status").write_text("exited\n")
    (docker / "log").write_text(RUN_LOG)
    assert dw(docker, "key").exit_code == 1


def test_key_refuses_a_stale_code_from_a_crashed_game(docker: Path):
    (docker / "game-exited").touch()
    (docker / "log").write_text(RUN_LOG)
    result = dw(docker, "key")
    assert result.exit_code == 1
    assert "AAAA-1111" not in result.output


def test_status(docker: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "public_build", lambda: "25811348")
    (docker / "log").write_text(RUN_LOG)
    result = dw(docker, "status")
    assert result.exit_code == 0
    assert "running (started" in result.stdout
    assert "25811348 (latest)" in result.stdout
    assert "MyWorld (GUID ABCD)" in result.stdout
    assert "AAAA-1111" in result.stdout
    assert "dragonwilds-backup running, dragonwilds-watchdog missing" in result.stdout


def test_status_reports_a_crash(docker: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "public_build", lambda: "25811348")
    (docker / "game-exited").touch()
    (docker / "log").write_text(
        RUN_LOG + "Launching RSDragonwildsServer.sh\nJoin succeeded: Alice\n"
    )
    result = dw(docker, "status")
    assert result.exit_code == 0
    assert "CRASHED" in result.stdout
    assert "Players online  nobody" in result.stdout
    assert "AAAA-1111" not in result.stdout


def test_update_does_nothing_when_already_on_the_latest_build(
    docker: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(cli, "public_build", lambda: "25811348")
    result = dw(docker, "update")
    assert result.exit_code == 0
    assert "Already on the latest build" in result.stdout
    assert "stop" not in (docker / "calls").read_text()


def test_restart_backs_up_stops_cleanly_and_checks_the_world(
    docker: Path, monkeypatch: pytest.MonkeyPatch
):
    backups = []
    backup = Backup("/backups/manual-x.tgz", 1_000_000, "0" * 64, 1, ("MyWorld.sav",))
    monkeypatch.setattr(Server, "backup", lambda self, label: backups.append(label) or backup)
    (docker / "log").write_text(RUN_LOG)
    result = dw(docker, "restart")
    assert result.exit_code == 0, result.output
    assert backups == ["pre-restart"]
    calls = (docker / "calls").read_text().splitlines()
    signal, stop, start = (
        "exec dragonwilds kill -TERM 57",
        "stop -t 30 dragonwilds",
        "start dragonwilds",
    )
    assert calls.index(signal) < calls.index(stop) < calls.index(start)
    assert "same world as before (GUID ABCD)" in result.stdout
    assert "Invite code: AAAA-1111" in result.stdout
