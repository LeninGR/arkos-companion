"""Filesystem scanning and gamelist.xml matching (pure Python, no Qt).

Scans the EASYROMS root of an R36S card, detects per-system folders and
builds ``GameEntry`` objects merging the filesystem state with the
EmulationStation ``gamelist.xml`` metadata.
"""

from __future__ import annotations

import os
import sys
import unicodedata
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from arkos_companion.compat_db import (
    compute_status,
    detect_arcade_target,
    display_name,
    get_compat,
    set_arcade_target,
)
from arkos_companion.gamelist_editor import parse_gamelist
from arkos_companion.models import GameEntry, OptimizationStatus, SystemFolder

# All supported ROM file extensions (lowercase).
ROM_EXTENSIONS: frozenset = frozenset({
    ".zip", ".7z", ".nes", ".fds", ".sfc", ".smc", ".gb", ".gbc", ".gba",
    ".nds", ".n64", ".z64", ".v64", ".bin", ".cue", ".iso", ".chd", ".pce",
    ".ngp", ".ngc", ".col", ".lnx", ".a26", ".a52", ".a78", ".ws", ".wsc",
    ".gg", ".md", ".gen", ".sms", ".32x", ".vb", ".min", ".int", ".j64",
    ".pxo",
    # PSX/PSP formats: .pbp is the eBoot format (PSX single-file eboot and
    # PSP eboot); the rest are common PSX/PSP disc images and descriptors.
    ".pbp", ".img", ".mdf", ".mds", ".ccd", ".ecm", ".toc", ".cso",
})

# Known system folders; unknown systems are appended alphabetically after.
SYSTEM_SCAN_ORDER: List[str] = [
    "arcade", "mame2003", "fbneo", "neogeo", "pgm", "gba", "gb", "gbc",
    "nes", "snes", "n64", "psx", "psp", "nds", "gen", "sms", "gg", "pce",
    "wonderswan", "atari2600", "gamegear",
]

# Folders that are never treated as game systems.
_SKIPPED_FOLDER_NAMES = {"bios", "tools", "downloads", "media"}

# Known BIOS packages that community cards copy inside the system folders
# (support files, never games).  Extend as the community reports more BIOS
# files living next to the ROMs.
BIOS_FILE_NAMES = frozenset({
    "neogeo.zip",   # SNK Neo Geo BIOS
    "pgm.zip",      # IGS PGM BIOS (Demon Front, The Gladiator, ...)
    "uni-bios.zip", # alternate Neo Geo BIOS package
    "decs.zip",     # Data East CPU cassette system BIOS
})

# Media file extensions accepted for cover/video search.
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})
_VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mkv", ".mov", ".wmv", ".m4v", ".webm", ".mpg", ".mpeg"})


def resolve_roms_root(selected_path: str) -> str:
    """Return the folder that contains the per-system subfolders.

    If ``selected_path/roms`` exists and is a directory it is returned;
    otherwise the selected path itself is returned (the user may have picked
    the ``roms`` folder directly).  Always returns a normalized absolute path.
    """
    if selected_path:
        direct = os.path.join(selected_path, "roms")
        if os.path.isdir(direct):
            selected_path = direct
    return os.path.normpath(os.path.abspath(selected_path))


def _is_ignored_file(name: str) -> bool:
    """True for files that are never games, however their extension:

    * ``._*`` -- AppleDouble metadata macOS creates next to every file
      copied to an SD card (``._pgm.zip``, ``._dmnfrnt.zip``);
    * recognized BIOS packages copied inside system folders (``pgm.zip``).
    """
    if name.startswith("._"):
        return True
    return name.lower() in BIOS_FILE_NAMES


def _is_rom_file(name: str) -> bool:
    """True for a real game file (supported extension, case-insensitive).

    ``False`` for BIOS packages and AppleDouble garbage.
    """
    if _is_ignored_file(name):
        return False
    return os.path.splitext(name)[1].lower() in ROM_EXTENSIONS


def _sort_key(system_name: str) -> Tuple[int, str]:
    """Sort key: known systems first (in SYSTEM_SCAN_ORDER), then alphabetically."""
    try:
        idx = SYSTEM_SCAN_ORDER.index(system_name)
    except ValueError:
        idx = len(SYSTEM_SCAN_ORDER)
    return idx, system_name.lower()


# ---------------------------------------------------------------------------
# EASYROMS volume auto-detection
# ---------------------------------------------------------------------------

