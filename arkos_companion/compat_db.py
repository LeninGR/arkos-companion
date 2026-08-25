"""Internal compatibility database for arcade ROMs on the RK3326 (R36S).

Pure Python module (no Qt imports).  The database maps a ROM base name (the
stem of the zip file, lowercased) to metadata about the real game, the required
BIOS files, the ArkOS system folders where it runs acceptably on the RK3326
chip, the recommended core and folder for optimization, folders where the game
is known-bad, and a short Spanish note shown to the user.
"""

from __future__ import annotations

import copy
import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from arkos_companion.models import OptimizationStatus

# Human-friendly labels for ArkOS system folder names.
ARCADE_FOLDER_ALIASES: Dict[str, str] = {
    "mame2003": "MAME 2003",
    "fbneo": "FBNeo",
    "arcade": "Arcade (FBNeo)",
    "neogeo": "Neo Geo",
    "pgm": "PGM",
    "gba": "Game Boy Advance",
    "gb": "Game Boy",
    "gbc": "Game Boy Color",
    "nes": "NES",
    "snes": "SNES",
    "n64": "Nintendo 64",
    "psx": "PSX",
    "psp": "PSP",
    "nds": "Nintendo DS",
    "gen": "Sega Genesis",
    "sms": "Sega Master System",
    "gg": "Sega Game Gear",
    "gamegear": "Sega Game Gear",
    "pce": "PC Engine",
    "wonderswan": "WonderSwan",
    "atari2600": "Atari 2600",
}

