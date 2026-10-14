import os
import shlex
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dragonwilds import server
from dragonwilds.server import Host, Server, Settings

SCRIPTS = [
    server.CURRENT_RUN,
    server.BACKUP,
    server.STOP,
    server.START,
    server.FOLLOW_STARTUP,
    server.WAIT_FOR_SAVE,
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_host_scripts_are_valid_sh(script: str):
    assert subprocess.run(["sh", "-n", "-c", script], check=False).returncode == 0


def test_ssh_command_survives_the_remote_shell():
    script = "echo \"$1\" 'quoted' $((1 + 1))"
    argv = Host("pedro@host").argv(script, ("a b", "it's"))
    assert argv[0] == "ssh" and argv[-2] == "pedro@host"
    assert shlex.split(argv[-1]) == ["sh", "-c", script, "sh", "a b", "it's"]
    assert Host("pedro@host", password="pw").argv(script, ())[:2] == ["sshpass", "-e"]
    assert Host("local").text('echo "$1"', "it's") == "it's\n"


def test_snapshot_reads_the_current_run_without_the_join_password(docker: Path, settings: Settings):
    (docker / "log").write_text(
        "LogNet: Login request: ?p=c2VjcmV0?pf=PC?cpx=1?c?Name=Alice userId: RedpointEOS:a"
        " platform: RedpointEOS\n"
        "LogNet: Join succeeded: Alice\n"
        'LogNetSessionSettings: Setting ["JoinCode"] written with key[xz] value[AAAA-1111]\n'
        "unrelated noise\n"
    )
    snap = Server(settings).snapshot()
    assert (snap.status, snap.installed_build, snap.invite_code) == (
        "running",
        "25811348",
        "AAAA-1111",
    )
    assert snap.started_at == "2026-10-14T19:49:44.866612718Z"
    assert snap.last_save == datetime(2026, 10, 14, 20, 13, 23, tzinfo=UTC)
    assert snap.players == ["Alice"]
    assert len(snap.lines) == 3
    assert not any("c2VjcmV0" in line for line in snap.lines)


def test_wait_for_autosave_watches_the_save_file(docker: Path, settings: Settings):
    save = docker / "data/RSDragonwilds/Saved/SaveGames/MyWorld.sav"
    autosave = threading.Timer(1, os.utime, (save, (1792009103, 1792009103)))
    autosave.start()
    assert Server(settings).wait_for_autosave(timeout=10)
    autosave.join()


def test_wait_for_autosave_gives_up(settings: Settings):
    assert not Server(settings).wait_for_autosave(timeout=2)


def test_stop_signals_the_game_before_stopping_the_container(docker: Path, settings: Settings):
    assert Server(settings).stop(timeout=5) == "exited cleanly after 0s"
    assert (docker / "calls").read_text().splitlines() == [
        "exec dragonwilds ps -eo pid=,args=",
        "exec dragonwilds kill -TERM 57",
        "exec dragonwilds kill -0 57",
        "stop -t 30 dragonwilds",
    ]


def test_follow_startup_streams_milestones_until_the_invite_code(docker: Path, settings: Settings):
    (docker / "log").write_text(
        "\x1b[0mError! App '4019830' state is 0x6 after update job.\n"
        "Retrying SteamCMD, attempt 2\n"
        "[ 61%] Downloading update (25,523 of 41,425 KB)...\n"
        '\x1b[0mInstalled Steam build ID: "25811348"\n'
        "Launching RSDragonwildsServer.sh\n"
        'Setting ["JoinCode"] written with key[xz] value[AAAA-1111]\n'
    )
    lines = list(Server(settings).follow_startup("2026-10-14T19:49:44Z", timeout=60))
    assert lines == [
        "Error! App '4019830' state is 0x6 after update job.",
        "Retrying SteamCMD, attempt 2",
        'Installed Steam build ID: "25811348"',
        "Launching RSDragonwildsServer.sh",
        'Setting ["JoinCode"] written with key[xz] value[AAAA-1111]',
        "@ready",
    ]


def test_follow_startup_reports_a_container_that_died(docker: Path, settings: Settings):
    (docker / "status").write_text("exited\n")
    (docker / "log").write_text("Session privacy set to [3]. Password[hunter2]\nFatal error\n")
    lines = list(Server(settings).follow_startup("2026-10-14T19:49:44Z", timeout=60))
    assert lines == [
        "@stopped exited",
        "@log Session privacy set to [3]. Password[redacted]",
        "@log Fatal error",
    ]
