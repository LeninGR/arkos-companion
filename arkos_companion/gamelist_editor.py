"""Read, edit and save EmulationStation ``gamelist.xml`` files (pure Python).

This module is the single source of truth for ``gamelist.xml`` handling in the
application.  It uses ONLY the standard library (``xml.etree.ElementTree``),
so it can be unit-tested without a display and reused by any other ArkOS
tooling.

Public API (the integration surface requested by the UI):
  * ``parse_gamelist``        -- read a system folder's gamelist.xml, or
                                 bootstrap a fresh ``<gameList>`` when the
                                 file is missing or corrupt.
  * ``update_game_metadata``  -- update ``<emulator>``/``<core>`` (and any
                                 other tag) of an existing ``<game>`` node, or
                                 create the node when the ROM is new.
  * ``remove_game_from_xml``  -- remove the ``<game>`` node whose ``<path>``
                                 matches a deleted ROM file.
  * ``save_gamelist``         -- pretty-printed, atomic write with a ``.bak``
                                 backup before the first overwrite per folder.

Safety rules followed by every function:
  * Never raise on a missing or corrupt file -- the caller's flow must not
    break because of a broken XML.
  * A corrupt file is quarantined (renamed to ``gamelist.xml.corrupt``) so the
    original bytes are never silently overwritten.
  * Writes go through a temp file + ``os.replace`` so the target path is never
    left half-written.
"""

from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
from typing import Optional, Set

GAMELIST_FILENAME = "gamelist.xml"
_CORRUPT_SUFFIX = ".corrupt"

# Folders (absolute paths) already backed up during this session.  A ``.bak``
# is created at most ONCE per folder per session, so repeated saves never
# litter the card with backups.
_BACKED_UP_FOLDERS: Set[str] = set()

_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'


# ---------------------------------------------------------------------------
# 1. READING (parse_gamelist)
# ---------------------------------------------------------------------------

def parse_gamelist(system_path: str) -> ET.Element:
    """Return the ``<gameList>`` root element of ``system_path``.

    Scenarios handled (the function NEVER raises):
      * File missing          -> a fresh ``<gameList>`` root is returned.
                                 Nothing is written to disk yet: the tree is
                                 persisted only when a ``save_*`` call runs.
      * File corrupt/malformed-> the offending file is quarantined (renamed,
                                 best effort, to ``gamelist.xml.corrupt``) and
                                 a fresh ``<gameList>`` root is returned, so
                                 the app keeps working and the broken file can
                                 be inspected later.
      * File valid            -> the parsed root is returned as-is.

    ``system_path`` is the SYSTEM FOLDER (e.g. ``roms/mame2003``); the file
    name itself is always ``gamelist.xml`` inside it.
    """
    xml_path = os.path.join(system_path, GAMELIST_FILENAME)
    try:
        tree = ET.parse(xml_path)
    except (OSError, ET.ParseError, ValueError):
        # Broken XML or unreadable file: never crash the caller.
        if os.path.exists(xml_path):
            _quarantine_corrupt(xml_path)
        return ET.Element("gameList")
    return tree.getroot()


def _quarantine_corrupt(xml_path: str) -> None:
    """Move a corrupt gamelist.xml out of the way (best effort).

    The first quarantine is ``gamelist.xml.corrupt``; if that name is already
    taken, ``gamelist.xml.corrupt.1``, ``.2``, ... are tried.  If the rename
    fails (e.g. read-only card), the file is simply left in place and the app
    keeps running with a fresh in-memory tree.
    """
    for index in range(1000):
        if index == 0:
            target = xml_path + _CORRUPT_SUFFIX
        else:
            target = "{}{}.{}".format(xml_path, _CORRUPT_SUFFIX, index)
        if not os.path.exists(target):
            try:
                os.replace(xml_path, target)
            except OSError:
                pass  # best effort only
            return


def read_gamelist(path: str) -> Optional[ET.Element]:
    """Legacy helper: parsed root, or ``None`` when missing/corrupt.

    Kept for callers that only need to know "is there a usable gamelist?".
    ``parse_gamelist`` is the recommended entry point for new code.
    """
    try:
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError, ValueError):
        return None


def find_game_node(root: ET.Element, rom_file_name: str) -> Optional[ET.Element]:
    """Return the ``<game>`` node whose ``<path>`` names ``rom_file_name``.

    Matching is case-insensitive and tolerant of the ``./`` prefix used by
    EmulationStation (``./dmnfrnt.zip`` matches ``DMNFRNT.ZIP``).  Returns
    ``None`` when the ROM is not registered in the tree.
    """
    needle = (rom_file_name or "").lower()
    if not needle:
        return None
    for game in root.findall("game"):
        path_el = game.find("path")
        if path_el is not None and _path_basename(path_el.text) == needle:
            return game
    return None