# keyed by ROM base name (zip stem, lowercase).
ARCADE_COMPATIBILITY: Dict[str, dict] = {
    # ------------------------------------------------------------------ #
    # Group A - IGS PGM.  Required for FBNeo (RED in MAME 2003, the
    # optimizer button stays active and the target is "arcade", the
    # system folder the console actually exposes for FBNeo).
    # ------------------------------------------------------------------ #
    "dmnfrnt": {
        "name": "Demon Front",
        "system": "IGS PGM",
        "bios": ["pgm.zip"],
        "works_in": ["arcade"],
        "best_core": "fbneo",
        "recommended_folder": "arcade",
        "bad_in": ["mame2003"],
        "note": "Placa IGS PGM: requiere el BIOS pgm.zip. No funciona con "
                "MAME 2003; usar FBNeo (carpeta arcade).",
    },
    "theglad": {
        "name": "The Gladiator",
        "system": "IGS PGM",
        "bios": ["pgm.zip"],
        "works_in": ["arcade"],
        "best_core": "fbneo",
        "recommended_folder": "arcade",
        "bad_in": ["mame2003"],
        "note": "Placa IGS PGM: requiere el BIOS pgm.zip. No funciona con "
                "MAME 2003; usar FBNeo (carpeta arcade).",
    },
    "martmast": {
        "name": "Martial Masters",
        "system": "IGS PGM",
        "bios": ["pgm.zip"],
        "works_in": ["arcade"],
        "best_core": "fbneo",
        "recommended_folder": "arcade",
        "bad_in": ["mame2003"],
        "note": "Placa IGS PGM: requiere el BIOS pgm.zip. No funciona con "
                "MAME 2003; usar FBNeo (carpeta arcade).",
    },
    "kov": {
        "name": "Knights of Valour",
        "system": "IGS PGM",
        "bios": ["pgm.zip"],
        "works_in": ["arcade"],
        "best_core": "fbneo",
        "recommended_folder": "arcade",
        "bad_in": ["mame2003"],
        "note": "Placa IGS PGM: requiere el BIOS pgm.zip. No funciona con "
                "MAME 2003; usar FBNeo (carpeta arcade).",
    },
    # ------------------------------------------------------------------ #
    # Cave shooters - optimal in MAME 2003 (GREEN, do not move).
    # ------------------------------------------------------------------ #
    "ddonpach": {
        "name": "DoDonPachi",
        "system": "Cave (hardware propio)",
        "bios": [],
        "works_in": ["mame2003", "fbneo", "arcade"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Corre a 60 fps en RK3326 con MAME 2003 o FBNeo.",
    },
    # ------------------------------------------------------------------ #
    # Group B - Capcom CPS-3.  Strongly recommended for FBNeo (YELLOW /
    # WARNING in MAME 2003: it runs but with audio stutters).
    # ------------------------------------------------------------------ #
    "sfiii3": {
        "name": "Street Fighter III: 3rd Strike",
        "system": "CPS-3",
        "bios": [],
        "works_in": ["arcade"],
        "best_core": "fbneo",
        "recommended_folder": "arcade",
        "warning_in": ["mame2003"],
        "bad_in": [],
        "note": "CPS-3 requiere FBNeo para 60 FPS estables; en MAME 2003 "
                "sufre tirones de audio. Mover a la carpeta arcade.",
    },
    "jojoy": {
        "name": "JoJo's Bizarre Adventure",
        "system": "CPS-3",
        "bios": [],
        "works_in": ["arcade"],
        "best_core": "fbneo",
        "recommended_folder": "arcade",
        "warning_in": ["mame2003"],
        "bad_in": [],
        "note": "CPS-3 requiere FBNeo para 60 FPS estables; en MAME 2003 "
                "sufre tirones de audio. Mover a la carpeta arcade.",
    },
    # ------------------------------------------------------------------ #
    # Group C - optimal in MAME 2003 (GREEN, do not move).
    # ------------------------------------------------------------------ #
    "mslug": {
        "name": "Metal Slug",
        "system": "Neo Geo",
        "bios": ["neogeo.zip"],
        "works_in": ["fbneo", "arcade", "neogeo", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Corre al 100% en MAME 2003 o Neo Geo nativo (neogeo.zip).",
    },
    "mslug2": {
        "name": "Metal Slug 2",
        "system": "Neo Geo",
        "bios": ["neogeo.zip"],
        "works_in": ["fbneo", "arcade", "neogeo", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Corre al 100% en MAME 2003 o Neo Geo nativo (neogeo.zip).",
    },
    "mslug3": {
        "name": "Metal Slug 3",
        "system": "Neo Geo",
        "bios": ["neogeo.zip"],
        "works_in": ["fbneo", "arcade", "neogeo", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Corre al 100% en MAME 2003 o Neo Geo nativo (neogeo.zip).",
    },
    "mslug4": {
        "name": "Metal Slug 4",
        "system": "Neo Geo",
        "bios": ["neogeo.zip"],
        "works_in": ["fbneo", "arcade", "neogeo", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Corre al 100% en MAME 2003 o Neo Geo nativo (neogeo.zip).",
    },
    "mslug5": {
        "name": "Metal Slug 5",
        "system": "Neo Geo",
        "bios": ["neogeo.zip"],
        "works_in": ["fbneo", "arcade", "neogeo", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Corre al 100% en MAME 2003 o Neo Geo nativo (neogeo.zip).",
    },
    "mvsc": {
        "name": "Marvel vs. Capcom: Clash of Super Heroes",
        "system": "CPS-2",
        "bios": [],
        "works_in": ["fbneo", "arcade", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Perfecto a 60 fps en MAME 2003 o FBNeo.",
    },
    "sf2": {
        "name": "Street Fighter II: The World Warrior",
        "system": "CPS-1",
        "bios": [],
        "works_in": ["fbneo", "arcade", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Perfecto a 60 fps en MAME 2003.",
    },
    "sf2ce": {
        "name": "Street Fighter II': Champion Edition",
        "system": "CPS-1",
        "bios": [],
        "works_in": ["fbneo", "arcade", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Perfecto a 60 fps en MAME 2003.",
    },
    "sf2hf": {
        "name": "Street Fighter II' Turbo: Hyper Fighting",
        "system": "CPS-1",
        "bios": [],
        "works_in": ["fbneo", "arcade", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Perfecto a 60 fps en MAME 2003.",
    },
    "sf2j": {
        "name": "Street Fighter II': Champion Edition (Japan)",
        "system": "CPS-1",
        "bios": [],
        "works_in": ["fbneo", "arcade", "mame2003"],
        "best_core": "mame2003",
        "recommended_folder": "mame2003",
        "bad_in": [],
        "note": "Perfecto a 60 fps en MAME 2003.",
    },
    "kof2000": {
        "name": "The King of Fighters 2000",
        "system": "Neo Geo",
        "bios": ["neogeo.zip"],
        "works_in": ["fbneo", "arcade", "neogeo"],
        "best_core": "fbneo",
        "recommended_folder": "fbneo",
        "bad_in": [],
        "note": "Neo Geo: requiere neogeo.zip. FBNeo recomendado.",
    },
}


def get_compat(rom_base: str) -> Optional[dict]:
    """Look up a compatibility entry by ROM base name (case-insensitive).

    Returns a deep defensive copy of the entry so callers can never mutate
    the shared database (top-level keys AND nested lists like ``bios``,
    ``works_in``, ``warning_in``, ``bad_in``), or ``None`` when the ROM is
    not in the database.
    """
    if not rom_base:
        return None
    entry = ARCADE_COMPATIBILITY.get(rom_base.strip().lower())
    if entry is None:
        return None
    return copy.deepcopy(entry)


# ---------------------------------------------------------------------------
# Dynamic arcade system target
#
# Community cards use DIFFERENT system folder names for FBNeo arcade games:
# most ArkOS images expose "arcade", but some firmwares call the system
# "fbneo".  The static database stores the canonical folder names; the
# ``detect_arcade_target`` function inspects the mounted card and the
# evaluator below resolves the effective target at query time.
# ---------------------------------------------------------------------------

# Canonical system folders that mean "FBNeo arcade" in the static database.
_ARCADE_SYSTEM_FOLDERS = frozenset({"arcade", "fbneo"})

# Arcade ROM extensions used by the folder heuristics (not a full scan).
_ARCADE_ROM_EXTS = (".zip", ".7z")

# Common locations of the EmulationStation systems config on ArkOS cards.
_ES_CFG_REL_CANDIDATES = (
    "es_systems.cfg",
    "emulationstation/es_systems.cfg",
    "emulationstation/.emulationstation/es_systems.cfg",
    ".emulationstation/es_systems.cfg",
    "configs/emulationstation/es_systems.cfg",
    "retroarch/es_systems.cfg",
)

_arcade_target: str = "arcade"  # module state: system folder of the mounted card


def arcade_target() -> str:
    """Return the currently detected system folder for FBNeo arcade games."""
    return _arcade_target


def set_arcade_target(target: str) -> str:
    """Set the effective arcade system folder for this card (e.g. "fbneo").

    ``detect_arcade_target`` calls this after inspecting the card.  Unknown
    or empty values fall back to the canonical "arcade".
    """
    global _arcade_target
    normalized = (target or "").strip().lower()
    _arcade_target = normalized if normalized else "arcade"
    return _arcade_target


def detect_arcade_target(roms_root: str) -> str:
    """Detect the real system folder the console uses for arcade/FBNeo ROMs.

    Priority:
      1. ``es_systems.cfg`` of the mounted card, when reachable (Emulation
         Station config is authoritative: it names the system folder and its
         platform).  The config usually lives in the console rootfs and is
         not always visible from macOS; when present it wins.
      2. Folder structure: a top-level ``arcade/`` (or ``fbneo/``) folder
         that actually contains ROM files.
      3. Default ``"arcade"`` (the canonical ArkOS name).

    Pure filesystem logic, never raises; a corrupt config simply falls back
    to the folder heuristics.
    """
    cfg_path = _find_es_systems_cfg(roms_root)
    if cfg_path is not None:
        parsed = _parse_es_systems_target(cfg_path)
        if parsed is not None:
            return parsed
    for name in ("arcade", "fbneo"):
        folder = os.path.join(roms_root, name)
        if os.path.isdir(folder) and _folder_has_roms(folder):
            return name
    return "arcade"


def _find_es_systems_cfg(root: str) -> Optional[str]:
    """Locate an ``es_systems.cfg`` under ``root`` (known paths, then a
    bounded shallow walk), or ``None``."""
    for rel in _ES_CFG_REL_CANDIDATES:
        candidate = os.path.join(root, rel)
        if os.path.isfile(candidate):
            return candidate
    budget: List[int] = [2000]
    found: List[str] = []
    _walk_for_cfg(root, 0, 3, budget, found)
    return found[0] if found else None


def _walk_for_cfg(
    directory: str,
    depth: int,
    max_depth: int,
    budget: List[int],
    found: List[str],
) -> None:
    """Recursive bounded scan for ``es_systems.cfg`` (stops at first hit)."""
    if depth > max_depth or found or budget[0] <= 0:
        return
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return
    for entry in entries:
        budget[0] -= 1
        if budget[0] <= 0 or found:
            return
        try:
            if entry.is_dir():
                _walk_for_cfg(entry.path, depth + 1, max_depth, budget, found)
                if found:
                    return
            elif entry.name.lower() == "es_systems.cfg":
                found.append(entry.path)
                return
        except OSError:
            continue


def _parse_es_systems_target(cfg_path: str) -> Optional[str]:
    """Extract the arcade system folder name from an ``es_systems.cfg``.

    Returns ``None`` on unreadable/corrupt configs so the caller can fall
    back to the folder heuristics.  Resolution order:
      * a system named exactly ``arcade`` (the ArkOS convention);
      * a system whose ``platform`` is ``arcade`` (any folder name);
      * a system whose ``path`` basename is ``arcade``;
      * a system named (or pathed) ``fbneo``.
    """
    try:
        with open(cfg_path, "r", encoding="utf-8", errors="replace") as fh:
            root = ET.fromstring(fh.read())
    except Exception:  # noqa: BLE001 - corrupt configs must never crash
        return None

    systems: List[tuple] = []
    for sys_el in root.iter("system"):
        name = (sys_el.findtext("name") or "").strip().lower()
        platform = (sys_el.findtext("platform") or "").strip().lower()
        path_base = os.path.basename(
            (sys_el.findtext("path") or "").strip().lower()
        )
        systems.append((name, platform, path_base))

    for name, _platform, _path_base in systems:
        if name == "arcade":
            return "arcade"
    for name, platform, _path_base in systems:
        if platform == "arcade" and name:
            return name
    for name, _platform, path_base in systems:
        if path_base == "arcade" and name:
            return name
    for name, _platform, path_base in systems:
        if name == "fbneo" or path_base == "fbneo":
            return "fbneo"
    return None


def _folder_has_roms(folder: str) -> bool:
    """True when ``folder`` directly contains at least one arcade ROM file."""
    try:
        for name in os.listdir(folder):
            if (
                os.path.isfile(os.path.join(folder, name))
                and name.lower().endswith(_ARCADE_ROM_EXTS)
            ):
                return True
    except OSError:
        return False
    return False


def _effective_target(info: dict) -> str:
    """Resolve the optimization target for an entry against the card target.

    Arcade/FBNeo entries (recommended folder ``arcade`` or ``fbneo``) follow
    the dynamically detected system folder; every other entry keeps its
    static recommendation (e.g. ``mame2003``).
    """
    recommended = info["recommended_folder"].lower()
    if recommended in _ARCADE_SYSTEM_FOLDERS:
        return _arcade_target
    return recommended


def _effective_works_in(info: dict) -> set:
    """works_in plus the dynamically detected arcade folder when relevant."""
    works = {f.lower() for f in info["works_in"]}
    if info["recommended_folder"].lower() in _ARCADE_SYSTEM_FOLDERS:
        works.add(_arcade_target)
    return works


def _normalize_folder(folder_name: str) -> str:
    """Normalize a system folder name for database lookups."""
    return (folder_name or "").strip().lower()


def compute_status(folder_name: str, rom_base: str) -> OptimizationStatus:
    """Compute the optimization status for a ROM inside a system folder.

    Logic:
      * ROM not in the database            -> UNKNOWN (treated as correct).
      * Folder in ``works_in``             -> CORRECT.
      * Folder in ``warning_in``           -> WARNING (runs but suboptimal;
        the optimizer button stays available).
      * Folder in ``bad_in``               -> NEEDS_OPTIMIZATION.
      * Any other folder                    -> NEEDS_OPTIMIZATION only when the
        recommended folder differs from the current folder, else CORRECT.

    Arcade/FBNeo entries are evaluated against the dynamically detected
    system folder (``arcade`` or ``fbneo``, see ``detect_arcade_target``).
    """
    info = get_compat(rom_base)
    if info is None:
        return OptimizationStatus.UNKNOWN

    folder = _normalize_folder(folder_name)
    works_in = _effective_works_in(info)
    warning_in = {f.lower() for f in info.get("warning_in", [])}
    bad_in = {f.lower() for f in info["bad_in"]}
    recommended = _effective_target(info)

    if folder in works_in:
        return OptimizationStatus.CORRECT
    if folder in warning_in:
        return OptimizationStatus.WARNING
    if folder in bad_in:
        return OptimizationStatus.NEEDS_OPTIMIZATION
    if recommended != folder:
        return OptimizationStatus.NEEDS_OPTIMIZATION
    return OptimizationStatus.CORRECT


def recommended_action(folder_name: str, rom_base: str) -> Optional[str]:
    """Return the folder the optimizer should move the ROM into.

    Only meaningful when ``compute_status`` is ``NEEDS_OPTIMIZATION`` or
    ``WARNING`` (the move is recommended in both cases); returns ``None``
    otherwise (or when the game is unknown).  Arcade/FBNeo entries resolve
    to the dynamically detected system folder.
    """
    status = compute_status(folder_name, rom_base)
    if status not in (
        OptimizationStatus.NEEDS_OPTIMIZATION,
        OptimizationStatus.WARNING,
    ):
        return None
    info = get_compat(rom_base)
    if info is None:
        return None
    return _effective_target(info)


def display_name(folder_name: str) -> str:
    """Return the human-friendly label for a system folder name."""
    name = _normalize_folder(folder_name)
    return ARCADE_FOLDER_ALIASES.get(name, name or "—")