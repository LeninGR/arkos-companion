"""Unit tests for the manual cover operation (``rom_operations.set_game_cover``).

Covers copying, naming, gamelist registration, orphan cleanup and journaling.
All operations run inside temporary directories; nothing touches real cards.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arkos_companion import history, rom_operations
from arkos_companion.gamelist_editor import GAMELIST_FILENAME, find_game_node
from arkos_companion.models import GameEntry, OptimizationStatus, SystemFolder


def _patch_journal_path(tmp: str) -> mock._patch:
    """Redirect ``history.history_file_path`` to a temp dir journal."""
    return mock.patch.object(
        history,
        "history_file_path",
        return_value=os.path.join(tmp, history.HISTORY_FILENAME),
    )


def _make_system(tmp: str):
    """Create a fake arcade system folder with one ROM and a gamelist."""
    system_path = os.path.join(tmp, "arcade")
    os.makedirs(system_path)
    with open(os.path.join(system_path, "dragonsh.zip"), "wb") as fh:
        fh.write(b"pk")
    gamelist = os.path.join(system_path, GAMELIST_FILENAME)
    with open(gamelist, "w", encoding="utf-8") as fh:
        fh.write(
            '<?xml version="1.0"?>\n<gameList>\n'
            "  <game>\n"
            "    <path>./dragonsh.zip</path>\n"
            "    <name>Dragon's Heaven</name>\n"
            "  </game>\n"
            "</gameList>\n"
        )
    return system_path, SystemFolder(
        name="arcade", display_name="Arcade", path=system_path
    )


def _entry(system_path: str) -> GameEntry:
    return GameEntry(
        sys_folder="arcade",
        rom_file="dragonsh.zip",
        rom_base="dragonsh",
        title="Dragon's Heaven",
        status=OptimizationStatus.CORRECT,
    )


def _fake_image(tmp_path: str, name: str = "cover.png") -> str:
    path = os.path.join(tmp_path, name)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\nfakepixels")
    return path


def test_set_game_cover_copies_into_images_and_updates_gamelist():
    with tempfile.TemporaryDirectory() as tmp:
        system_path, system = _make_system(tmp)
        source = _fake_image(tmp)
        with _patch_journal_path(tmp):
            result = rom_operations.set_game_cover(system, _entry(system_path), source)

        dest = os.path.join(system_path, "images", "dragonsh.png")
        assert os.path.isfile(dest)
        assert result["image_path"] == dest
        assert result["image_rel"] == "./images/dragonsh.png"

        root = open(os.path.join(system_path, GAMELIST_FILENAME), encoding="utf-8").read()
        assert "./images/dragonsh.png" in root


def test_set_game_cover_keeps_source_extension():
    with tempfile.TemporaryDirectory() as tmp:
        system_path, system = _make_system(tmp)
        source = _fake_image(tmp, "portada.jpg")
        with _patch_journal_path(tmp):
            result = rom_operations.set_game_cover(system, _entry(system_path), source)

        assert result["image_path"].endswith("dragonsh.jpg")
        assert os.path.isfile(result["image_path"])


def test_set_game_cover_replaces_previous_different_file():
    with tempfile.TemporaryDirectory() as tmp:
        system_path, system = _make_system(tmp)
        old_cover = os.path.join(system_path, "images", "dragonsh.jpg")
        os.makedirs(os.path.dirname(old_cover))
        with open(old_cover, "wb") as fh:
            fh.write(b"oldjpeg")
        entry = _entry(system_path)
        entry.image_path = old_cover
        source = _fake_image(tmp, "nueva.png")
        with _patch_journal_path(tmp):
            result = rom_operations.set_game_cover(system, entry, source)

        assert not os.path.exists(old_cover), "previous cover must be removed"
        assert result["image_path"] == os.path.join(
            system_path, "images", "dragonsh.png"
        )
        assert result["removed"] == ["images/dragonsh.jpg"]


def test_set_game_cover_same_file_is_noop_copy():
    with tempfile.TemporaryDirectory() as tmp:
        system_path, system = _make_system(tmp)
        # The user picks the file that is already the game's cover.
        dest = os.path.join(system_path, "images", "dragonsh.png")
        os.makedirs(os.path.dirname(dest))
        with open(dest, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\nalready-there")
        entry = _entry(system_path)
        entry.image_path = dest
        with _patch_journal_path(tmp):
            result = rom_operations.set_game_cover(system, entry, dest)

        assert result["image_path"] == dest
        assert os.path.isfile(dest)


def test_set_game_cover_journals_media_manual_entry():
    with tempfile.TemporaryDirectory() as tmp:
        system_path, system = _make_system(tmp)
        source = _fake_image(tmp, "cover.png")
        with _patch_journal_path(tmp):
            rom_operations.set_game_cover(system, _entry(system_path), source)
            entries = history.load_entries()

        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == history.ACTION_MEDIA_MANUAL
        assert entry["system"] == "arcade"
        assert entry["rom_file"] == "dragonsh.zip"
        assert entry["details"]["source"] == "cover.png"
        assert entry["details"]["image"] == "./images/dragonsh.png"


def test_set_game_cover_missing_source_raises():
    with tempfile.TemporaryDirectory() as tmp:
        system_path, system = _make_system(tmp)
        try:
            rom_operations.set_game_cover(
                system, _entry(system_path), os.path.join(tmp, "no.png")
            )
        except FileNotFoundError as exc:
            assert "no.png" in str(exc)
        else:
            raise AssertionError("expected FileNotFoundError")