def _volume_candidates() -> List[str]:
    """Return the mounted-volume root paths for this platform (defensive).

    * macOS: every entry under ``/Volumes``.
    * Windows: every existing drive letter root (``C:\\``, ``D:\\``, ...).
    * Linux/BSD: entries under ``/media/<user>``, ``/run/media/<user>``,
      ``/media`` and ``/mnt``.
    Missing/unreadable base directories are simply skipped.
    """
    if sys.platform == "darwin":
        return _list_children("/Volumes")
    if sys.platform.startswith("win"):
        return [
            "{}:\\".format(chr(code))
            for code in range(ord("A"), ord("Z") + 1)
            if os.path.exists("{}:\\".format(chr(code)))
        ]
    user = os.path.basename(os.path.expanduser("~")) or "user"
    candidates: List[str] = []
    for base in ("/media/" + user, "/run/media/" + user, "/media", "/mnt"):
        candidates.extend(_list_children(base))
    return candidates


def _list_children(directory: str) -> List[str]:
    """List absolute paths of direct children of ``directory`` (defensive)."""
    try:
        return [
            os.path.join(directory, name)
            for name in os.listdir(directory)
        ]
    except OSError:
        return []


def _detect_from_candidates(candidates: List[str]) -> Optional[str]:
    """Pick the EASYROMS volume from a list of candidate root paths.

    Priority 1: any candidate whose folder name is ``EASYROMS``
    (case-insensitive) -- the default ArkOS partition label.
    Priority 2: any candidate that directly contains a ``roms/`` folder
    (structural fallback for renamed/re-labelled volumes).
    """
    for candidate in candidates:
        if (
            os.path.isdir(candidate)
            and os.path.basename(candidate).lower() == "easyroms"
        ):
            return candidate
    for candidate in candidates:
        if os.path.isdir(os.path.join(candidate, "roms")):
            return candidate
    return None


def detect_easyroms_volume() -> Optional[str]:
    """Return the absolute path of a mounted EASYROMS volume, or ``None``.

    Used at application startup so the user does not have to pick the card
    manually when it is already connected.  Pure filesystem logic, no Qt.
    """
    return _detect_from_candidates(_volume_candidates())


def scan_systems(roms_root: str) -> List[SystemFolder]:
    """Scan ``roms_root`` for game system folders (direct children only).

    Also configures the dynamic arcade target (``es_systems.cfg`` or folder
    layout) so the optimizer targets the system folder the console really
    exposes (``arcade`` or ``fbneo``) on whichever community image is
    mounted.

    A folder counts as a system when it contains at least one file whose
    extension is in ``ROM_EXTENSIONS``.  Hidden folders and the well-known
    non-game folders (``bios``, ``tools``, ``downloads``, ``media``) are
    skipped.  Defensive: unreadable folders are ignored, never raised.
    """
    set_arcade_target(detect_arcade_target(roms_root))

    systems: List[SystemFolder] = []
    try:
        entries = sorted(os.listdir(roms_root))
    except OSError:
        return systems

    for name in entries:
        if name.startswith("."):
            continue
        if name.lower() in _SKIPPED_FOLDER_NAMES:
            continue
        folder_path = os.path.join(roms_root, name)
        if not os.path.isdir(folder_path):
            continue
        count = 0
        try:
            for child in os.listdir(folder_path):
                child_path = os.path.join(folder_path, child)
                if os.path.isfile(child_path) and _is_rom_file(child):
                    count += 1
        except OSError:
            continue  # permission error or file vanished mid-scan
        if count > 0:
            systems.append(SystemFolder(
                name=name,
                display_name=display_name(name),
                path=os.path.normpath(os.path.abspath(folder_path)),
                rom_count=count,
            ))

    systems.sort(key=lambda s: _sort_key(s.name))
    return systems


# ---------------------------------------------------------------------------
# gamelist.xml handling helpers
# ---------------------------------------------------------------------------

