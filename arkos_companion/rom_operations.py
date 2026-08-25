"""Destructive and move operations on the ROM card (pure Python, no Qt).

* ``delete_game`` removes a ROM plus its media and gamelist entry.
* ``optimize_game`` moves a ROM into its recommended system folder and updates
  both gamelists.
* ``edit_game_metadata`` persists edited title/description/emulator/core in the
  system's ``gamelist.xml``.
* ``set_game_cover`` installs a user-picked image as the game's cover and
  registers it in ``gamelist.xml``.
* ``check_bios`` verifies that required BIOS zips exist on the card.

All routines are defensive about missing files and only raise for the
situations the caller needs to surface (permissions, missing source).

Every successful operation records an entry in the journal (``history``);
the journal write is best-effort and can never break the operation itself.
"""

from __future__ import annotations

import os
import shutil
from typing import List, Optional

from arkos_companion import history
from arkos_companion.gamelist_editor import (
    GAMELIST_FILENAME,
    find_game_node,
    parse_gamelist,
    read_gamelist,
    remove_game_from_xml,
    save_gamelist,
    update_game_metadata,
)
from arkos_companion.models import (
    DeleteResult,
    GameEntry,
    OptimizeResult,
    SystemFolder,
)


def _rel_path(full_path: str, base_dir: str) -> str:
    """Return ``full_path`` relative to ``base_dir`` (patched for .. segments)."""
    rel = os.path.relpath(full_path, base_dir)
    return rel


def delete_game(system: SystemFolder, entry: GameEntry) -> DeleteResult:
    """Permanently delete a game: ROM file, its cover and video, and its gamelist entry.

    * Files already missing are reported in ``missing`` instead of raising.
    * A file that exists but cannot be removed raises ``PermissionError`` with
      the offending path (the caller shows it to the user).
    * The gamelist is saved with backup semantics whenever the ROM had an
      entry there (removing an entry the file does not have is harmless).
    """
    result = DeleteResult()

    files_to_remove: List[str] = [os.path.join(system.path, entry.rom_file)]
    if entry.image_path:
        files_to_remove.append(entry.image_path)
    if entry.video_path:
        files_to_remove.append(entry.video_path)

    for file_path in files_to_remove:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except PermissionError:
                raise PermissionError(
                    "No se pudo eliminar el archivo (permiso denegado): "
                    + file_path
                ) from None
            result.removed.append(_rel_path(file_path, system.path))
        else:
            result.missing.append(_rel_path(file_path, system.path))

    gamelist_path = os.path.join(system.path, GAMELIST_FILENAME)
    root = read_gamelist(gamelist_path)
    if root is not None:
        had_entry = find_game_node(root, entry.rom_file) is not None
        removed_any = remove_game_from_xml(root, entry.rom_file)
        if had_entry or removed_any:
            save_gamelist(gamelist_path, root)
            result.gamelist_updated = True

    # Journal the deletion once every file move + gamelist write succeeded.
    history.append_entry(
        history.ACTION_DELETE,
        system.name,
        entry.rom_file,
        entry.title,
        details={},
    )

    return result


def _move_file(
    source: str,
    dest_dir: str,
    subdir: str,
    result: OptimizeResult,
) -> Optional[str]:
    """Move one media/rom file into ``dest_dir/<subdir>`` keeping its name.

    If a same-named file already exists at the destination, the existing copy
    is kept (reported as skipped).  Returns the destination path when the
    destination file is in place, else ``None``.
    """
    if not source or not os.path.exists(source):
        return None
    os.makedirs(dest_dir, exist_ok=True)
    fname = os.path.basename(source)
    dest = os.path.join(dest_dir, fname)

    if os.path.exists(dest):
        result.skipped.append(os.path.join(subdir, fname) if subdir else fname)
        return dest

    os.replace(source, dest)
    entry = os.path.join(subdir, fname) if subdir else fname
    result.moved.append(entry)
    if not os.path.exists(dest):
        raise OSError("Fallo al verificar el archivo movido: " + dest)
    return dest


