"""Operations on the stack: shell scripts run on the Docker host, over SSH or locally."""

import hashlib
import json
import os
import shlex
import subprocess
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from dragonwilds import parsing

STEAM_APP_ID = "4019830"
PUBLIC_BUILD_URL = f"https://api.steamcmd.net/v1/info/{STEAM_APP_ID}"

# What the commands read from the container log. Login requests carry the join password
# (?p=...); the host strips it before the lines leave. The game buffers its stdout, so lines
# reach docker up to ~90 s late: anything timing-sensitive (autosaves) watches files instead.
LOG_PATTERN = (
    "JoinCode|WorldSaveGuid|Loading configured world|Login request|Join succeeded"
    "|UChannel::CleanUp|Requested pausing"
)
STARTUP_PATTERN = (
    "Installed Steam build ID|Error! App|Retrying SteamCMD|Launching RSDragonwildsServer"
    "|Loading configured world|WorldSaveGuid|JoinCode"
)

# The scripts below run under sh on the host and take their inputs as $1, $2, ...

# $1 container, $2 grep pattern, $3 app manifest, $4 SaveGames dir. Prints "@state <status>
# <started at>", "@build <manifest buildid line>", "@saved <epoch of the newest .sav>", then
# the matching log lines of the current (or last) run.
CURRENT_RUN = r"""
state=$(docker inspect -f '{{.State.Status}} {{.State.StartedAt}}' "$1") || exit 1
echo "@state $state"
echo "@build $(grep -m 1 '"buildid"' "$3" 2>/dev/null)"
newest=$(ls -t "$4"/*.sav 2>/dev/null | head -n 1)
echo "@saved ${newest:+$(date -r "$newest" +%s)}"
docker logs --since "${state#* }" "$1" 2>&1 | grep -a -E "$2" | sed -E 's/[?]p=[^?]*//'
"""

# $1 Saved dir, $2 backup dir, $3 app manifest, $4 label. Tars Saved/ (minus logs) and checks
# each world .sav inside the archive against the live file, retrying when an autosave lands
# mid-copy. The "manual-" prefix keeps these away from the sidecar's pruning of saves-*.tgz.
BACKUP = r"""
set -eu
saved=$1 out_dir=$2 label=$4
build=$(grep -m 1 '"buildid"' "$3" 2>/dev/null | tr -dc 0-9) || true
name="manual-$(date +%Y-%m-%d-%H%M%S)-build${build:-unknown}${label:+-$label}.tgz"
tmp="$out_dir/.$name.partial"
mkdir -p "$out_dir"
trap 'rm -f "$tmp"' EXIT
attempt=1
while :; do
  rc=0
  tar czf "$tmp" --ignore-failed-read --exclude=Saved/Logs --exclude=Saved/Crashes \
    -C "${saved%/*}" Saved || rc=$?
  [ "$rc" -le 1 ] || exit "$rc"  # 1: files changed while read; the check below decides
  ok=1
  for sav in "$saved"/SaveGames/*.sav; do
    [ -e "$sav" ] || continue
    live=$(sha256sum < "$sav")
    packed=$(tar xzf "$tmp" -O "Saved/SaveGames/${sav##*/}" | sha256sum)
    [ "$live" = "$packed" ] || ok=0
  done
  [ "$ok" = 1 ] && break
  [ "$attempt" -lt 3 ] || { echo "a world save kept changing during the backup" >&2; exit 1; }
  attempt=$((attempt + 1))
  sleep 5
done
mv "$tmp" "$out_dir/$name"
echo "path=$out_dir/$name"
echo "bytes=$(stat -c %s "$out_dir/$name")"
echo "sha256=$(sha256sum < "$out_dir/$name" | cut -d ' ' -f 1)"
echo "attempts=$attempt"
for sav in "$saved"/SaveGames/*.sav; do
  if [ -e "$sav" ]; then echo "save=${sav##*/}"; fi
done
"""