def _strip_diacritics(text: str) -> str:
    """Remove combining diacritics from ``text`` (e.g. "á" -> "a")."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _normalize_key(text: str) -> str:
    """Normalize a string for fuzzy comparison: lowercase, no diacritics, alnum only."""
    clean = _strip_diacritics(text or "")
    return "".join(ch for ch in clean if ch.isalnum()).lower()


def _rom_key_from_xml_path(path_text: str) -> str:
    """Extract the ROM file name from a gamelist.xml <path> value.

    Handles the common ``./`` prefix: returns e.g. ``dmnfrnt.zip``.
    """
    if not path_text:
        return ""
    stripped = path_text.strip()
    if stripped.startswith("./"):
        stripped = stripped[2:]
    return os.path.basename(stripped)


def _resolve_media_from_xml(system_path: str, media_value: Optional[str]) -> Optional[str]:
    """Resolve a relative ``./images/x.png`` XML media path against the system folder."""
    if not media_value:
        return None
    cleaned = media_value.strip().lstrip("./")
    if not cleaned:
        return None
    abs_path = os.path.normpath(os.path.abspath(os.path.join(system_path, cleaned)))
    return abs_path if os.path.exists(abs_path) else None


def _load_gamelist_index(system_path: str) -> Dict[str, dict]:
    """Build {rom_file_lower: metadata} from the system's gamelist.xml.

    Parses through ``parse_gamelist``, the single parser owned by
    ``gamelist_editor``: a missing file yields an empty index, a corrupt file
    is quarantined (``gamelist.xml.corrupt``) and also yields an empty index.
    Never raises.
    """
    index: Dict[str, dict] = {}
    root = parse_gamelist(system_path)

    for game in root.iter("game"):
        path_el = game.find("path")
        if path_el is None or not path_el.text:
            continue
        rom_file = _rom_key_from_xml_path(path_el.text)
        if not rom_file:
            continue
        index[rom_file.lower()] = {
            "name": _element_text(game.find("name")),
            "desc": _element_text(game.find("desc")),
            "image": _resolve_media_from_xml(system_path, _element_text(game.find("image"))),
            "video": _resolve_media_from_xml(system_path, _element_text(game.find("video"))),
            "emulator": _element_text(game.find("emulator")),
            "core": _element_text(game.find("core")),
        }
    return index


def _element_text(el: Optional[ET.Element]) -> Optional[str]:
    """Return element text stripped of whitespace, or None."""
    if el is None or not el.text:
        return None
    text = el.text.strip()
    return text or None


def _find_media_by_stem(system_path: str, subdir: str, candidates: set) -> Optional[str]:
    """Search ``system_path/<subdir>`` for a file matching any normalized candidate.

    Returns the first match (sorted for determinism) or None.
    """
    media_dir = os.path.join(system_path, subdir)
    try:
        names = sorted(os.listdir(media_dir))
    except OSError:
        return None
    for name in names:
        stem, ext = os.path.splitext(name)
        if ext.lower() not in (_IMAGE_EXTENSIONS if subdir == "images" else _VIDEO_EXTENSIONS):
            continue
        if _normalize_key(stem) in candidates:
            return os.path.normpath(os.path.abspath(os.path.join(media_dir, name)))
    return None


def load_system_games(system: SystemFolder) -> List[GameEntry]:
    """Build a GameEntry for every ROM file in ``system.path``.

    Metadata is merged from gamelist.xml when available: title, description,
    emulator/core, and media paths.  Media falls back to a stem-based search
    in the ``images/`` and ``videos/`` subfolders.  Each entry gets its
    compatibility status computed from the internal database.  Defensive:
    a single unreadable file never aborts the whole scan.
    """
    index = _load_gamelist_index(system.path)
    games: List[GameEntry] = []

    try:
        rom_names = sorted(os.listdir(system.path))
    except OSError:
        return games

    for rom_name in rom_names:
        rom_path = os.path.join(system.path, rom_name)
        if not os.path.isfile(rom_path) or not _is_rom_file(rom_name):
            continue
        rom_base = os.path.splitext(rom_name)[0]
        xml_meta = index.get(rom_name.lower())

        title = rom_name
        description = None
        emulator = None
        core = None
        image_path = None
        video_path = None
        if xml_meta:
            title = xml_meta["name"] or rom_name
            description = xml_meta["desc"]
            emulator = xml_meta["emulator"]
            core = xml_meta["core"]
            image_path = xml_meta["image"]
            video_path = xml_meta["video"]

        # Fallback media discovery by stem, only when XML gave nothing usable.
        if not image_path or not os.path.exists(image_path):
            candidates = {_normalize_key(rom_base), _normalize_key(title)}
            image_path = _find_media_by_stem(system.path, "images", candidates) or image_path
        if not video_path or not os.path.exists(video_path):
            candidates = {_normalize_key(rom_base), _normalize_key(title)}
            video_path = _find_media_by_stem(system.path, "videos", candidates) or video_path

        compat = get_compat(rom_base)
        status = compute_status(system.name, rom_base)

        games.append(GameEntry(
            sys_folder=system.name,
            rom_file=rom_name,
            rom_base=rom_base,
            title=title,
            description=description,
            emulator=emulator,
            core=core,
            image_path=image_path,
            video_path=video_path,
            in_gamelist=xml_meta is not None,
            status=status,
            compat=compat,
        ))

    return games