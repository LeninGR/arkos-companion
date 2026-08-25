"""Background workers so the UI never freezes on slow disk/card operations.

Each worker is a ``QRunnable`` holding a ``WorkerSignals`` QObject; the heavy
work runs on ``QThreadPool.globalInstance()`` and all results are delivered
back to the GUI thread through signals.  Widgets are never touched from the
worker threads.
"""

from __future__ import annotations

import os
from typing import List

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from arkos_companion import gamelist_editor, history, rom_operations, scanner, scraper
from arkos_companion.models import GameEntry, SystemFolder


class WorkerSignals(QObject):
    """Signal holder shared by all runnable workers."""

    finished = pyqtSignal(object)     # any result object
    error = pyqtSignal(str)           # human-readable error message
    progress = pyqtSignal(str)        # optional progress text
    progress_value = pyqtSignal(int, int)  # (current, total) for progress bars
    progress_message = pyqtSignal(str)     # per-item progress text


class Runnable(QRunnable):
    """Base runnable: runs ``_work`` in the pool and routes results to signals."""

    def __init__(self) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.signals = WorkerSignals()

    def run(self) -> None:  # noqa: D102 - executed in a worker thread
        try:
            result = self._work()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            message = str(exc)
            if isinstance(exc, (PermissionError, FileNotFoundError)):
                message = "{}: {}".format(type(exc).__name__, exc)
            self.signals.error.emit(message)
        else:
            self.signals.finished.emit(result)

    def _work(self):  # pragma: no cover - overridden by subclasses
        raise NotImplementedError


class ScanSystemsWorker(Runnable):
    """Scan a selected EASYROMS path for game systems.

    Emits ``finished`` with a ``(roms_root, systems)`` tuple.
    """

    def __init__(self, selected_path: str) -> None:
        super().__init__()
        self.selected_path = selected_path

    def _work(self):
        roms_root = scanner.resolve_roms_root(self.selected_path)
        systems = scanner.scan_systems(roms_root)
        return roms_root, systems


class LoadSystemWorker(Runnable):
    """Load all games (with metadata) of a system folder.

    Emits ``finished`` with a list of ``GameEntry``.
    """

    def __init__(self, system: SystemFolder) -> None:
        super().__init__()
        self.system = system

    def _work(self) -> list:
        return scanner.load_system_games(self.system)


class DeleteWorker(Runnable):
    """Permanently delete a game (ROM + media + gamelist entry)."""

    def __init__(self, system: SystemFolder, entry: GameEntry) -> None:
        super().__init__()
        self.system = system
        self.entry = entry

    def _work(self):
        return rom_operations.delete_game(self.system, self.entry)


class EditMetadataWorker(Runnable):
    """Update title/description/emulator/core of a game in its gamelist.xml."""

    def __init__(
        self,
        system: SystemFolder,
        entry: GameEntry,
        *,
        title: str = "",
        emulator: str = "",
        core: str = "",
        description: str = "",
    ) -> None:
        super().__init__()
        self.system = system
        self.entry = entry
        self.title = title
        self.emulator = emulator
        self.core = core
        self.description = description

    def _work(self):
        return rom_operations.edit_game_metadata(
            self.system,
            self.entry,
            title=self.title,
            emulator=self.emulator,
            core=self.core,
            description=self.description,
        )


class OptimizeWorker(Runnable):
    """Move a game into its recommended folder, updating both gamelists."""

    def __init__(self, roms_root: str, system: SystemFolder,
                 entry: GameEntry, target_folder: str) -> None:
        super().__init__()
        self.roms_root = roms_root
        self.system = system
        self.entry = entry
        self.target_folder = target_folder

    def _work(self):
        return rom_operations.optimize_game(
            self.roms_root, self.system, self.entry, self.target_folder
        )


class CoverWorker(Runnable):
    """Install a user-picked image as the game's cover on the card."""

    def __init__(self, system: SystemFolder, entry: GameEntry,
                 source_image: str) -> None:
        super().__init__()
        self.system = system
        self.entry = entry
        self.source_image = source_image

    def _work(self):
        return rom_operations.set_game_cover(
            self.system, self.entry, self.source_image
        )


