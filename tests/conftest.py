import os
from pathlib import Path

import pytest

from dragonwilds.server import Settings

SAVED_AT = 1792008803  # 2026-10-14 20:13:23 UTC, the fake world's last autosave

# Stands in for the docker CLI: records each call in $FAKE_DOCKER/calls and serves the
# container status and log from files there. The game process "exits" once sent SIGTERM.
FAKE_DOCKER = r"""#!/bin/sh
d=$FAKE_DOCKER
echo "$*" >> "$d/calls"
case $1 in
  inspect)
    case $3 in
      *Status*StartedAt*) echo "$(cat "$d/status") 2026-10-14T19:49:44.866612718Z" ;;
      *Status*) cat "$d/status" ;;
      *) echo 2026-10-14T19:49:44.866612718Z ;;
    esac ;;
  logs) cat "$d/log" ;;
  exec)
    case "$3 $4" in
      "ps -eo")
        dir=/home/steam/rsdw-dedicated/RSDragonwilds/Binaries/Linux
        echo "  1 /bin/bash /entry.sh"
        echo " 57 $dir/RSDragonwildsServer-Linux-Shipping RSDragonwilds -Port=7777" ;;
      "kill -TERM") touch "$d/game-exited" ;;
      "kill -0") [ ! -e "$d/game-exited" ] ;;
    esac ;;
esac
"""


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake docker on PATH; write the container's status and log into the returned dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER", str(tmp_path))
    (tmp_path / "status").write_text("running\n")
    (tmp_path / "log").write_text("")
    manifest = tmp_path / "data/steamapps/appmanifest_4019830.acf"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('"AppState"\n{\n\t"appid"\t\t"4019830"\n\t"buildid"\t\t"25811348"\n}\n')
    save = tmp_path / "data/RSDragonwilds/Saved/SaveGames/MyWorld.sav"
    save.parent.mkdir(parents=True)
    save.write_bytes(b"world")
    os.utime(save, (SAVED_AT, SAVED_AT))
    return tmp_path


@pytest.fixture
def settings(docker: Path) -> Settings:
    return Settings("local", "dragonwilds", str(docker / "data"), "unused", docker / "copies")