def _path_basename(path_text: Optional[str]) -> str:
    """Normalize a ``<path>`` value to its bare, lowercased file name."""
    if not path_text:
        return ""
    stripped = path_text.strip()
    if stripped.startswith("./"):
        stripped = stripped[2:]
    return os.path.basename(stripped).lower()


# ---------------------------------------------------------------------------
# 2. UPDATING (update_game_metadata)
# ---------------------------------------------------------------------------

def update_game_metadata(
    root: ET.Element,
    rom_file_name: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    image_rel: Optional[str] = None,
    video_rel: Optional[str] = None,
    emulator: Optional[str] = None,
    core: Optional[str] = None,
) -> bool:
    """Register ``emulator``/``core`` (and optional metadata) for a ROM.

    Behavior:
      * The ROM is already registered in the tree -> the matching ``<game>``
        node is UPDATED in place: every tag passed is set (creating the tag
        when the node does not have it yet); tags not passed are preserved.
      * The ROM is NOT registered (new ROM) -> a brand-new ``<game>`` node is
        created and appended, with ``<path>./<rom_file_name></path>`` plus
        every tag passed.

    Returns ``True`` when a new node was CREATED, ``False`` when an existing
    node was updated -- useful for the caller's status messages.

    Because the update path reuses the existing node, the EmulationStation
    metadata the user curated (name, desc, image, video) is never lost.
    """
    game = find_game_node(root, rom_file_name)
    created = game is None
    if created:
        game = ET.SubElement(root, "game")
        path_el = ET.SubElement(game, "path")
        path_el.text = "./" + rom_file_name

    if title is not None:
        _set_tag_text(game, "name", title)
    if description is not None:
        _set_tag_text(game, "desc", description)
    if image_rel is not None:
        _set_tag_text(game, "image", image_rel)
    if video_rel is not None:
        _set_tag_text(game, "video", video_rel)
    if emulator is not None:
        _set_tag_text(game, "emulator", emulator)
    if core is not None:
        _set_tag_text(game, "core", core)
    return created


def _set_tag_text(node: ET.Element, tag: str, value: str) -> None:
    """Set the text of ``tag`` on ``node``, creating the tag if missing."""
    element = node.find(tag)
    if element is None:
        element = ET.SubElement(node, tag)
    element.text = value


# Backwards-compatible alias: "add" is now insert-or-update (no duplicates).
add_game_entry = update_game_metadata


# ---------------------------------------------------------------------------
# 3. REMOVING (remove_game_from_xml)
# ---------------------------------------------------------------------------

def remove_game_from_xml(root: ET.Element, rom_file_name: str) -> bool:
    """Remove EVERY ``<game>`` node whose ``<path>`` matches the ROM file.

    Matching is case-insensitive and tolerant of the ``./`` prefix.  All
    matching nodes are removed (defensive: a hand-edited gamelist could
    contain duplicates).  Returns ``True`` when at least one node was removed
    -- the caller should then persist the tree with ``save_gamelist``.
    """
    needle = (rom_file_name or "").lower()
    if not needle:
        return False
    removed = False
    for game in list(root.findall("game")):
        path_el = game.find("path")
        if path_el is not None and _path_basename(path_el.text) == needle:
            root.remove(game)
            removed = True
    return removed


# Backwards-compatible alias.
remove_game_entry = remove_game_from_xml


# ---------------------------------------------------------------------------
# 4. SAVING / FORMATTING (save_gamelist)
# ---------------------------------------------------------------------------

def save_gamelist(path: str, root: ET.Element, backup: bool = True) -> None:
    """Serialize ``root`` to ``path`` pretty-printed and atomically.

    * Pretty-printing: ``ET.indent`` (available since Python 3.9) indents
      every level with four spaces, producing the clean, human-readable tree
      EmulationStation also expects:

          <?xml version="1.0" encoding="UTF-8"?>
          <gameList>
              <game>
                  <path>./dmnfrnt.zip</path>
                  <name>Demon Front</name>
                  ...
              </game>
          </gameList>

    * UTF-8 XML declaration on the first line.
    * Backup: when ``backup`` is True and the target already exists, a
      ``gamelist.xml.bak`` copy is created -- at most ONCE per folder per
      session (tracked in ``_BACKED_UP_FOLDERS``).
    * Atomicity: the content is written to ``path + ".tmp"`` and moved into
      place with ``os.replace``; a crash mid-write can never leave a truncated
      gamelist at the target path.
    """
    path = os.path.abspath(path)
    ET.indent(root, space="    ")
    body = ET.tostring(root, encoding="utf-8")
    data = _XML_DECLARATION + body

    folder = os.path.dirname(path)
    if backup and os.path.isfile(path) and folder not in _BACKED_UP_FOLDERS:
        _BACKED_UP_FOLDERS.add(folder)
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass  # backup is best-effort; never block the save because of it

    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up the temp file so we never leave garbage behind.
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


# Backwards-compatible alias: same bootstrap semantics as parse_gamelist.
ensure_gamelist = parse_gamelist