# ---------------------------------------------------------------------------
# TheGamesDB scraping workers
# ---------------------------------------------------------------------------

def _download_cover(system: SystemFolder, entry: GameEntry, image_url: str) -> str:
    """Download the scraped cover into ``<system>/images/`` (returns dest path).

    The extension is kept when the URL suggests a common image type
    (e.g. ``.jpg``), otherwise the cover is saved as ``.png``.
    """
    images_dir = os.path.join(system.path, "images")
    os.makedirs(images_dir, exist_ok=True)
    extension = scraper.url_extension(image_url) or ".png"
    dest = os.path.join(images_dir, entry.rom_base + extension)
    return scraper.download_media(image_url, dest)


def _download_video(system: SystemFolder, entry: GameEntry, video_url: str):
    """Download the scraped sample video into ``<system>/videos/``.

    Returns a ``(dest_path, warning)`` tuple: ``dest_path`` is the absolute
    path when the MP4 was written (never overwritten for an existing stem),
    else ``None``; ``warning`` holds a user-facing message when the video
    could not be saved (download failure, rate-limited 429), else ``None``.
    A video problem is always NON-fatal: it never raises.
    """
    existing = _existing_video_for(system, entry)
    if existing:
        return None, None
    extension = scraper.video_extension(video_url)
    if not extension:
        return None, None
    dest = os.path.join(system.path, "videos", entry.rom_base + extension)
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        scraper.download_media(video_url, dest)
    except Exception as exc:  # noqa: BLE001 - never fail the scrape because of a video
        return None, "No se pudo descargar el vídeo de {}: {} (se omite)".format(
            entry.rom_base, exc
        )
    return dest, None


def _existing_video_for(system: SystemFolder, entry: GameEntry) -> Optional[str]:
    """Absolute path of an existing ``videos/<stem>.mp4`` for the entry, or None.

    Stem matching mirrors the scanner (case/accent-insensitive) so a video
    already on the card for this game is never re-downloaded nor clobbered.
    """
    videos_dir = os.path.join(system.path, "videos")
    try:
        names = sorted(os.listdir(videos_dir))
    except OSError:
        return None
    needle = scanner._normalize_key(entry.rom_base)
    for name in names:
        stem, ext = os.path.splitext(name)
        if ext.lower() != ".mp4":
            continue
        if scanner._normalize_key(stem) == needle:
            return os.path.join(videos_dir, name)
    return None


def _persist_scrape_in_gamelist(
    system: SystemFolder,
    entry: GameEntry,
    result: dict,
    image_dest: Optional[str],
    video_dest: Optional[str] = None,
) -> None:
    """Write the scraped metadata (name/desc/image/video) into gamelist.xml.

    Best effort: a gamelist failure must never fail the whole scrape -- the
    media files are already on disk and the scanner discovers them by stem.
    Only non-empty values are written so curated metadata is never clobbered
    by an empty scrape result.
    """
    if image_dest is None and video_dest is None and not result.get("title") and not result.get("description"):
        return
    try:
        root = gamelist_editor.parse_gamelist(system.path)
        gamelist_editor.update_game_metadata(
            root,
            entry.rom_file,
            title=result.get("title") or None,
            description=result.get("description") or None,
            image_rel=(
                "./images/" + os.path.basename(image_dest)
                if image_dest
                else None
            ),
            video_rel=(
                "./videos/" + os.path.basename(video_dest)
                if video_dest
                else None
            ),
        )
        gamelist_editor.save_gamelist(
            os.path.join(system.path, gamelist_editor.GAMELIST_FILENAME), root
        )
    except Exception:  # noqa: BLE001 - best effort only
        pass


