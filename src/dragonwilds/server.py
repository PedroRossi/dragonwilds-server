"""Operations on the stack: shell scripts run on the Docker host, over SSH or locally."""

import hashlib
import io
import json
import os
import shlex
import subprocess
import tarfile
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
    "|UChannel::CleanUp|Requested pausing|Launching RSDragonwildsServer"
)
GAME_PROCESS = "RSDragonwildsServer-Linux-Shipping"
STARTUP_PATTERN = (
    "Installed Steam build ID|Error! App|Retrying SteamCMD|Launching RSDragonwildsServer"
    "|Loading configured world|WorldSaveGuid|JoinCode"
)

# The scripts below run under sh on the host and take their inputs as $1, $2, ...

# $1 container, $2 grep pattern, $3 app manifest, $4 SaveGames dir, $5 game process name,
# $6 and $7 sidecar containers. Prints "@state <status> <started at>", "@game up|down",
# "@build <manifest buildid line>", "@saved <epoch of the newest .sav>", "@sidecars
# <name>=<status>...", then the matching log lines of the current (or last) run.
CURRENT_RUN = r"""
state=$(docker inspect -f '{{.State.Status}} {{.State.StartedAt}}' "$1") || exit 1
echo "@state $state"
game=down
if [ "${state%% *}" = running ] && docker top "$1" 2>/dev/null | grep -q "$5"; then game=up; fi
echo "@game $game"
echo "@build $(grep -m 1 '"buildid"' "$3" 2>/dev/null)"
newest=$(ls -t "$4"/*.sav 2>/dev/null | head -n 1)
echo "@saved ${newest:+$(date -r "$newest" +%s)}"
sidecars=$(docker inspect -f '{{.Name}}={{.State.Status}}' "$6" "$7" 2>/dev/null | tr -d /)
echo "@sidecars" $sidecars
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

# $1 container, $2 seconds to wait, $3 game process name. `docker stop` alone kills the game
# outright: the image's bash launcher doesn't pass SIGTERM on. Signalling the game process itself
# lets the engine shut down cleanly (no torn save, and the session leaves the browser at once).
STOP = r"""
pid=$(docker exec "$1" ps -eo pid=,args= | awk -v game="$3" '$0 ~ game {print $1; exit}')
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

# Pak mods live in a "~mods" folder under Content/Paks. The game mounts every pak there over the
# base game and flags the server "modded" in the browser while the folder exists (even empty), so
# "disable" moves the whole folder aside to "~mods.disabled" to get a clean vanilla server back.
# All of these take $1 = the ~mods directory. They are POSIX sh (no stat -c), so they also run on
# the macOS host used by the tests.

# $1 mods dir. Prints "@state active|disabled|none", then "@file <name> <bytes>" per mod file.
MODS_STATE = r"""
dir=$1 dis="${1}.disabled"
if [ -d "$dir" ]; then echo "@state active"; d=$dir
elif [ -d "$dis" ]; then echo "@state disabled"; d=$dis
else echo "@state none"; exit 0; fi
for f in "$d"/*; do [ -e "$f" ] || continue; echo "@file ${f##*/} $(wc -c < "$f" | tr -d ' ')"; done
"""

# $1 mods dir. Reads a tar of mod files from stdin and extracts them into it (created if needed).
MOD_INSTALL = r"""
set -e
mkdir -p "$1"
cd "$1"
tar -xf -
"""

# $1 mods dir, $2 name stem. Removes "$2"* from it; removes the now-empty folder (clears the flag).
MOD_REMOVE = r"""
dir=$1 stem=$2 any=0
for f in "$dir/$stem"*; do
  [ -e "$f" ] || continue
  rm -rf "$f"; echo "@removed ${f##*/}"; any=1
done
[ "$any" = 1 ] || echo "@none"
rmdir "$dir" 2>/dev/null && echo "@emptied" || true
"""

# $1 mods dir. Moves the active folder aside to "<dir>.disabled": the server goes back to vanilla.
MOD_DISABLE = r"""
dir=$1 dis="${1}.disabled"
[ -d "$dir" ] || { echo "@none"; exit 0; }
mkdir -p "$dis"
for f in "$dir"/*; do [ -e "$f" ] || continue; mv -f "$f" "$dis"/; done
rmdir "$dir" 2>/dev/null || true
echo "@disabled"
"""

