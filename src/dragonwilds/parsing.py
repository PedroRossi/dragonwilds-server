"""Pure parsers for the server's container log and Steam metadata."""

import re
from collections.abc import Iterable

JOIN_CODE = re.compile(r'"JoinCode"\] written with key\[\w*\] value\[([^\]]+)\]')
WORLD_GUID = re.compile(r'"WorldSaveGuid"\] written with key\[\w*\] value\[([^\]]+)\]')
WORLD_NAME = re.compile(r"Loading configured world \[([^\]]+)\]")
INSTALLED_BUILD = re.compile(r'Installed Steam build ID: "?(\d+)')
LOGIN = re.compile(r"Login request: .*Name=(.+?) userId: (\S+)")
JOINED = re.compile(r"Join succeeded: (.+)")
LEFT = re.compile(r"UChannel::CleanUp: .*UniqueId: (\S+)")
NOBODY_ONLINE = "Requested pausing as we have no player connected"
ACF_BUILD = re.compile(r'"buildid"\s+"(\d+)"')
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def last_match(pattern: re.Pattern[str], lines: Iterable[str]) -> str | None:
    """Group 1 of the last line that matches."""
    found = None
    for line in lines:
        if match := pattern.search(line):
            found = match[1]
    return found


def online_players(lines: Iterable[str]) -> list[str]:
    """Players still connected, replaying the run's logins, joins and disconnects."""
    names: dict[str, str] = {}  # EOS user id -> player name
    online: set[str] = set()
    for line in lines:
        if NOBODY_ONLINE in line:
            online.clear()
        elif match := LOGIN.search(line):
            names[match[2]] = match[1]
        elif match := JOINED.search(line):
            online.add(match[1].strip())
        elif match := LEFT.search(line):
            online.discard(names.get(match[1], ""))
    return sorted(online)


def acf_build_id(text: str) -> str | None:
    """Installed build from steamapps/appmanifest_<app>.acf."""
    match = ACF_BUILD.search(text)
    return match[1] if match else None


def public_build_id(payload: dict, app_id: str) -> str:
    """Public-branch build from an api.steamcmd.net /v1/info response."""
    return str(payload["data"][app_id]["depots"]["branches"]["public"]["buildid"])
