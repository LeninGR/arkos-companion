"""Unit tests for the compatibility database (``arkos_companion.compat_db``).

Pure bytes: the compat-db assertions never touch the disk or Qt.  The four
UI tests at the bottom are hermetic (offscreen QApplication, no disk).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arkos_companion.compat_db import (
    ARCADE_COMPATIBILITY,
    compute_status,
    detect_arcade_target,
    get_compat,
    recommended_action,
    set_arcade_target,
)
from arkos_companion.models import GameEntry, OptimizationStatus

_APP = None


def _qapp():
    """Lazily create the single offscreen QApplication shared by UI tests."""
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication

        _APP = QApplication([])
    return _APP


# ---------------------------------------------------------------------------
# Group A - IGS PGM games are RED in MAME 2003 (must move to FBNeo).
# ---------------------------------------------------------------------------

def test_dmnfrnt_mame2003_needs_optimization():
    assert compute_status("mame2003", "dmnfrnt") == (
        OptimizationStatus.NEEDS_OPTIMIZATION
    )
    assert recommended_action("mame2003", "dmnfrnt") == "arcade"


def test_dmnfrnt_arcade_correct():
    assert compute_status("arcade", "dmnfrnt") == OptimizationStatus.CORRECT
    assert recommended_action("arcade", "dmnfrnt") is None


def test_dmnfrnt_fbneo_ghost_folder_needs_optimization():
    # "fbneo" is NOT a system the console exposes (it shows "arcade"); a ROM
    # stranded in a ghost fbneo folder must be flagged for a move to arcade.
    assert compute_status("fbneo", "dmnfrnt") == (
        OptimizationStatus.NEEDS_OPTIMIZATION
    )
    assert recommended_action("fbneo", "dmnfrnt") == "arcade"


def test_pgm_games_need_optimization_in_mame2003():
    for rom_base in ("theglad", "martmast", "kov"):
        assert compute_status("mame2003", rom_base) == (
            OptimizationStatus.NEEDS_OPTIMIZATION
        ), rom_base
        assert recommended_action("mame2003", rom_base) == "arcade", rom_base


# ---------------------------------------------------------------------------
# Group B - CPS-3 games are YELLOW (WARNING) in MAME 2003, GREEN in arcade.
# ---------------------------------------------------------------------------

def test_cps3_mame2003_warning():
    for rom_base in ("sfiii3", "jojoy"):
        assert compute_status("mame2003", rom_base) == OptimizationStatus.WARNING, rom_base
        assert recommended_action("mame2003", rom_base) == "arcade", rom_base


def test_cps3_arcade_correct():
    for rom_base in ("sfiii3", "jojoy"):
        assert compute_status("arcade", rom_base) == OptimizationStatus.CORRECT, rom_base
        assert recommended_action("arcade", rom_base) is None, rom_base


def test_cps3_fbneo_ghost_folder_needs_optimization():
    for rom_base in ("sfiii3", "jojoy"):
        assert compute_status("fbneo", rom_base) == (
            OptimizationStatus.NEEDS_OPTIMIZATION
        ), rom_base
        assert recommended_action("fbneo", rom_base) == "arcade", rom_base


# ---------------------------------------------------------------------------
# Group C - optimal in MAME 2003 / Neo Geo (GREEN, do not move).
# ---------------------------------------------------------------------------

def test_mslug_family_correct_in_mame2003_and_neogeo():
    for rom_base in ("mslug", "mslug2", "mslug3", "mslug4", "mslug5"):
        for folder in ("mame2003", "neogeo"):
            assert compute_status(folder, rom_base) == (
                OptimizationStatus.CORRECT
            ), (folder, rom_base)
            assert recommended_action(folder, rom_base) is None, (folder, rom_base)


def test_arcade_cps_games_correct_in_mame2003():
    for rom_base in ("mvsc", "sf2", "sf2ce", "sf2hf", "sf2j"):
        assert compute_status("mame2003", rom_base) == (
            OptimizationStatus.CORRECT
        ), rom_base
        assert recommended_action("mame2003", rom_base) is None, rom_base


# ---------------------------------------------------------------------------
# Unknown games.
# ---------------------------------------------------------------------------

def test_unknown_game_unknown():
    assert compute_status("arcade", "zzznope") == OptimizationStatus.UNKNOWN
    assert compute_status("mame2003", "zzznope") == OptimizationStatus.UNKNOWN
    assert recommended_action("arcade", "zzznope") is None


def test_get_compat_defensive_copy_includes_warning_in():
    entry = get_compat("sfiii3")
    assert entry is not None
    assert "warning_in" in entry
    entry["warning_in"].append("hacked")
    fresh = get_compat("sfiii3")
    assert fresh["warning_in"] == ["mame2003"]


# ---------------------------------------------------------------------------
# UI: WARNING rendering in the game list (offscreen).
# ---------------------------------------------------------------------------

def _warning_entry(folder="mame2003", rom_base="sfiii3") -> GameEntry:
    return GameEntry(
        sys_folder=folder,
        rom_file=f"{rom_base}.zip",
        rom_base=rom_base,
        title=ARCADE_COMPATIBILITY[rom_base]["name"],
        status=compute_status(folder, rom_base),
        compat=get_compat(rom_base),
    )


def test_warning_label_text():
    from arkos_companion.ui.game_list import GameListPanel

    _qapp()
    entry = _warning_entry()
    assert entry.status == OptimizationStatus.WARNING
    assert GameListPanel._status_label(entry) == "Se recomienda optimizar"


def test_warning_item_uses_warn_color_and_suffix():
    from PyQt6.QtWidgets import QListWidgetItem

    from arkos_companion.ui.game_list import (
        GameListPanel,
        _COLOR_WARN,
    )

    _qapp()
    panel = GameListPanel()
    panel.set_games([_warning_entry()])
    item: QListWidgetItem = panel._list.item(0)
    assert item.foreground().color() == _COLOR_WARN
    text = item.text()
    assert "⚠" in text
    assert "[⚠ optimizar → arcade]" in text


# ---------------------------------------------------------------------------
# UI: Details panel button text + optimize button enable logic (offscreen).
# ---------------------------------------------------------------------------

def test_details_edit_button_text():
    from arkos_companion.ui.details_panel import DetailsPanel

    _qapp()
    panel = DetailsPanel()
    assert panel._edit_button.text() == "Actualizar Metadata"


def test_optimize_button_enabled_for_warning():
    from arkos_companion.ui.details_panel import DetailsPanel

    _qapp()
    panel = DetailsPanel()

    warning = _warning_entry("mame2003", "sfiii3")
    assert warning.status == OptimizationStatus.WARNING
    panel.show_entry(warning)
    assert panel._optimize_button.isEnabled() is True

    correct = _warning_entry("arcade", "sfiii3")
    assert correct.status == OptimizationStatus.CORRECT
    panel.show_entry(correct)
    assert panel._optimize_button.isEnabled() is False


# ---------------------------------------------------------------------------
# Dynamic arcade target detection (community cards with different layouts).
# ---------------------------------------------------------------------------

def _make_root(folders_roms: dict, cfg: str = None) -> str:
    """Build a fake roms_root: {folder_name: [rom files]}, optional es_systems.cfg."""
    import tempfile

    root = tempfile.mkdtemp(prefix="arkos_cfg_")
    for folder, roms in folders_roms.items():
        os.makedirs(os.path.join(root, folder), exist_ok=True)
        for rom in roms:
            with open(os.path.join(root, folder, rom), "w") as fh:
                fh.write("x")
    if cfg is not None:
        with open(os.path.join(root, "es_systems.cfg"), "w", encoding="utf-8") as fh:
            fh.write(cfg)
    return root


_CFG = """<?xml version="1.0"?>
<systemList>
  <system>
    <name>{name}</name>
    <fullname>{fullname}</fullname>
    <path>{path}</path>
    <extension>.zip</extension>
    <command>%EMULATOR%</command>
    <platform>{platform}</platform>
  </system>