def optimize_game(
    roms_root: str,
    system: SystemFolder,
    entry: GameEntry,
    target_folder: str,
) -> OptimizeResult:
    """Move a game into its recommended folder and update both gamelists.

    * Creates the target folder (and its ``images/``/``videos/`` subfolders).
    * Moves ROM, cover and video, never overwriting an existing target media
      file (skipped instead).
    * Removes the entry from the source gamelist and adds the equivalent entry
      to the target gamelist with the best core.
    * Raises ``FileNotFoundError`` with the path if the source ROM is missing.
    """
    result = OptimizeResult(target_folder=target_folder)

    source_rom = os.path.join(system.path, entry.rom_file)
    if not os.path.exists(source_rom):
        raise FileNotFoundError("No se encontró el archivo ROM: " + source_rom)

    target_abs = os.path.join(roms_root, target_folder)
    target_images = os.path.join(target_abs, "images")
    target_videos = os.path.join(target_abs, "videos")
    os.makedirs(target_images, exist_ok=True)
    os.makedirs(target_videos, exist_ok=True)

    # Move the ROM file.
    _move_file(source_rom, target_abs, "", result)

    # Move cover + video (never clobber existing targets).
    image_dest = _move_file(entry.image_path, target_images, "images", result)
    video_dest = _move_file(entry.video_path, target_videos, "videos", result)

    # Drop the entry from the source gamelist (only if it exists).
    source_gamelist = os.path.join(system.path, GAMELIST_FILENAME)
    root = read_gamelist(source_gamelist)
    if root is not None and os.path.exists(source_gamelist):
        remove_game_from_xml(root, entry.rom_file)
        save_gamelist(source_gamelist, root)

    # Register it in the target gamelist.  update_game_metadata is
    # insert-or-update, so a stale duplicate node can never survive.
    target_gamelist = os.path.join(target_abs, GAMELIST_FILENAME)
    existed = os.path.exists(target_gamelist)
    target_root = parse_gamelist(target_abs)

    title = entry.title
    if not title or title == entry.rom_file:
        compat = entry.compat or {}
        title = compat.get("name") or entry.rom_file
    core = None
    if entry.compat and entry.compat.get("best_core"):
        core = entry.compat["best_core"]
    core = core or "fbneo"

    image_rel = None
    if image_dest:
        image_rel = "./images/" + os.path.basename(image_dest)
    video_rel = None
    if video_dest:
        video_rel = "./videos/" + os.path.basename(video_dest)

    update_game_metadata(
        target_root,
        entry.rom_file,
        title=title,
        description=entry.description,
        image_rel=image_rel,
        video_rel=video_rel,
        emulator="retroarch",
        core=core,
    )
    save_gamelist(target_gamelist, target_root, backup=existed)

    # Journal the move once both gamelists are persisted.  ``core`` holds the
    # value actually written to the target gamelist (best core or fbneo).
    history.append_entry(
        history.ACTION_OPTIMIZE,
        system.name,
        entry.rom_file,
        title,
        details={
            "moved_from": system.name,
            "moved_to": target_folder,
            "emulator": {"old": entry.emulator, "new": "retroarch"},
            "core": {"old": entry.core, "new": core},
        },
    )

    result.bios_checked = bool(entry.compat and entry.compat.get("bios"))
    return result


def edit_game_metadata(
    system: SystemFolder,
    entry: GameEntry,
    *,
    title: str = "",
    emulator: str = "",
    core: str = "",
    description: str = "",
) -> dict:
    """Persist edited title/description/emulator/core for a game.

    Reads the system's ``gamelist.xml``, updates (or creates) the matching
    ``<game>`` node through ``update_game_metadata`` and persists it with the
    module's backup semantics.  Empty strings clear the field.  A no-op edit
    (every field unchanged) is detected before any write and leaves the file
    untouched.

    On success an ``ACTION_EDIT`` journal entry is appended (best effort)
    describing exactly which fields changed and their old/new values.

    Returns a dict: ``{"system", "rom_file", "title", "created", "changed"}``.
    """
    changed = _changed_fields(entry, title, description, emulator, core)
    if not changed:
        return {
            "system": system.name,
            "rom_file": entry.rom_file,
            "title": title or entry.title,
            "created": False,
            "changed": {},
        }

    root = parse_gamelist(system.path)
    created = update_game_metadata(
        root,
        entry.rom_file,
        title=title,
        description=description,
        emulator=emulator,
        core=core,
    )
    save_gamelist(os.path.join(system.path, GAMELIST_FILENAME), root)

    # Journal the edit only after the gamelist was persisted successfully.
    history.append_entry(
        history.ACTION_EDIT,
        system.name,
        entry.rom_file,
        title or entry.title,
        details=changed,
    )
    return {
        "system": system.name,
        "rom_file": entry.rom_file,
        "title": title or entry.title,
        "created": created,
        "changed": changed,
    }


