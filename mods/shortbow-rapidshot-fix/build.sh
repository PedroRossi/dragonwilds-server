#!/usr/bin/env bash
# Build the shortbow-rapidshot-fix pak mod. See NOTES.md for the crash and the exact edit.
#
# Needs: retoc (https://github.com/trumank/retoc) and the game's Content/Paks (the .utoc AND the
# ~4.6 GB .ucas). The edit step also needs a Dragonwilds Mappings.usmap + a uasset editor
# (UAssetGUI); retoc itself does not. The server content is unencrypted, so no AES key.
#
#   RETOC=/path/to/retoc GAME_PAKS=/path/to/RSDragonwilds/Content/Paks ./build.sh extract
#   # ...edit work/legacy/.../PA_RangedAttack_Shortbow_Rapidshot.uasset in UAssetGUI (uses .usmap),
#   #    then trim work/legacy/ to just the asset(s) you changed, keeping their folders...
#   RETOC=/path/to/retoc ./build.sh pack
#   # then: uv run dw mod install dist && uv run dw restart   (undo: uv run dw mod disable)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
name=shortbow-rapidshot-fix
version=UE5_6
filter=${FILTER:-Shortbow}            # extraction name filter; the crash asset matches "Shortbow"
work=$here/work/legacy
dist=$here/dist

: "${RETOC:?set RETOC to the retoc binary (https://github.com/trumank/retoc/releases)}"

case "${1:-}" in
  extract)
    : "${GAME_PAKS:?set GAME_PAKS to RSDragonwilds/Content/Paks (needs both .utoc and .ucas)}"
    rm -rf "$work"
    mkdir -p "$work"
    "$RETOC" to-legacy --version "$version" -f "$filter" "$GAME_PAKS" "$work"
    echo
    echo "Extracted to: $work"
    echo "Next: edit the Rapidshot asset per NOTES.md with UAssetGUI + the Dragonwilds .usmap,"
    echo "      trim $work to just the edited asset(s), then: RETOC=$RETOC $0 pack"
    ;;
  pack)
    [ -d "$work" ] || { echo "run '$0 extract' first" >&2; exit 1; }
    mkdir -p "$dist"
    # _P marks a patch container so it mounts over the base game.
    "$RETOC" to-zen --version "$version" "$work" "$dist/${name}_P.utoc"
    echo
    echo "Built:"
    ls -la "$dist"
    echo "Deploy (no restart):         uv run dw mod install \"$dist\""
    echo "Apply:                       uv run dw restart"
    echo "Undo (e.g. before a patch):  uv run dw mod disable"
    ;;
  *)
    echo "usage: [RETOC=...] [GAME_PAKS=...] $0 {extract|pack}" >&2
    exit 2
    ;;
esac
