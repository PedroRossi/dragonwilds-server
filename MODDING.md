# Modding & tuning the Dragonwilds server

A practical guide to what you can and can't change on this dedicated server, written for
**this build** (UE 5.6.1, `++dominion+staging` CL-248854, Steam build `25811348`, the
`ghcr.io/runescape/rsdw-dedicated` image), not UE in general.

## Reality check first

Dragonwilds has **no official modkit, SDK, or editor for its content**, but it is **not closed to
mods**: the engine mounts community **pak content mods**, and Jagex's community guidelines welcome
fan mods. What's possible here splits three ways:

- **Config & launch tuning** — fully supported, no tools (below).
- **Content pak mods** — override the game's cooked assets (items, data assets, Blueprints) by
  dropping a pak in `Content/Paks/~mods/`. Supported on this Linux server; the toolchain and the
  `dw mod` install/undo commands are in [`mods/`](mods/README.md).
- **Compiled C++ / Lua hooks** — **not available on this build.** UE4SS (the Lua/C++ runtime
  loader) is Windows-only and fails silently on our Linux server, and nothing here edits the
  compiled binary. A bug that lives in C++ can therefore only be *worked around* at the content
  layer, not fixed directly.

Everything the server runs is one ~219 MB binary (`RSDragonwildsServer-Linux-Shipping`) plus
IoStore content containers (`RSDragonwilds-LinuxServer.{pak,utoc,ucas}`, ~4.6 GB). A pak in
`~mods` mounts *over* that base — `~mods` is an engine convention and need not appear in
`Engine.ini`'s `Paths=` list.

## The config surface that actually exists

The server reads three files under `RSDragonwilds/Saved/Config/LinuxServer/`:

| File | Backing class | What's in it |
| --- | --- | --- |
| `DedicatedServer.ini` | `/Script/Dominion.DedicatedServerSettings` | Identity & access: `ServerName`, `DefaultWorldName`, `WorldPassword`, `OwnerId`, `AdministratorList`, `PlatformPolicy` (Crossplay), `bAllowSendingCrashDumps`, and the persisted `KnownPlayerList` (bans, privileges). |
| `Engine.ini` | UE `[Core.System]`, `[ConsoleVariables]`, etc. | Engine/network/log config and the content search `Paths=` list (all under `Engine/Content` and `RSDragonwilds/Plugins/*/Content`). |
| `GameUserSettings.ini` | `/Script/Dominion.DominionUserSettings` | A client-oriented settings object (camera, audio, chest defaults). Largely inert on a dedicated server. |

> **All three carry a `;METADATA=(Diff=true, UseCommands=true)` header, which means the
> server rewrites them on save.** Hand-edits can be merged away or overwritten, and the whole
> `Saved/` tree is wiped if the data volume is reset. So **don't treat edited config files as
> your source of truth** — use the two durable mechanisms below.

## 1. Server identity, password, admins → Portainer env (durable)

The image **regenerates `DedicatedServer.ini` from the stack's environment variables on every
start**. That's the intended, persistent way to set these — editing the file directly won't
survive a restart. The keys are documented in `.env.example`; the live mapping is in
`compose.yaml`:

- `RSDW_SERVER_NAME`, `RSDW_WORLD_NAME`, `RSDW_PASSWORD`, `RSDW_ADMIN_PASSWORD`
- `RSDW_OWNER_ID`, `RSDW_ADMINS` (comma-separated Player IDs that get admin without the password)

