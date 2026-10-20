import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dragonwilds import cli, server

MOD_SCRIPTS = [
    server.MODS_STATE,
    server.MOD_INSTALL,
    server.MOD_REMOVE,
    server.MOD_DISABLE,
    server.MOD_ENABLE,
]


@pytest.mark.parametrize("script", MOD_SCRIPTS)
def test_mod_scripts_are_valid_sh(script: str):
    assert subprocess.run(["sh", "-n", "-c", script], check=False).returncode == 0


def dw(docker: Path, *args: str):
    options = ["--host", "local", "--data-dir", str(docker / "data")]
    return CliRunner().invoke(cli.app, [*options, *args])


def mods_dir(docker: Path) -> Path:
    return docker / "data/RSDragonwilds/Content/Paks/~mods"


def make_dist(tmp_path: Path, stem: str = "shortbow-rapidshot-fix_P") -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    for suffix in (".pak", ".utoc", ".ucas"):
        (dist / f"{stem}{suffix}").write_bytes(b"x" * 10)
    return dist


def test_ls_on_a_vanilla_server(docker: Path):
    result = dw(docker, "mod", "ls")
    assert result.exit_code == 0
    assert "vanilla" in result.stdout


def test_install_lists_and_removes_a_mod(docker: Path, tmp_path: Path):
    assert dw(docker, "mod", "install", str(make_dist(tmp_path))).exit_code == 0
    assert sorted(p.name for p in mods_dir(docker).iterdir()) == [
        "shortbow-rapidshot-fix_P.pak",
        "shortbow-rapidshot-fix_P.ucas",
        "shortbow-rapidshot-fix_P.utoc",
    ]
    listing = dw(docker, "mod", "ls")
    assert "active" in listing.stdout
    assert "shortbow-rapidshot-fix_P.utoc" in listing.stdout

    assert dw(docker, "mod", "rm", "shortbow-rapidshot-fix").exit_code == 0
    # Removing the last mod clears the folder, which drops the server's "modded" flag.
    assert not mods_dir(docker).exists()


def test_install_rejects_paths_without_pak_files(docker: Path, tmp_path: Path):
    (tmp_path / "notes.txt").write_text("not a pak")
    result = dw(docker, "mod", "install", str(tmp_path / "notes.txt"))
    assert result.exit_code == 1
    assert not mods_dir(docker).exists()


def test_disable_then_enable_round_trips(docker: Path, tmp_path: Path):
    dw(docker, "mod", "install", str(make_dist(tmp_path)))

    assert dw(docker, "mod", "disable").exit_code == 0
    assert not mods_dir(docker).exists()
    assert (docker / "data/RSDragonwilds/Content/Paks/~mods.disabled").is_dir()
    assert "disabled" in dw(docker, "mod", "ls").stdout

    assert dw(docker, "mod", "enable").exit_code == 0
    assert mods_dir(docker).is_dir()
    assert not (docker / "data/RSDragonwilds/Content/Paks/~mods.disabled").exists()


def test_disable_on_a_vanilla_server_is_a_noop(docker: Path):
    result = dw(docker, "mod", "disable")
    assert result.exit_code == 0
    assert "no active mods" in result.stdout