</systemList>
"""


def test_detect_arcade_target_cfg_arcade_system():
    root = _make_root({}, cfg=_CFG.format(name="arcade", fullname="Arcade",
                                          path="./arcade", platform="arcade"))
    try:
        assert detect_arcade_target(root) == "arcade"
    finally:
        set_arcade_target("arcade")


def test_detect_arcade_target_cfg_platform_arcade_uses_system_name():
    # Some firmwares name the system "fbneo" but mark the platform arcade.
    root = _make_root({}, cfg=_CFG.format(name="fbneo", fullname="FBNeo",
                                          path="./fbneo", platform="arcade"))
    try:
        assert detect_arcade_target(root) == "fbneo"
    finally:
        set_arcade_target("arcade")


def test_detect_arcade_target_cfg_path_arcade_uses_system_name():
    root = _make_root({}, cfg=_CFG.format(name="fba", fullname="FBA",
                                          path="./arcade", platform="whatever"))
    try:
        assert detect_arcade_target(root) == "fba"
    finally:
        set_arcade_target("arcade")


def test_detect_arcade_target_cfg_corrupt_falls_back_to_folders():
    root = _make_root({"arcade": ["game.zip"]}, cfg="esto no es xml {{")
    try:
        assert detect_arcade_target(root) == "arcade"
    finally:
        set_arcade_target("arcade")


def test_detect_arcade_target_folder_heuristics():
    # arcade with ROMs wins over everything (canonical).
    try:
        root = _make_root({"arcade": ["a.zip"], "fbneo": ["b.zip"]})
        assert detect_arcade_target(root) == "arcade"
        # only fbneo has ROMs -> the firmware uses fbneo as the system.
        root = _make_root({"fbneo": ["b.zip"], "arcade": []})
        assert detect_arcade_target(root) == "fbneo"
        # empty card -> canonical default.
        root = _make_root({})
        assert detect_arcade_target(root) == "arcade"
    finally:
        set_arcade_target("arcade")


def test_arcade_target_dynamic_switch_moves_pgm_games():
    try:
        set_arcade_target("fbneo")
        assert compute_status("fbneo", "dmnfrnt") == OptimizationStatus.CORRECT
        assert recommended_action("mame2003", "dmnfrnt") == "fbneo"
        assert recommended_action("mame2003", "kov") == "fbneo"
        assert recommended_action("mame2003", "sfiii3") == "fbneo"
    finally:
        set_arcade_target("arcade")


def test_arcade_target_does_not_affect_mame_games():
    try:
        set_arcade_target("fbneo")
        # Group C keeps its static mame2003 recommendation.
        assert compute_status("mame2003", "mslug") == OptimizationStatus.CORRECT
        assert recommended_action("mame2003", "mslug") is None
        assert recommended_action("mame2003", "mvsc") is None
        assert compute_status("mame2003", "sf2") == OptimizationStatus.CORRECT
    finally:
        set_arcade_target("arcade")