"""`dw`: invite code, backups, clean restarts and game updates for the Dragonwilds server."""

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from dragonwilds import parsing
from dragonwilds.server import RemoteError, Server, Settings, Snapshot, public_build

LOCAL_BACKUPS = Path.home() / "dragonwilds-backups"
LABEL_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
MOD_SUFFIXES = {".pak", ".utoc", ".ucas"}  # one IoStore pak set per mod

app = typer.Typer(no_args_is_help=True, add_completion=False)
mod_app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Pak mods in the server's Content/Paks/~mods folder (install, list, disable).",
)
app.add_typer(mod_app, name="mod")

Yes = Annotated[bool, typer.Option("--yes", "-y", help="Don't ask before kicking online players.")]
WaitSave = Annotated[
    bool, typer.Option(help="With players online, wait for the next autosave before stopping.")
]
Timeout = Annotated[int, typer.Option(help="Seconds to wait for the server to come back up.")]


@app.callback()
def configure(
    ctx: typer.Context,
    host: Annotated[
        str, typer.Option(envvar="DW_HOST", help='SSH target, or "local" when run on the host.')
    ] = "pedro@192.168.1.5",
    container: Annotated[str, typer.Option(envvar="DW_CONTAINER")] = "dragonwilds",
    data_dir: Annotated[
        str, typer.Option(envvar="DW_DATA_DIR", help="Server data directory on the host.")
    ] = "/home/pedro/dragonwilds",
    backup_dir: Annotated[
        str, typer.Option(envvar="DW_BACKUP_DIR", help="Backup directory on the host.")
    ] = "/home/pedro/dragonwilds-backups",
    local_backup_dir: Annotated[
        Path, typer.Option(envvar="DW_LOCAL_BACKUP_DIR", help="Where backups are copied here.")
    ] = LOCAL_BACKUPS,
) -> None:
    """Run the Dragonwilds server: invite code, backups, clean restarts, game updates.

    Logs in to the host with your SSH keys, or with DW_SSH_PASSWORD (needs sshpass).
    """
    password = os.environ.get("DW_SSH_PASSWORD") or None
    settings = Settings(host, container, data_dir, backup_dir, local_backup_dir, password)
    ctx.obj = Server(settings)


@app.command()
def key(
    ctx: typer.Context,
    wait: Annotated[int, typer.Option(help="Seconds to keep trying while it starts.")] = 0,
) -> None:
    """Print the invite code. It changes on every restart."""
    server: Server = ctx.obj
    deadline = time.monotonic() + wait
    while True:
        snap = server.snapshot("JoinCode")
        if snap.game_running and snap.invite_code:
            typer.echo(snap.invite_code)
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(5)
    if not snap.container_running:
        raise _fail(f"the server is {snap.status}, so there is no valid invite code")
    if not snap.game_running:
        raise _fail("the game isn't running: still starting, or crashed (see `dw status`)")
    raise _fail("no invite code logged yet; the server is still starting (try --wait 600)")


def _check_label(label: str) -> str:
    if not set(label) <= LABEL_CHARS:
        raise typer.BadParameter("use letters, digits, dots, dashes or underscores")
    return label


@app.command()
def backup(
    ctx: typer.Context,
    label: Annotated[
        str, typer.Option(callback=_check_label, help="File name suffix, e.g. before-boss.")
    ] = "",
    download: Annotated[bool, typer.Option(help="Also keep a copy on this machine.")] = True,
) -> None:
    """Back up the world on the host (checksum-verified) and copy it here."""
    _backup(ctx.obj, label, download=download)


@app.command()
def status(ctx: typer.Context) -> None:
    """Show the server state, game build, world, players online, last autosave, invite code."""
    server: Server = ctx.obj
    snap = server.snapshot()
    sidecars = (f"{name} {snap.sidecars.get(name, 'missing')}" for name in server.sidecar_names)
    rows = {
        "Server": f"{snap.condition} (started {_local_time(snap.started_at)})",
        "Game build": _build_status(snap.installed_build, _latest_build()),
        "World": f"{snap.world or '?'} (GUID {snap.world_guid or '?'})",
        "Players online": ", ".join(snap.players) or "nobody",
        "Last autosave": _ago(snap.last_save),
        "Invite code": (snap.game_running and snap.invite_code) or "-",
        "Sidecars": ", ".join(sidecars),
    }
    width = max(map(len, rows))
    for name, value in rows.items():
        typer.echo(f"{name:<{width}}  {value}")