# $1 mods dir. Moves a "<dir>.disabled" folder back into place.
MOD_ENABLE = r"""
dir=$1 dis="${1}.disabled"
[ -d "$dis" ] || { echo "@none"; exit 0; }
mkdir -p "$dir"
for f in "$dis"/*; do [ -e "$f" ] || continue; mv -f "$f" "$dir"/; done
rmdir "$dis" 2>/dev/null || true
echo "@enabled"
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

    def run(
        self, script: str, *args: str, timeout: float = 300, stdin: bytes | None = None
    ) -> bytes:
        feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        try:
            proc = subprocess.run(
                self.argv(script, args),
                capture_output=True,
                env=self._env(),
                timeout=timeout,
                check=False,
                **feed,
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

    status: str  # the container's, from docker
    started_at: str
    game_running: bool  # the game process itself; it can die while its container stays up
    installed_build: str | None
    last_save: datetime | None
    sidecars: dict[str, str]  # container name -> docker status, for those that exist
    lines: tuple[str, ...]

    @property
    def container_running(self) -> bool:
        return self.status == "running"

    @property
    def condition(self) -> str:
        if not self.container_running:
            return self.status
        if self.game_running:
            return "running"
        if any("Launching RSDragonwildsServer" in line for line in self.lines):
            return "CRASHED: the game process is gone but its container is still up"
        return "starting (SteamCMD)"

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
        return parsing.online_players(self.lines) if self.game_running else []


@dataclass(frozen=True)
class Backup:
    path: str
    size: int
    sha256: str
    attempts: int
    saves: tuple[str, ...]


@dataclass(frozen=True)
class ModState:
    """What is in the server's Content/Paks/~mods folder."""

    state: str  # "active" (mounted), "disabled" (moved aside), or "none"
    files: tuple[tuple[str, int], ...]  # (file name, bytes)


def _tar(files: list[Path]) -> bytes:
    """A flat tar of the given files, named by their basenames, for MOD_INSTALL's stdin."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in files:
            archive.add(path, arcname=path.name)
    return buffer.getvalue()


class Server:
    """The Dragonwilds container and its data directory on the Docker host."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.host = Host(settings.host, settings.ssh_password)
        self._manifest = f"{settings.data_dir}/steamapps/appmanifest_{STEAM_APP_ID}.acf"
        self._saved = f"{settings.data_dir}/RSDragonwilds/Saved"
        self._mods_dir = f"{settings.data_dir}/RSDragonwilds/Content/Paks/~mods"
        # Named by compose.yaml after the server container.
        self.sidecar_names = (f"{settings.container}-backup", f"{settings.container}-watchdog")

    def snapshot(self, pattern: str = LOG_PATTERN) -> Snapshot:
        saves = f"{self._saved}/SaveGames"
        args = (self.settings.container, pattern, self._manifest, saves, GAME_PROCESS)
        out = self.host.text(CURRENT_RUN, *args, *self.sidecar_names)
        state, game, build, saved, sidecars, *lines = out.splitlines()
        status, _, started_at = state.removeprefix("@state ").partition(" ")
        epoch = saved.removeprefix("@saved").strip()
        return Snapshot(
            status=status,
            started_at=started_at,
            game_running=game == "@game up",
            installed_build=parsing.acf_build_id(build),
            last_save=datetime.fromtimestamp(int(epoch), UTC) if epoch else None,
            sidecars=dict(item.split("=", 1) for item in sidecars.split()[1:]),
            lines=tuple(lines),
        )

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
        args = (self.settings.container, str(timeout), GAME_PROCESS)
        out = self.host.text(STOP, *args, timeout=timeout + 90)
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

    def mod_state(self) -> ModState:
        """What the server's ~mods folder holds, without changing anything."""
        state = "none"
        files: list[tuple[str, int]] = []
        for line in self.host.text(MODS_STATE, self._mods_dir).splitlines():
            if line.startswith("@state "):
                state = line.removeprefix("@state ").strip()
            elif line.startswith("@file "):
                name, _, size = line.removeprefix("@file ").rpartition(" ")
                files.append((name, int(size)))
        return ModState(state, tuple(files))

    def install_mods(self, files: list[Path]) -> None:
        """Copy built mod files into ~mods (creating it). Applies on the next restart."""
        self.host.run(MOD_INSTALL, self._mods_dir, stdin=_tar(files))

    def remove_mod(self, stem: str) -> list[str]:
        """Remove ~mods files beginning with stem; returns the names removed."""
        out = self.host.text(MOD_REMOVE, self._mods_dir, stem)
        return [
            ln.removeprefix("@removed ") for ln in out.splitlines() if ln.startswith("@removed ")
        ]

    def disable_mods(self) -> bool:
        """Move ~mods aside to ~mods.disabled. True if there were active mods to disable."""
        return "@disabled" in self.host.text(MOD_DISABLE, self._mods_dir)

    def enable_mods(self) -> bool:
        """Move ~mods.disabled back to ~mods. True if there were disabled mods to restore."""
        return "@enabled" in self.host.text(MOD_ENABLE, self._mods_dir)


def public_build() -> str:
    """The server build Steam currently ships on the public branch."""
    request = urllib.request.Request(PUBLIC_BUILD_URL, headers={"User-Agent": "dragonwilds-cli"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return parsing.public_build_id(json.load(response), STEAM_APP_ID)
