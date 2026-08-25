"""Tests for the scanner's game-file filter (BIOS + AppleDouble garbage).

Hermetic: all scans run against temp directories; the real card is never
touched.  ``scan_systems`` re-detects the arcade target from the temp root,
which is harmless (it defaults to "arcade").
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arkos_companion.models import SystemFolder
from arkos_companion.scanner import _is_rom_file, load_system_games, scan_systems


def _touch(directory: str, name: str) -> None:
    with open(os.path.join(directory, name), "w") as fh:
        fh.write("x")


def test_is_rom_file_filters_bios_and_apple_double():
    assert _is_rom_file("dmnfrnt.zip") is True
    assert _is_rom_file("kov.zip") is True
    assert _is_rom_file("pgm.zip") is False            # BIOS
    assert _is_rom_file("neogeo.zip") is False         # BIOS
    assert _is_rom_file("._pgm.zip") is False          # AppleDouble
    assert _is_rom_file("._dmnfrnt.zip") is False      # AppleDouble
    assert _is_rom_file("._kov.zip") is False          # AppleDouble
    assert _is_rom_file("gamelist.xml") is False       # not a ROM extension
    assert _is_rom_file("readme.txt") is False


def test_load_system_games_skips_bios_and_apple_double():
    with tempfile.TemporaryDirectory() as tmp:
        _touch(tmp, "dmnfrnt.zip")
        _touch(tmp, "pgm.zip")          # BIOS must NOT appear
        _touch(tmp, "._pgm.zip")        # AppleDouble must NOT appear
        _touch(tmp, "._dmnfrnt.zip")    # AppleDouble must NOT appear
        _touch(tmp, "kov.zip")

        system = SystemFolder(name="arcade", display_name="Arcade",
                              path=tmp, rom_count=0)
        games = load_system_games(system)
        rom_files = sorted(g.rom_file for g in games)
        assert rom_files == ["dmnfrnt.zip", "kov.zip"]


def test_scan_systems_rom_count_excludes_support_files():
    with tempfile.TemporaryDirectory() as tmp:
        arcade_dir = os.path.join(tmp, "arcade")
        os.makedirs(arcade_dir)
        _touch(arcade_dir, "dmnfrnt.zip")
        _touch(arcade_dir, "theglad.zip")
        _touch(arcade_dir, "pgm.zip")
        _touch(arcade_dir, "._theglad.zip")

        systems = scan_systems(tmp)
        arcade = next(s for s in systems if s.name == "arcade")
        assert arcade.rom_count == 2