@app.command()
def restart(
    ctx: typer.Context, yes: Yes = False, wait_save: WaitSave = True, timeout: Timeout = 1200
) -> None:
    """Back up, stop the game cleanly and start it again. Also applies a pending game update."""
    server: Server = ctx.obj
    _restart(
        server, server.snapshot(), "pre-restart", yes=yes, wait_save=wait_save, timeout=timeout
    )


@app.command()
def update(
    ctx: typer.Context,
    yes: Yes = False,
    wait_save: WaitSave = True,
    timeout: Timeout = 1200,
    force: Annotated[
        bool, typer.Option("--force", help="Restart even without a newer build.")
    ] = False,
) -> None:
    """Restart onto Steam's newest server build, when it's newer than the installed one."""
    server: Server = ctx.obj
    before = server.snapshot()
    latest = _latest_build()
    if latest is None and not force:
        raise _fail("can't tell whether there's an update; `dw update --force` restarts anyway")
    if latest == before.installed_build and not force:
        _step(f"Already on the latest build ({latest})")
        return
    _step(f"Updating build {before.installed_build} -> {latest or '?'}")
    if server.mod_state().state == "active":
        _warn(
            "pak mods are installed; a new build usually breaks them and can stop the server"
            " booting. If it doesn't come back, run `dw mod disable` and rebuild them for the"
            " new build."
        )
    lines = _restart(server, before, "pre-update", yes=yes, wait_save=wait_save, timeout=timeout)
    installed = parsing.last_match(parsing.INSTALLED_BUILD, lines)
    if latest and installed != latest:
        _warn(f"came back on build {installed}, not {latest}; Steam may still be rolling it out")


def _restart(
    server: Server, before: Snapshot, label: str, *, yes: bool, wait_save: bool, timeout: int
) -> list[str]:
    """Back up, stop cleanly, start, and check the same world came back. Returns startup lines."""
    if before.players:
        if not yes:
            names = ", ".join(before.players)
            typer.confirm(f"Online now: {names}. Restarting kicks them. Continue?", abort=True)
        if wait_save:
            _step("Waiting for the next autosave so nobody loses progress (up to ~6 min)")
            if not server.wait_for_autosave():
                _warn("no autosave in time; going ahead with the last one")
    _backup(server, label, download=True)
    if before.container_running:
        _step("Stopping the game cleanly")
        outcome = server.stop()
        if outcome.startswith("still running"):
            _warn(f"game {outcome}; docker stop killed it (the backup above has the world)")
        else:
            _info(f"game {outcome}")
    _step("Starting (SteamCMD runs first and applies any game update)")
    lines = _follow_startup(server, server.start(), timeout)
    guid = parsing.last_match(parsing.WORLD_GUID, lines)
    if before.world_guid is None:
        _info(f"world GUID {guid}")
    elif guid == before.world_guid:
        _info(f"same world as before (GUID {guid})")
    else:
        _warn(
            f"world GUID changed from {before.world_guid} to {guid}: this is NOT the same world."
            " Stop the server and restore the backup above."
        )
    _step(f"Server is up. Invite code: {parsing.last_match(parsing.JOIN_CODE, lines)}")
    return lines


def _backup(server: Server, label: str, *, download: bool) -> None:
    _step("Backing up the world")
    result = server.backup(label)
    _info(f"{result.path} ({result.size / 1e6:.1f} MB)")
    if result.saves:
        _info(f"checked against the live save: {', '.join(result.saves)}")
    else:
        _warn("no .sav in Saved/SaveGames, so this backup holds no world")
    if result.attempts > 1:
        _info(f"an autosave landed mid-copy; took {result.attempts} attempts")
    if download and not server.host.local:
        _info(f"copy on this machine: {server.download(result)}")


def _follow_startup(server: Server, started_at: str, timeout: int) -> list[str]:
    lines: list[str] = []
    for line in server.follow_startup(started_at, timeout):
        if line == "@ready":
            return lines
        if line == "@timeout":
            raise _fail(f"no invite code after {timeout}s; check `dw status` or the logs")
        if line.startswith("@waiting "):
            _info(f"still starting ({line.removeprefix('@waiting ')}s)")
        elif line.startswith("@stopped "):
            _warn(f"the container stopped ({line.removeprefix('@stopped ')}); last log lines:")
        elif line.startswith("@log "):
            typer.echo(f"      {line.removeprefix('@log ')}", err=True)
        else:
            lines.append(line)
            _describe(line)
    raise _fail("the server did not come back up")


def _describe(line: str) -> None:
    """Narrate a startup milestone from the container log."""
    if build := parsing.last_match(parsing.INSTALLED_BUILD, [line]):
        _info(f"game build {build} installed")
    elif world := parsing.last_match(parsing.WORLD_NAME, [line]):
        _info(f"loading world {world}")
    elif "Launching RSDragonwildsServer" in line:
        _info("server process launched")
    elif "Error! App" in line:
        _warn(f"SteamCMD: {line.strip()} (the image retries)")
    elif "Retrying SteamCMD" in line:
        _info(line.strip())