# $1 container, $2 seconds to wait. `docker stop` alone kills the game outright: the image's
# bash launcher doesn't pass SIGTERM on. Signalling the game process itself lets the engine
# shut down cleanly (no torn save, and the session leaves the server browser right away).
STOP = r"""
pid=$(docker exec "$1" ps -eo pid=,args= \
  | awk '/RSDragonwildsServer-Linux-Shipping/ {print $1; exit}')
if [ -z "$pid" ]; then
  echo "game=not running"
else
  docker exec "$1" kill -TERM "$pid"
  waited=0
  while docker exec "$1" kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$2" ]; then break; fi
    sleep 1
    waited=$((waited + 1))
  done
  if [ "$waited" -ge "$2" ]; then
    echo "game=still running after ${waited}s"
  else
    echo "game=exited cleanly after ${waited}s"
  fi
fi
docker stop -t 30 "$1" >/dev/null
"""

# $1 container. Prints when the new run started.
START = r"""
set -e
docker start "$1" >/dev/null
docker inspect -f '{{.State.StartedAt}}' "$1"
"""

# $1 container, $2 start time, $3 timeout (s), $4 grep pattern. Streams new matching log lines
# until the invite code appears (@ready), the container stops (@stopped, then its last log
# lines as @log) or time runs out (@timeout); "@waiting <s>" every minute in between.
FOLLOW_STARTUP = r"""
seen=0 waited=0
while :; do
  status=$(docker inspect -f '{{.State.Status}}' "$1")
  lines=$(docker logs --since "$2" "$1" 2>&1 | grep -a -E "$4")
  count=$(printf '%s\n' "$lines" | grep -a -c .)
  if [ "$count" -gt "$seen" ]; then
    printf '%s\n' "$lines" | grep -a . | tail -n "+$((seen + 1))"
    seen=$count
  fi
  case $lines in *JoinCode*) echo "@ready"; exit 0 ;; esac
  if [ "$status" != running ]; then
    echo "@stopped $status"
    docker logs --tail 25 "$1" 2>&1 \
      | sed -E 's/Password\[[^]]*\]/Password[redacted]/; s/[?]p=[^?]*//; s/^/@log /'
    exit 1
  fi
  if [ "$waited" -ge "$3" ]; then echo "@timeout"; exit 1; fi
  sleep 5
  waited=$((waited + 5))
  if [ $((waited % 60)) -eq 0 ]; then echo "@waiting $waited"; fi
done
"""

# $1 SaveGames dir, $2 timeout (s). Prints "saved" once the next autosave has been written,
# going by the newest .sav's modification time.
WAIT_FOR_SAVE = r"""
newest() { f=$(ls -t "$1"/*.sav 2>/dev/null | head -n 1) && [ -n "$f" ] && date -r "$f" +%s; }
before=$(newest "$1") waited=0
while [ "$waited" -lt "$2" ]; do
  sleep 2
  waited=$((waited + 2))
  if [ "$(newest "$1")" != "$before" ]; then
    sleep 2  # a save takes well under a second; let it finish
    echo saved
    exit 0
  fi
done
echo timeout
"""


class RemoteError(RuntimeError):
    """A command on the Docker host failed."""


