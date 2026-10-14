"""The watchdog service's script from compose.yaml, run against the fake docker CLI."""

import os
import subprocess
import tarfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).parents[1] / "compose.yaml"


@contextmanager
def watchdog(docker: Path) -> Iterator[Path]:
    """Run the watchdog, checking every 0.2 s, with its mounts under the fake docker's dir."""
    script = yaml.safe_load(COMPOSE.read_text())["services"]["watchdog"]["command"][0]
    script = script.replace("$$", "$")  # what compose hands the container
    script = script.replace("/data/", f"{docker}/data/").replace("/backups/", f"{docker}/backups/")
    (docker / "backups").mkdir(exist_ok=True)
    env = {**os.environ, "WATCH_CONTAINER": "dragonwilds", "WATCH_INTERVAL": "0.2"}
    output = docker / "watchdog.out"
    with output.open("w") as out:
        proc = subprocess.Popen(["sh", "-c", script], env=env, stdout=out, stderr=out)
        try:
            yield output
        finally:
            proc.terminate()
            proc.wait()


def wait_for(condition: Callable[[], bool], timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def calls(docker: Path) -> str:
    path = docker / "calls"
    return path.read_text() if path.exists() else ""


def test_restarts_the_server_when_the_game_dies(docker: Path):
    (docker / "log").write_text(
        "Unhandled Exception: SIGSEGV: invalid attempt to read memory at address 0x30\n"
        "0x000000000a6ab41a RSDragonwildsServer!HandlePlayerEngagedActors\n"
    )
    with watchdog(docker) as output:
        assert wait_for(lambda: calls(docker).count("top dragonwilds") >= 2)
        (docker / "game-exited").touch()
        assert wait_for(lambda: "restart -t 30 dragonwilds" in calls(docker))
    log = output.read_text()
    assert "game process gone, restarting dragonwilds" in log
    assert "Unhandled Exception: SIGSEGV" in log
    [snapshot] = (docker / "backups").glob("saves-*-crash.tgz")
    with tarfile.open(snapshot) as archive:
        assert "SaveGames/MyWorld.sav" in archive.getnames()


@pytest.mark.parametrize("status", ["running", "exited"])
def test_leaves_alone_a_game_it_never_saw_running(docker: Path, status: str):
    # Running without the game: SteamCMD is still at work. Exited: someone stopped the stack.
    (docker / "status").write_text(f"{status}\n")
    (docker / "game-exited").touch()
    with watchdog(docker):
        assert wait_for(lambda: calls(docker).count("inspect") >= 5)
    assert "restart" not in calls(docker)


def test_stands_back_after_three_restarts_in_an_hour(docker: Path):
    stop = threading.Event()

    def crash_whenever_seen_running() -> None:
        while not stop.wait(0.05):
            since_restart = calls(docker).rsplit("restart -t 30", 1)[-1]
            if since_restart.count("top dragonwilds") >= 2:
                (docker / "game-exited").touch()

    crasher = threading.Thread(target=crash_whenever_seen_running)
    crasher.start()
    try:
        with watchdog(docker) as output:
            assert wait_for(lambda: "3 restarts this hour" in output.read_text())
            time.sleep(1)
    finally:
        stop.set()
        crasher.join()
    assert calls(docker).count("restart -t 30 dragonwilds") == 3