def _latest_build() -> str | None:
    try:
        return public_build()
    except (OSError, ValueError, KeyError) as error:
        _warn(f"couldn't get the latest build from Steam: {error}")
        return None


def _build_status(installed: str | None, latest: str | None) -> str:
    if latest is None:
        return f"{installed} (latest unknown)"
    if installed == latest:
        return f"{installed} (latest)"
    return f"{installed}, Steam has {latest}: run `dw update`"


def _local_time(timestamp: str) -> str:
    return f"{datetime.fromisoformat(timestamp).astimezone():%Y-%m-%d %H:%M}"


def _ago(moment: datetime | None) -> str:
    if moment is None:
        return "none yet"
    minutes = int((datetime.now(UTC) - moment).total_seconds() // 60)
    return f"{moment.astimezone():%Y-%m-%d %H:%M:%S} ({minutes} min ago)"


def _step(message: str) -> None:
    typer.secho(f"==> {message}", bold=True)


def _info(message: str) -> None:
    typer.echo(f"    {message}")


def _warn(message: str) -> None:
    typer.secho(f"!!  {message}", fg=typer.colors.YELLOW, err=True)


def _fail(message: str) -> typer.Exit:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    return typer.Exit(1)


def _collect_mod_files(paths: list[Path]) -> list[Path]:
    """The .pak/.utoc/.ucas files among the given files and directories."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files += sorted(f for f in path.iterdir() if f.suffix in MOD_SUFFIXES)
        elif path.is_file():
            if path.suffix in MOD_SUFFIXES:
                files.append(path)
        else:
            raise _fail(f"no such path: {path}")
    return files


@mod_app.command("ls")
def mod_ls(ctx: typer.Context) -> None:
    """List the pak mods installed on the server."""
    server: Server = ctx.obj
    state = server.mod_state()
    if state.state == "none":
        _info("no mods installed — the server is vanilla")
        return
    where = "~mods" if state.state == "active" else "~mods.disabled (inactive until re-enabled)"
    _step(f"Mods {state.state} ({where})")
    for name, size in state.files:
        _info(f"{name} ({size / 1e6:.1f} MB)")
    if not state.files:
        _info("(folder present but empty — still flags the server as modded)")


@mod_app.command("install")
def mod_install(
    ctx: typer.Context,
    paths: Annotated[
        list[Path],
        typer.Argument(help="Built .pak/.utoc/.ucas files, or a dir (e.g. mods/<name>/dist)."),
    ],
) -> None:
    """Copy a built mod into ~mods. Does NOT restart — run `dw restart` to apply it."""
    server: Server = ctx.obj
    files = _collect_mod_files(paths)
    if not files:
        raise _fail("found no .pak/.utoc/.ucas files in the given paths")
    server.install_mods(files)
    _step(f"Installed to ~mods: {', '.join(sorted(f.name for f in files))}")
    _warn("the server now lists as MODDED, and this only takes effect after a restart")
    _info("apply it: dw restart   •   undo it: dw mod disable")


@mod_app.command("rm")
def mod_rm(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Mod name; removes ~mods/<name>* files.")],
) -> None:
    """Remove one mod's files from ~mods."""
    server: Server = ctx.obj
    removed = server.remove_mod(name)
    if not removed:
        _warn(f"nothing in ~mods matched {name}*")
        return
    _step(f"Removed from ~mods: {', '.join(removed)}")
    _info("takes effect on the next restart: dw restart")


@mod_app.command("disable")
def mod_disable(ctx: typer.Context) -> None:
    """Revert to vanilla: move ~mods aside to ~mods.disabled. The easy undo before a game patch."""
    server: Server = ctx.obj
    if server.disable_mods():
        _step("Mods disabled — ~mods moved to ~mods.disabled; the server is vanilla again")
        _info("takes effect on the next restart: dw restart   •   bring them back: dw mod enable")
    else:
        _info("no active mods to disable")


@mod_app.command("enable")
def mod_enable(ctx: typer.Context) -> None:
    """Re-activate mods previously set aside with `dw mod disable`."""
    server: Server = ctx.obj
    if server.enable_mods():
        _step("Mods re-enabled from ~mods.disabled")
        _info("takes effect on the next restart: dw restart")
    else:
        _info("no disabled mods to enable")


def main() -> None:
    try:
        app()
    except RemoteError as error:
        typer.secho(f"error: {error}", fg=typer.colors.RED, err=True)
        raise SystemExit(1) from None