@dataclass(frozen=True)
class Host:
    """Runs scripts on the Docker host: over SSH, or directly when the target is "local"."""

    target: str
    password: str | None = None

    @property
    def local(self) -> bool:
        return self.target == "local"

    def run(self, script: str, *args: str, timeout: float = 300) -> bytes:
        try:
            proc = subprocess.run(
                self.argv(script, args),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=self._env(),
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as error:
            raise RemoteError(f"{error.filename} is not installed") from None
        except subprocess.TimeoutExpired:
            raise RemoteError(f"no answer from {self.target} after {timeout:.0f}s") from None
        if proc.returncode != 0:
            message = proc.stderr.decode(errors="replace").strip()
            raise RemoteError(message or f"exit status {proc.returncode}")
        return proc.stdout

    def text(self, script: str, *args: str, timeout: float = 300) -> str:
        return self.run(script, *args, timeout=timeout).decode(errors="replace")

    def stream(self, script: str, *args: str) -> Iterator[str]:
        """Yield the script's output lines as they arrive."""
        try:
            proc = subprocess.Popen(
                self.argv(script, args),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                env=self._env(),
                text=True,
                errors="replace",
            )
        except FileNotFoundError as error:
            raise RemoteError(f"{error.filename} is not installed") from None
        with proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                yield line.rstrip("\n")
        if proc.returncode == 255 and not self.local:
            raise RemoteError(f"lost the SSH connection to {self.target}")

    def argv(self, script: str, args: tuple[str, ...]) -> list[str]:
        command = ["sh", "-c", script, "sh", *args]
        if self.local:
            return command
        ssh = ["ssh", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15"]
        if self.password:
            ssh = ["sshpass", "-e", *ssh, "-o", "PubkeyAuthentication=no"]
        return [*ssh, self.target, shlex.join(command)]

    def _env(self) -> dict[str, str] | None:
        return {**os.environ, "SSHPASS": self.password} if self.password else None


@dataclass(frozen=True)
class Settings:
    host: str
    container: str
    data_dir: str
    backup_dir: str
    local_backup_dir: Path
    ssh_password: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """The container's state and what its current run has logged so far."""

    status: str
    started_at: str
    installed_build: str | None
    last_save: datetime | None
    lines: tuple[str, ...]

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def invite_code(self) -> str | None:
        return parsing.last_match(parsing.JOIN_CODE, self.lines)

    @property
    def world(self) -> str | None:
        return parsing.last_match(parsing.WORLD_NAME, self.lines)

    @property
    def world_guid(self) -> str | None:
        return parsing.last_match(parsing.WORLD_GUID, self.lines)

    @property
    def players(self) -> list[str]:
        return parsing.online_players(self.lines) if self.running else []


@dataclass(frozen=True)
class Backup:
    path: str
    size: int
    sha256: str
    attempts: int
    saves: tuple[str, ...]


class Server:
    """The Dragonwilds container and its data directory on the Docker host."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.host = Host(settings.host, settings.ssh_password)
        self._manifest = f"{settings.data_dir}/steamapps/appmanifest_{STEAM_APP_ID}.acf"
        self._saved = f"{settings.data_dir}/RSDragonwilds/Saved"

    def snapshot(self, pattern: str = LOG_PATTERN) -> Snapshot:
        args = (self.settings.container, pattern, self._manifest, f"{self._saved}/SaveGames")
        state, build, saved, *lines = self.host.text(CURRENT_RUN, *args).splitlines()
        status, _, started_at = state.removeprefix("@state ").partition(" ")
        epoch = saved.removeprefix("@saved").strip()
        last_save = datetime.fromtimestamp(int(epoch), UTC) if epoch else None
        return Snapshot(status, started_at, parsing.acf_build_id(build), last_save, tuple(lines))

    def backup(self, label: str = "") -> Backup:
        args = (self._saved, self.settings.backup_dir, self._manifest, label)
        out = self.host.text(BACKUP, *args)
        fields: dict[str, list[str]] = {}
        for line in out.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                fields.setdefault(key, []).append(value)
        return Backup(
            path=fields["path"][0],
            size=int(fields["bytes"][0]),
            sha256=fields["sha256"][0],
            attempts=int(fields["attempts"][0]),
            saves=tuple(fields.get("save", [])),
        )

    def download(self, backup: Backup) -> Path:
        """Copy a backup to this machine, checking that it arrived intact."""
        data = self.host.run('cat "$1"', backup.path)
        if hashlib.sha256(data).hexdigest() != backup.sha256:
            raise RemoteError(f"the copy of {backup.path} doesn't match its checksum")
        target = self.settings.local_backup_dir / PurePosixPath(backup.path).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def stop(self, timeout: int = 120) -> str:
        """Stop the game cleanly, then the container; says how the game process ended."""
        out = self.host.text(STOP, self.settings.container, str(timeout), timeout=timeout + 90)
        outcomes = [line for line in out.splitlines() if line.startswith("game=")]
        return outcomes[0].removeprefix("game=") if outcomes else "in an unknown state"

    def start(self) -> str:
        """Start the container; returns when this run started."""
        return self.host.text(START, self.settings.container).strip()

    def follow_startup(self, started_at: str, timeout: int) -> Iterator[str]:
        args = (self.settings.container, started_at, str(timeout), STARTUP_PATTERN)
        for line in self.host.stream(FOLLOW_STARTUP, *args):
            yield parsing.ANSI.sub("", line)

    def wait_for_autosave(self, timeout: int = 360) -> bool:
        saves = f"{self._saved}/SaveGames"
        out = self.host.text(WAIT_FOR_SAVE, saves, str(timeout), timeout=timeout + 60)
        return out.strip() == "saved"


def public_build() -> str:
    """The server build Steam currently ships on the public branch."""
    request = urllib.request.Request(PUBLIC_BUILD_URL, headers={"User-Agent": "dragonwilds-cli"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return parsing.public_build_id(json.load(response), STEAM_APP_ID)
