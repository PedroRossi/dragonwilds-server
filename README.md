# Dragonwilds dedicated server

Official `ghcr.io/runescape/rsdw-dedicated` image, deployed as a **Portainer stack** on a Linux desktop.
Crossplay, up to **6 players** (the official cap). No secrets live in this repo: every per-server value
(owner ID, world name, passwords) is set in the Portainer stack's environment variables.

## Deploy with Portainer
1. Stacks → **Add stack** → name `dragonwilds` → **Repository**.
2. Repository URL: this repo, reference `refs/heads/main`, compose path `compose.yaml`.
   Leave GitOps polling off: a redeploy restarts the server and kicks everyone. Use **Pull and redeploy**
   when you actually want a change applied.
3. **Environment variables**: add every key from `.env.example` (at least `RSDW_OWNER_ID`,
   `RSDW_WORLD_NAME`, `RSDW_PASSWORD` — may be empty — and `RSDW_ADMIN_PASSWORD`).
4. Deploy. First start downloads the server from Steam (several GB). Watch the container logs for
   `Installed Steam build ID` and `Launching RSDragonwildsServer.sh`.

Host prerequisites: idle suspend disabled; the data directories in `compose.yaml` exist and are owned by
uid 1000 (the image runs as that user). Keep the machine powered on while people play.

## Operations CLI (`dw`)
Runs from this repo on any machine that can SSH to the host, and drives Docker there:
```
uv sync              # once; or `uv tool install --editable .` to get `dw` on your PATH
uv run dw key        # current invite code (`dw key | pbcopy` copies it)
uv run dw backup     # verified backup on the host, plus a copy in ~/dragonwilds-backups
uv run dw status     # state, installed vs latest Steam build, world, players online, last autosave
uv run dw update     # after a game patch: restart onto the new build, only if there is one
uv run dw restart    # same, without the build check
```
`restart` and `update` back up first. If anyone is online they ask before kicking them and wait for the next
autosave. Then they stop the game process cleanly (a plain `docker stop` kills it outright because the image's
launcher doesn't pass SIGTERM on), start the container (SteamCMD updates on start), check that the same
world (by GUID) came back and print the new invite code.

Login uses your SSH keys (`ssh-copy-id pedro@192.168.1.5` once), or `DW_SSH_PASSWORD` in the environment
(needs `sshpass`). Defaults match this deployment; override them with `DW_HOST` (`local` when running on
the host itself), `DW_CONTAINER`, `DW_DATA_DIR`, `DW_BACKUP_DIR` and `DW_LOCAL_BACKUP_DIR` (see `dw --help`).

## Router (MikroTik, RouterOS v6)
Give the host a static DHCP lease, then forward both UDP ports to it (replace `HOST_LAN_IP`):
```
/ip firewall nat add chain=dstnat in-interface-list=WAN protocol=udp dst-port=7777 action=dst-nat to-addresses=HOST_LAN_IP to-ports=7777 comment="Dragonwilds game"
/ip firewall nat add chain=dstnat in-interface-list=WAN protocol=udp dst-port=8888 action=dst-nat to-addresses=HOST_LAN_IP to-ports=8888 comment="Dragonwilds beacon"
```
No filter rule is needed with the default configuration (DSTNATed traffic is allowed). The WAN must have a
public IPv4 address; behind CGNAT nobody outside can reach the server.

## Joining
In the game: Worlds → **Public** → search the exact `RSDW_WORLD_NAME` (case-sensitive) → join with
`RSDW_PASSWORD`. Console players must enable online play and crossplay. On the LAN you can also
direct-connect to `HOST_LAN_IP:7777`.

Invite code: the server logs one on every start (it changes on each restart). `dw key` prints it; without
the CLI, search the container logs for `JoinCode` in Portainer, or on the host (`-a` because the log
contains binary bytes):
```
docker logs dragonwilds 2>&1 | grep -a -o 'JoinCode.*value\[[^]]*\]' | tail -1
```

## Worlds
- Fresh: the server creates a Standard world named `RSDW_WORLD_NAME` on first start.
- Custom settings or migrating a co-op world: create/load the world in the game client (wait ~2 min
  after entering so it generates), quit, then with the stack **stopped** copy the `.sav` from
  `%LOCALAPPDATA%\RSDragonwilds\Saved\SaveGames` (Windows) into
  `<data dir>/RSDragonwilds/Saved/SaveGames/` (capital G on Linux) **without renaming it**, empty that folder of
  other saves first, set `RSDW_WORLD_NAME` to the file name without `.sav`, start the stack.
- The server always loads the newest `.sav` in that folder.

## Updates
Clients and server must run the same build or the server disappears from the browser. The entrypoint
updates the game via SteamCMD on every container start, but nothing triggers that start when a patch ships.
`RSDW_AUTO_STOP_ON_UPDATE` relies on Valve's UpToDateCheck API, which answers "Couldn't get app info" for
this app (4019830), so the check never fires and logs nothing. After a game patch, run `dw update`
(about three minutes of downtime).
Bumps of the image tag (`2.0.1`; ghcr tags carry no "v") are done here in git, then **Pull and redeploy** in Portainer.

## Backups
The `backup` sidecar tars `RSDragonwilds/Saved/SaveGames` every 30 minutes into the backups directory
and keeps 14 days. Restore = stop the stack, extract into `<data dir>/RSDragonwilds/Saved/`, start.
`dw backup` (and every `dw restart`/`dw update`) also writes `manual-*.tgz` archives of the whole `Saved/`
folder (logs excluded). The sidecar never prunes these; extract them into `<data dir>/RSDragonwilds/`.

## Config reference
The only server-side settings (`RSDragonwilds/Saved/Config/LinuxServer/DedicatedServer.ini`, generated from the
env vars on every start — do not hand-edit): `OwnerId`, `ServerName`, `DefaultWorldName`, `WorldPassword`,
`AdminPassword`, `AdministratorList`. The game rewrites this file while running (adds `ServerGuid`, drops
`AdminPassword`): that is normal, the password stays active and is rendered again on the next start.
The join code in the logs and the server GUID change on every start. Gameplay settings (difficulty, PvP, etc.)
live in the world save.
Roles: owner (the Owner ID) can ban/unban; admins (password or list) can ban online players; players need the
world name + password.

## Troubleshooting
- Listed but "connection lost" on join → UDP 7777/8888 not reaching the host (router/ISP).
- Not listed → version mismatch (check the build line at the top of the logs) or the mandatory env vars are missing.
- Corrupt files → set `STEAMAPPVALIDATE=1` for one start.
- RAM errors in the log → the 8 GB budget is not available.