def _scrape_and_persist(
    system: SystemFolder, entry: GameEntry, include_video: bool = False
) -> dict:
    """Scrape one entry: download cover/video, register them, journal success.

    Returns a per-entry result dict (``status`` in ``ok``/``not_found``) or
    raises ``ScrapeError``/``OSError`` for hard failures (network, disk).
    ``include_video`` adds the sample-video download: it is always best
    effort (a video failure is reported as ``video_warning``, never raised).
    When the entry already has a cover (``video_only`` run) the cover is not
    re-downloaded and the curated title/description are never overwritten --
    only the missing video is fetched and registered.
    Called by both ``ScrapeWorker`` and ``MassScrapeWorker`` to avoid
    duplicating the persist logic.
    """
    api_key = scraper.effective_api_key()
    title_hint = entry.title if entry.title != entry.rom_file else None

    # Offline identification (FBNeo index) before hitting the API.
    compat = scraper.get_compat(entry.rom_base)
    indexed = scraper.rom_index.identify(entry.rom_base)
    if compat and compat.get("name"):
        identified_name, source = compat["name"], "compat"
    elif indexed and indexed.get("title"):
        identified_name, source = indexed["title"], (
            "hack" if indexed.get("hack") else "rom_index"
        )
    else:
        identified_name, source = None, None

    result = scraper.scrape_game(api_key, entry.rom_base, title_hint=title_hint)
    if result is None:
        query = scraper.resolve_clean_query(entry.rom_base, title_hint=title_hint)
        return {
            "status": "not_found",
            "rom_base": entry.rom_base,
            "query": query,
            "identified": identified_name,
            "source": source,
        }

    image_dest = None
    video_only = entry.image_path is not None and include_video
    if result.image_url and not video_only:
        image_dest = _download_cover(system, entry, result.image_url)

    video_dest = None
    video_warning = None
    if include_video and result.game_id is not None:
        video_url = scraper.fetch_game_video_url(result.game_id)
        if video_url:
            video_dest, video_warning = _download_video(system, entry, video_url)

    _persist_scrape_in_gamelist(system, entry, {
        "title": None if video_only else result.title,
        "description": None if video_only else result.description,
    }, image_dest, video_dest)

    history.append_entry(
        history.ACTION_SCRAPE,
        system.name,
        entry.rom_file,
        entry.title,
        details={
            "title": result.title if not video_only else entry.title,
            "year": result.year if not video_only else None,
            "developer": result.developer if not video_only else None,
            "description_len": len(result.description or "") if not video_only else 0,
            "video_only": video_only,
        },
    )
    return {
        "status": "ok",
        "rom_base": entry.rom_base,
        "title": result.title,
        "description": result.description,
        "year": result.year,
        "developer": result.developer,
        "image_path": image_dest,
        "video_path": video_dest,
        "video_warning": video_warning,
    }


class ScrapeWorker(Runnable):
    """Scrape a single game from TheGamesDB (cover download + metadata)."""

    def __init__(
        self,
        system: SystemFolder,
        entry: GameEntry,
        *,
        include_video: bool = False,
    ) -> None:
        super().__init__()
        self.system = system
        self.entry = entry
        self.include_video = include_video

    def _work(self) -> dict:
        return _scrape_and_persist(
            self.system, self.entry, include_video=self.include_video
        )


