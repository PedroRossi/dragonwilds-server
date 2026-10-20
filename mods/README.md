# Mods & patches

Server-side **pak content mods** for the Dragonwilds dedicated server, plus the build/deploy/undo
workflow. Read [`../MODDING.md`](../MODDING.md) first for what is and isn't possible on this build;
this file is the hands-on recipe.

Short version: on our **Linux** server you can override the game's **cooked content** (data assets,
Blueprints, item defs) with a pak dropped in `Content/Paks/~mods/`. You **cannot** change compiled
C++ here (UE4SS is Windows-only). So a C++ crash is *worked around* by editing the content that
triggers it — that's exactly what [`shortbow-rapidshot-fix/`](shortbow-rapidshot-fix/) does.

## One-time setup

- **retoc** — converts IoStore `.utoc`/`.ucas` ↔ legacy `.uasset`. Prebuilt binaries:
  <https://github.com/trumank/retoc/releases>. Point `RETOC` at the binary.
- **The game's `Content/Paks`** — the build scripts read `RSDragonwilds-LinuxServer.utoc` **and**
  `.ucas` (the 4.6 GB data file must be present). Copy them off the host, or run the build there.
  The server content is **not encrypted**, so no AES key is needed.
- **A `.usmap` + a uasset editor** — *only for the edit step*. UE5 stores asset properties
  unversioned, so to read/change them you need the Dragonwilds mappings file (`Mappings.usmap`,
  shared in the FModel community — see the [wiki FModel guide](https://dragonwilds.runescape.wiki/w/User:Keplare/FModel_Guide))
  and an editor like [UAssetGUI](https://github.com/atenfyr/UAssetGUI). retoc's extract/repack
  themselves do **not** need the `.usmap`.

Nothing here is committed to git (see `.gitignore`): extracted/edited/built assets are Jagex's
copyrighted content, and the `.usmap` is community-redistributed. We commit only the recipe.

## Build a mod

Each mod is a folder with a `build.sh`, a `mod.toml` (metadata), and `NOTES.md` (what it changes).
The pipeline:

```bash
cd mods/<name>
# 1. Extract the assets you want to change (needs the full container):
RETOC=/path/to/retoc GAME_PAKS=/path/to/RSDragonwilds/Content/Paks ./build.sh extract
# 2. Edit the target .uasset in UAssetGUI using the Dragonwilds .usmap (see NOTES.md for the
#    exact asset and property). Then trim work/legacy/ down to just the asset(s) you edited,
#    keeping their folder layout — a patch should override only what it changes.
# 3. Repack into a patch pak (dist/<name>_P.utoc + .ucas):
RETOC=/path/to/retoc ./build.sh pack
```

The `_P` suffix marks it a *patch* container so it mounts over the base game.

## Deploy, apply, and undo (`dw mod`)

The `dw` CLI manages the server's `~mods` folder over SSH. **`install` never restarts on its own**
— the server only picks mods up on a restart.

```bash
uv run dw mod ls                        # what's installed, and whether it's active or disabled
uv run dw mod install mods/<name>/dist  # copy the built pak into ~mods (no restart)
uv run dw restart                       # apply it
uv run dw mod rm <name>                 # remove one mod's files
uv run dw mod disable                   # revert to vanilla: move ~mods aside (the easy undo)
uv run dw mod enable                    # bring a disabled set back
```

Having **any** pak in `~mods` flags the server as **modded** in the public browser (even an empty
folder does), so `disable`/`rm` delete the folder when it empties to clear the flag.

## After a game patch (important)

A new game build almost always breaks mod paks, and an incompatible pak can stop the server
booting. So mods are **not** fire-and-forget across updates:

1. Before updating, or if the server won't come back after one: `uv run dw mod disable`.
   (`dw update` warns you when mods are active, and backs up first either way.)
2. Rebuild each mod against the new build: re-run `./build.sh extract`, redo the edit against the
   new assets, `./build.sh pack`, bump `game_build` in `mod.toml`.
3. `uv run dw mod install …` the fresh pak and `uv run dw restart`.

Always test a rebuilt mod on a throwaway world (empty `RSDW_WORLD_NAME`) before the live one.

## Mods here

- [`shortbow-rapidshot-fix/`](shortbow-rapidshot-fix/) — neutralizes the shortbow **Rapidshot**
  special that segfaults the server on a player projectile hit. A content-layer workaround for a
  game-code bug; the real fix must come from Jagex.