def set_game_cover(
    system: SystemFolder,
    entry: GameEntry,
    source_image: str,
) -> dict:
    """Install a user-picked image as the game's cover on the card.

    * Copies ``source_image`` into ``<system>/images/<rom_base><ext>`` using
      the source file's extension (or ``.png`` when it has none).
    * Replaces the previous cover file (if any and different) so the card
      never accumulates orphaned media.
    * Updates the system's ``gamelist.xml`` ``<image>`` tag for the entry.
    * Journals an ``ACTION_MEDIA_MANUAL`` entry (best effort).

    Raises ``FileNotFoundError`` when the source image is missing and
    ``OSError`` subclasses (permissions, copy failures) for the caller to
    surface.  Returns a result dict with ``image_path``/``image_rel``.
    """
    if not os.path.isfile(source_image):
        raise FileNotFoundError(
            "No se encontró la imagen seleccionada: " + source_image
        )

    images_dir = os.path.join(system.path, "images")
    os.makedirs(images_dir, exist_ok=True)

    extension = os.path.splitext(source_image)[1].lower() or ".png"
    dest = os.path.join(images_dir, entry.rom_base + extension)

    # Same-file pick is a no-op copy but must still persist the gamelist tag.
    if os.path.abspath(dest) != os.path.abspath(source_image):
        try:
            shutil.copy2(source_image, dest)
        except PermissionError:
            raise PermissionError(
                "No se pudo copiar la imagen (permiso denegado): " + source_image
            ) from None
        if not os.path.exists(dest):
            raise OSError("Fallo al verificar la imagen copiada: " + dest)

    # Drop the previous cover when it is a different file (avoid orphans).
    removed: List[str] = []
    if entry.image_path and os.path.abspath(entry.image_path) != os.path.abspath(dest):
        try:
            os.remove(entry.image_path)
            removed.append(_rel_path(entry.image_path, system.path))
        except OSError:
            pass  # best effort: a stale cover must not fail the operation

    image_rel = "./images/" + os.path.basename(dest)
    root = parse_gamelist(system.path)
    created = update_game_metadata(root, entry.rom_file, image_rel=image_rel)
    save_gamelist(os.path.join(system.path, GAMELIST_FILENAME), root)

    history.append_entry(
        history.ACTION_MEDIA_MANUAL,
        system.name,
        entry.rom_file,
        entry.title,
        details={
            "source": os.path.basename(source_image),
            "image": image_rel,
            "removed": removed,
        },
    )
    return {
        "system": system.name,
        "rom_file": entry.rom_file,
        "title": entry.title,
        "image_path": dest,
        "image_rel": image_rel,
        "created": created,
        "removed": removed,
    }


def _changed_fields(
    entry: GameEntry,
    title: str,
    description: str,
    emulator: str,
    core: str,
) -> dict:
    """Return {field: {"old": ..., "new": ...}} for every field that changed.

    ``None`` (model default) and ``""`` (dialog empty value) are treated as
    the same "no value" state so untouched fields are never journaled.
    """
    changed: dict = {}
    for field, old, new in (
        ("title", entry.title, title),
        ("description", entry.description, description),
        ("emulator", entry.emulator, emulator),
        ("core", entry.core, core),
    ):
        if (old or "") != (new or ""):
            changed[field] = {"old": old or "", "new": new or ""}
    return changed


def check_bios(roms_root: str, target_folder: str, bios_list: List[str]) -> List[str]:
    """Return the names of required BIOS files that are missing from the card.

    A BIOS is considered present when a file with its name exists
    (case-insensitively) under ``<roms_root>/bios/`` OR under
    ``<roms_root>/<target_folder>/``.
    """
    missing: List[str] = []
    for bios_name in bios_list or []:
        if not _file_exists_anywhere(roms_root, target_folder, bios_name):
            missing.append(bios_name)
    return missing


def _file_exists_anywhere(roms_root: str, target_folder: str, name: str) -> bool:
    """Case-insensitive existence check in the bios/ and target folders."""
    for directory in (
        os.path.join(roms_root, "bios"),
        os.path.join(roms_root, target_folder),
    ):
        if _file_exists_ci(directory, name):
            return True
    return False


def _file_exists_ci(directory: str, name: str) -> bool:
    """True when ``directory`` contains a file whose name matches case-insensitively."""
    direct = os.path.join(directory, name)
    if os.path.isfile(direct):
        return True
    try:
        entries = os.listdir(directory)
    except OSError:
        return False
    needle = name.lower()
    return any(item.lower() == needle for item in entries)