Change them in **Portainer → the `dragonwilds` stack → Environment variables**, then
**Pull and redeploy**. Bans and the known-player list persist inside `DedicatedServer.ini`
in the data volume (they're managed in-game by the owner/admins, not by env).

## 2. Engine, network & log tuning → `RSDW_ADDITIONAL_ARGS` (durable)

The launcher runs the server as `RSDragonwildsServer-Linux-Shipping RSDragonwilds "$@"`, and
the image appends whatever you put in **`RSDW_ADDITIONAL_ARGS`** as those `$@` args. This is
the clean place for launch-time tuning because it lives in env and survives redeploys without
touching any file. Useful, low-risk examples:

```
# Quieter or louder logging (verify a change landed by grepping the container log)
-LogCmds="LogNet Warning, DominionLog Display"

# Console-variable overrides at boot (networking/perf). Syntax: -dpcvars="a=1,b=2"
-dpcvars="net.MaxConnectionsToAllowInGame=6"

# Force an ini value without editing the (rewritten) file on disk
-ini:Engine:[/Script/Engine.GameNetworkManager]:MaxDynamicBandwidth=8000
```

Set it in the stack env, redeploy, then confirm the server honoured it:

```
uv run dw backup                      # snapshot first — see "Test safely" below
# after redeploy:
docker logs dragonwilds 2>&1 | grep -ai "CVar\|LogInit: Command Line"
```

Caveats, stated honestly:
- This is a **Shipping** build, so the in-game console and `-ExecCmds=` / cheat commands are
  likely compiled out. CVar overrides via `-dpcvars=` and `-ini:` generally still apply — but
  **verify from the log** rather than assuming; if a setting doesn't appear, it isn't supported
  on this build.
- CVars tune performance/networking/logging. They are **not** a gameplay-rules editor.

## 3. Gameplay rules → the world save

Difficulty, PvP, and similar gameplay settings are **baked into the world**, not into server
config. Set them when you create/load the world in the game client, then host that save (see
the "Worlds" section of the [README](README.md)). The server always loads the newest `.sav`
in `RSDragonwilds/Saved/SaveGames/`.

## Content mods (pak mods)

You **can** override the game's cooked content with a pak mod, and this repo is set up for it —
full recipe in [`mods/README.md`](mods/README.md).

- **Mechanism.** Drop a `.pak` (+ its `.utoc`/`.ucas`) in `RSDragonwilds/Content/Paks/~mods/`
  while the server is stopped; the engine mounts it over the base game. The server then lists as
  **modded** in the browser while that folder exists — even an empty `~mods` trips it — so going
  back to vanilla means removing the folder, not just emptying it (`dw mod disable` does this).
- **No AES key needed.** The *server* content containers are unencrypted (flags `Indexed` only,
  zero key GUID) — their asset index reads in plaintext. (The "get the key from Discord" advice
  online is for the *client* build.) You still need a **`.usmap`** to read/edit asset properties,
  since UE5 serializes them unversioned; it's shared in the Dragonwilds FModel community.
- **What a pak can and can't do.** It overrides **content** — data assets, Blueprints, item defs,
  meshes. It **cannot** patch compiled C++, so a C++ crash (e.g. the shortbow one) is addressed by
  changing the *content that triggers it*, not the code.
- **Toolchain.** Extract/repack with [retoc](https://github.com/trumank/retoc) (no editor, no AES
  key), edit the `.uasset` with [UAssetGUI](https://github.com/atenfyr/UAssetGUI) + the `.usmap`.
  This guide does **not** cover defeating pak signing/encryption or patching the shipped binary —
  none of which is needed, since the sanctioned `~mods` path works.

Worked example: [`mods/shortbow-rapidshot-fix/`](mods/shortbow-rapidshot-fix/).

### Install & undo (`dw mod`)

```
uv run dw mod ls                        # what's in the server's ~mods folder
uv run dw mod install mods/<name>/dist  # copy a built pak in (does NOT restart)
uv run dw restart                       # apply it
uv run dw mod disable                   # revert to vanilla (move ~mods aside) — the easy undo
uv run dw mod enable                    # bring them back
uv run dw mod rm <name>                 # remove one mod
```

`dw mod install` never restarts on its own, and `dw update` warns when mods are active, because a
new game build usually breaks mod paks. The rebuild-after-a-patch flow is in
[`mods/README.md`](mods/README.md).

## The real power here: operational "mods"

The most effective way to extend this server is **around** the binary, not inside it — exactly
what this repo already does:

- **`compose.yaml` sidecars** — the `backup` and `watchdog` containers add behavior (periodic
  save tarballs; auto-restart + crash snapshot when the game process dies) without changing the
  game. Add your own sidecar the same way: give it the Docker socket or a read-only mount and
  have it react to server state.
- **The `dw` CLI** (`src/dragonwilds/`) — invite codes, verified backups, clean restarts,
  build-aware updates, health/status. Extend it with new subcommands for whatever you want to
  automate (scheduled restarts, Discord notifications, player-count metrics).
- **Scheduled jobs** — e.g. a nightly `dw backup --label nightly`, or `dw update` after a patch.

This is where new server behavior actually belongs on a closed build, and it's safe, reversible
and version-controlled.

## Test safely

- **Never experiment on the live world.** Run `uv run dw backup` first (and every `dw restart`/
  `dw update` already backs up), or point a throwaway stack at an empty `RSDW_WORLD_NAME`.
- A config change that won't boot shows up in `docker logs dragonwilds` near the
  `LogInit: Command Line` / build line at the top. If the server drops out of the in-game
  browser after a change, suspect a bad arg or a version mismatch (see README → Troubleshooting).
- Keep changes in env (`RSDW_ADDITIONAL_ARGS`) and in git, not in the volatile `Saved/Config`
  files.
- **Test a new pak mod on a throwaway world first** (empty `RSDW_WORLD_NAME`); a pak built for an
  older build can stop the server booting. `dw mod disable` reverts to vanilla instantly.