class MassScrapeWorker(Runnable):
    """Scrape a list of entries sequentially, tolerating per-entry failures.

    Emits ``progress_value``/``progress_message`` as it advances; the caller
    can request cancellation with ``set_cancelled`` (checked between
    entries).  Returns a ``{"ok", "not_found", "errors", "errors_list"}``
    summary; every successful entry is journaled individually.
    """

    def __init__(
        self,
        system: SystemFolder,
        entries: List[GameEntry],
        *,
        include_video: bool = False,
    ) -> None:
        super().__init__()
        self.system = system
        self.entries = entries
        self.include_video = include_video
        self._cancel = False

    def set_cancelled(self) -> None:
        self._cancel = True

    def _work(self) -> dict:
        ok = 0
        not_found = 0
        errors = 0
        errors_list: List[str] = []
        total = len(self.entries)
        for index, entry in enumerate(self.entries, start=1):
            if self._cancel:
                break
            self.signals.progress_value.emit(index, total)
            title_hint = entry.title if entry.title != entry.rom_file else None

            # Offline identification keeps a per-rom message for the log.
            identified = scraper.rom_index.identify(entry.rom_base)
            compat = scraper.get_compat(entry.rom_base)
            internal_name = None
            is_hack = False
            if compat and compat.get("name"):
                internal_name = compat["name"]
            elif identified and identified.get("title"):
                internal_name = identified["title"]
                is_hack = bool(identified.get("hack"))

            if internal_name:
                hint = "" if not is_hack else " (hack → juego base)"
                self.signals.progress_message.emit(
                    f'✅ [Identificado internamente como "{internal_name}"{hint}] → '
                    "Buscando multimedia…"
                )
            else:
                self.signals.progress_message.emit(
                    f"Juego {index} de {total}: {entry.rom_file}…"
                )

            # Universal tag cleaner: report the original name and the cleaned
            # query used for the API request (e.g. "(Europe) (En,Fr,De,Es,It)").
            original_query = scraper.resolve_search_name(
                entry.rom_base, title_hint=title_hint
            )
            clean_query = scraper.clean_title_tags(original_query)
            if clean_query != original_query:
                self.signals.progress_message.emit(
                    f'🔍 [Limpiando etiquetas] {original_query}… → '
                    f'Buscando en internet como "{clean_query}"'
                )
            try:
                result = _scrape_and_persist(
                    self.system, entry, include_video=self.include_video
                )
            except Exception:  # noqa: BLE001 - one bad game must not stop the batch
                errors += 1
                errors_list.append(entry.rom_file)
                self.signals.progress_message.emit(
                    "❌ Error: no se pudo procesar {}. Revisa que el archivo "
                    "exista y no esté dañado.".format(entry.rom_file)
                )
                continue
            if result.get("status") == "not_found":
                not_found += 1
                query = result.get("query") or entry.rom_base
                source = result.get("source")
                internal = result.get("identified")
                if internal:
                    label = "base FBNeo" if source == "rom_index" else (
                        "de hacks" if source == "hack" else "compatibilidad"
                    )
                    line = (
                        "❌ No encontrado: {}.\n"
                        "   La base de datos interna (índice {}) lo nombra como "
                        "«{}», pero TheGamesDB no devuelve nada para ese título.\n"
                        "   Sugerencia: el juego puede no estar catalogado, o su "
                        "nombre real difiere; edita el juego y corrige el Título "
                        "para volver a buscarlo manualmente.".format(
                            entry.rom_file, label, internal
                        )
                    )
                else:
                    line = (
                        "❌ No encontrado: {}.\n"
                        "   No hay nombre legible para este ROM (se buscó «{}» "
                        "directamente en TheGamesDB).\n"
                        "   Sugerencia: edita el juego y escribe su título real "
                        "en el campo Título (p. ej. «The Martial Masters»), o "
                        "regístralo en la base interna (compat_db).".format(
                            entry.rom_file, query
                        )
                    )
                self.signals.progress_message.emit(line)
            else:
                ok += 1
                extra = []
                if result.get("year"):
                    extra.append("Año " + result["year"])
                if result.get("developer"):
                    extra.append(result["developer"])
                if result.get("image_path"):
                    extra.append("carátula guardada")
                if result.get("video_path"):
                    extra.append("vídeo guardado en videos/")
                self.signals.progress_message.emit(
                    "✅ {}: «{}»{}.".format(
                        entry.rom_file,
                        result.get("title") or entry.rom_file,
                        (" · " + " · ".join(extra)) if extra else "",
                    )
                )
                if result.get("video_warning"):
                    self.signals.progress_message.emit(
                        "⚠️ {}".format(result["video_warning"])
                    )
        return {
            "ok": ok,
            "not_found": not_found,
            "errors": errors,
            "errors_list": errors_list,
        }