"""Main application window: assembles all panels and orchestrates workers."""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtCore import Qt, QThreadPool, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from arkos_companion import scanner, scraper
from arkos_companion.models import GameEntry, SystemFolder
from arkos_companion.rom_operations import check_bios
from arkos_companion.scraper import ScraperConfig
from arkos_companion.ui.api_key_dialog import ApiKeyDialog
from arkos_companion.ui.details_panel import DetailsPanel
from arkos_companion.ui.game_list import GameListPanel
from arkos_companion.ui.history_dialog import HistoryDialog
from arkos_companion.ui.mass_scraper_dialog import MassScraperDialog
from arkos_companion.ui.sidebar import SidebarPanel
from arkos_companion.ui.workers import (
    CoverWorker,
    DeleteWorker,
    EditMetadataWorker,
    LoadSystemWorker,
    OptimizeWorker,
    ScanSystemsWorker,
    ScrapeWorker,
)

_HOME = os.path.expanduser("~")


class MainWindow(QMainWindow):
    """Main window: EASYROMS picker, system sidebar, game list, details panel."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ArkOS R36S ROM Manager & Optimizer")
        self.resize(1280, 780)

        self.current_roms_root: Optional[str] = None
        self.current_system: Optional[SystemFolder] = None
        self.games: List[GameEntry] = []
        # Python references to every in-flight QRunnable worker: PyQt6 can
        # garbage-collect a runnable's WorkerSignals while its thread is still
        # running if nothing holds a reference (RuntimeError on emit).  The
        # reference is dropped once the worker reports finished/error.
        self._active_workers: List = []
        # True while any async task is running; used to ignore overlapping
        # user interaction (e.g. a sidebar click during a folder scan).
        self._task_running: bool = False

        self._build_ui()
        self._connect_signals()

        # Auto-detect a connected EASYROMS volume once the event loop starts;
        # the user can still pick the folder manually with the toolbar button.
        QTimer.singleShot(0, self._auto_detect_volume)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(8)

        # --- Top toolbar --------------------------------------------------
        toolbar = QWidget(central)
        toolbar.setObjectName("toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(10)

        self._select_button = QPushButton(
            "Seleccionar unidad/Carpeta EASYROMS", toolbar
        )
        self._path_label = QLabel("Sin carpeta seleccionada", toolbar)
        self._path_label.setStyleSheet("color: #565f89;")
        self._path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._history_button = QPushButton("Historial", toolbar)
        self._scrape_button = QPushButton("Scraper Masivo", toolbar)

        toolbar_layout.addWidget(self._select_button)
        toolbar_layout.addWidget(self._path_label, 1)
        toolbar_layout.addWidget(self._history_button)
        toolbar_layout.addWidget(self._scrape_button)
        root_layout.addWidget(toolbar)

        # --- Three-pane splitter -------------------------------------------
        self._splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self.sidebar = SidebarPanel(self._splitter)
        self.game_list = GameListPanel(self._splitter)
        self.details = DetailsPanel(self._splitter)
        self._splitter.addWidget(self.sidebar)
        self._splitter.addWidget(self.game_list)
        self._splitter.addWidget(self.details)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setStretchFactor(2, 2)
        self._splitter.setSizes([220, 640, 380])
        root_layout.addWidget(self._splitter, 1)

        self.setCentralWidget(central)

        status = QStatusBar(self)
        status.showMessage("Selecciona la carpeta EASYROMS de tu tarjeta SD.")
        self._busy = QProgressBar(status)
        self._busy.setRange(0, 0)  # indeterminate: animates while a task runs
        self._busy.setFixedWidth(170)
        self._busy.setTextVisible(False)
        self._busy.hide()
        status.addPermanentWidget(self._busy)
        self.setStatusBar(status)

    def _connect_signals(self) -> None:
        self._select_button.clicked.connect(self._on_select_folder)
        self._history_button.clicked.connect(self._on_history_clicked)
        self._scrape_button.clicked.connect(self._on_scrape_mass_clicked)
        self.sidebar.systemSelected.connect(self._on_sidebar_system_selected)
        self.game_list.gameSelected.connect(self._on_game_selected)
        self.details.optimizeRequested.connect(self._on_optimize_requested)
        self.details.deleteRequested.connect(self._on_delete_requested)
        self.details.editRequested.connect(self._on_edit_requested)
        self.details.scrapeRequested.connect(self._on_scrape_requested)
        self.details.coverRequested.connect(self._on_cover_requested)

    def _on_sidebar_system_selected(self, system: Optional[SystemFolder]) -> None:
        """User click on the sidebar: ignore while an async task is running.

        Prevents overlapping workers (e.g. loading a system while the folder
        scan is still in flight), which previously could race on Qt objects.
        The programmatic path (``_on_rescan_finished``) calls
        ``_on_system_selected`` directly and is not affected.
        """
        if self._task_running:
            return
        self._on_system_selected(system)

    # ------------------------------------------------------------------
    # Worker orchestration helper
    # ------------------------------------------------------------------
    def _run_worker(self, worker, on_finished, on_error) -> None:
        """Start a runnable on the global thread pool, wiring its signals.

        The worker is kept in ``self._active_workers`` until it reports
        finished/error, then released -- see the note on GC in ``__init__``.
        """
        self._active_workers.append(worker)

        def _done(result) -> None:
            try:
                on_finished(result)
            finally:
                self._release_worker(worker)

        def _failed(message: str) -> None:
            try:
                on_error(message)
            finally:
                self._release_worker(worker)

        worker.signals.finished.connect(_done)
        worker.signals.error.connect(_failed)
        QThreadPool.globalInstance().start(worker)

    def _release_worker(self, worker) -> None:
        """Drop the Python reference to a finished worker."""
        try:
            self._active_workers.remove(worker)
        except ValueError:
            pass

    def _set_busy_ui(self, busy: bool) -> None:
        """Disable interactive controls while an async task is running."""
        self._task_running = busy
        self._select_button.setEnabled(not busy)
        self._history_button.setEnabled(not busy)
        self._scrape_button.setEnabled(not busy)
        self.details.set_actions_enabled(not busy)
        # Busy indicator: shown for EVERY async operation (scan, load, delete,
        # optimize, metadata edit, refresh) since _set_busy_ui is their single
        # choke point.
        self._busy.setVisible(busy)

    def closeEvent(self, event) -> None:
        """Let in-flight workers finish before the interpreter tears down.

        Closing while a worker thread is mid-run would destroy its Qt signal
        objects underneath it (RuntimeError on emit).  Give the pool a bounded
        window; scans are listdir-heavy and finish quickly even on slow cards.
        """
        if self._active_workers:
            QThreadPool.globalInstance().waitForDone(5000)
        event.accept()

    # ------------------------------------------------------------------
    # Folder selection + scanning
    # ------------------------------------------------------------------
    def _on_select_folder(self) -> None:
        start = self.current_roms_root or _HOME
        folder = QFileDialog.getExistingDirectory(
            self, "Selecciona la carpeta EASYROMS", start
        )
        if not folder:
            return
        self._load_path(folder)

    def _auto_detect_volume(self) -> None:
        """Best-effort startup detection of a connected EASYROMS volume.

        Runs on the GUI thread: it is only a handful of stat/listdir calls.
        If nothing is found the app stays in the empty state and the user
        picks the folder manually.
        """
        self.statusBar().showMessage("Buscando volumen EASYROMS…")
        detected = scanner.detect_easyroms_volume()
        if detected:
            self._load_path(detected)
        else:
            self.statusBar().showMessage(
                "No se detectó el volumen EASYROMS. Selecciónalo manualmente."
            )

    def _load_path(self, path: str) -> None:
        """Start a background scan of a chosen folder (manual or auto-detected)."""
        self._path_label.setText("Escaneando…")
        self.statusBar().showMessage("Escaneando sistemas…")
        self._set_busy_ui(True)
        worker = ScanSystemsWorker(path)
        self._run_worker(
            worker,
            on_finished=self._on_scan_finished,
            on_error=self._on_fatal_error,
        )

    def _on_scan_finished(self, result) -> None:
        roms_root, systems = result
        self.current_roms_root = roms_root
        self.current_system = None
        self.games = []
        self._set_busy_ui(False)
        # The card is loaded: the button now means "pick a different folder".
        self._select_button.setText("Cambiar carpeta EASYROMS…")
        self._path_label.setText(roms_root)
        self._path_label.setToolTip(roms_root)
        self.sidebar.set_systems(systems)
        self.game_list.set_games([])
        self.details.clear()

        total = sum(s.rom_count for s in systems)
        if systems:
            self.statusBar().showMessage(
                "{} sistemas · {} juegos".format(len(systems), total)
            )
        else:
            self.statusBar().showMessage(
                "No se detectaron sistemas en esa carpeta."
            )

    # ------------------------------------------------------------------
    # System selection + loading
    # ------------------------------------------------------------------
    def _on_system_selected(self, system: Optional[SystemFolder]) -> None:
        if system is None:
            return
        self.current_system = system
        self.games = []
        self.details.clear()
        self.game_list.set_loading(True)
        self.game_list.set_games([])
        self.sidebar.set_ui_enabled(False)
        self.game_list.set_ui_enabled(False)
        self._set_busy_ui(True)
        self.statusBar().showMessage(
            "Cargando juegos de {}…".format(system.display_name or system.name)
        )

        worker = LoadSystemWorker(system)
        self._run_worker(
            worker,
            on_finished=lambda games: self._on_system_loaded(system, games),
            on_error=self._on_load_error,
        )

    def _on_system_loaded(self, system: SystemFolder, games: List[GameEntry]) -> None:
        self.games = games
        self._set_busy_ui(False)
        self.sidebar.set_ui_enabled(True)
        self.game_list.set_ui_enabled(True)
        self.game_list.set_loading(False)
        self.game_list.set_games(games)
        self.statusBar().showMessage(
            "{} juegos en {}".format(len(games), system.display_name or system.name)
        )

    def _on_game_selected(self, entry: Optional[GameEntry]) -> None:
        if entry is not None:
            self.details.set_system(self.current_system)
            self.details.show_entry(entry)
        else:
            self.details.clear()

    # ------------------------------------------------------------------
    # Optimize
    # ------------------------------------------------------------------
    def _on_optimize_requested(self, entry: GameEntry, target_folder: str) -> None:
        if not self.current_roms_root or not self.current_system:
            return
        self._set_busy_ui(True)
        self.statusBar().showMessage(f"Optimizando {entry.rom_file}…")

        worker = OptimizeWorker(
            self.current_roms_root, self.current_system, entry, target_folder
        )
        self._run_worker(
            worker,
            on_finished=lambda _res: self._on_optimize_done(entry, target_folder),
            on_error=self._on_operation_error,
        )

    def _on_optimize_done(self, entry: GameEntry, target_folder: str) -> None:
        """Warn about missing BIOS files, then refresh the whole view."""
        if entry.compat and entry.compat.get("bios"):
            missing = check_bios(
                self.current_roms_root, target_folder, entry.compat["bios"]
            )
            if missing:
                lines = []
                for bios in missing:
                    base = bios[:-4] if bios.lower().endswith(".zip") else bios
                    lines.append(f"Falta el archivo BIOS {base}.zip para este juego")
                QMessageBox.warning(self, "Aviso: BIOS requerida", "\n".join(lines))

        self.statusBar().showMessage(f"Juego movido a {target_folder}. Actualizando…")
        self._refresh_after_change()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def _on_delete_requested(self, entry: GameEntry) -> None:
        if not self.current_system:
            return
        self._set_busy_ui(True)
        self.statusBar().showMessage(f"Eliminando {entry.rom_file}…")

        worker = DeleteWorker(self.current_system, entry)
        self._run_worker(
            worker,
            on_finished=lambda _res: self._refresh_after_change(),
            on_error=self._on_operation_error,
        )

    # ------------------------------------------------------------------
    # Metadata edit
    # ------------------------------------------------------------------
    def _on_edit_requested(self, entry: GameEntry, metadata: dict) -> None:
        """Persist edited metadata through the worker, then refresh the view."""
        if not self.current_system:
            return
        self._set_busy_ui(True)
        self.statusBar().showMessage(f"Guardando metadatos de {entry.rom_file}…")

        worker = EditMetadataWorker(
            self.current_system,
            entry,
            title=metadata.get("title") or "",
            emulator=metadata.get("emulator") or "",
            core=metadata.get("core") or "",
            description=metadata.get("description") or "",
        )
        self._run_worker(
            worker,
            on_finished=lambda _res: self._on_edit_done(entry),
            on_error=self._on_operation_error,
        )

    def _on_edit_done(self, entry: GameEntry) -> None:
        """Re-scan after a successful metadata edit so disk is the source of truth."""
        self.statusBar().showMessage("Metadatos guardados. Actualizando…")
        self._refresh_after_change()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def _on_history_clicked(self) -> None:
        """Open the read-only journal viewer."""
        HistoryDialog(self).exec()

    # ------------------------------------------------------------------
    # Manual cover upload
    # ------------------------------------------------------------------
    def _on_cover_requested(self, entry: GameEntry, source_image: str) -> None:
        """Copy a user-picked image as the cover, then refresh the view."""
        if not self.current_system:
            return
        self._set_busy_ui(True)
        self.statusBar().showMessage(f"Asignando carátula a {entry.rom_file}…")

        worker = CoverWorker(self.current_system, entry, source_image)
        self._run_worker(
            worker,
            on_finished=lambda result: self._on_cover_done(entry, result),
            on_error=self._on_operation_error,
        )

    def _on_cover_done(self, entry: GameEntry, result: dict) -> None:
        """Show the new cover in the panel and the game list, then refresh."""
        updated = self.details.apply_cover_result(result)
        if updated is not None:
            self.game_list.refresh_entry(updated)
        removed = result.get("removed") or []
        extra = f" (reemplazó {len(removed)} imagen anterior)" if removed else ""
        self.statusBar().showMessage(
            f"Carátula asignada manualmente para {entry.rom_base}{extra}"
        )
        self._refresh_after_change()

    # ------------------------------------------------------------------
    # TheGamesDB scraping
    # ------------------------------------------------------------------
    def _on_scrape_mass_clicked(self) -> None:
        """Open the mass scraper over every game without a cover."""
        if not self.current_system or self._task_running:
            return
        if not scraper.effective_api_key():
            self._show_scraper_setup_hint()
            return
        # Load the current system asynchronously (reads disk), then open the
        # mass scraper with the games that have no cover yet.
        self.statusBar().showMessage("Buscando juegos sin carátula…")
        self._set_busy_ui(True)
        worker = LoadSystemWorker(self.current_system)
        self._run_worker(
            worker,
            on_finished=self._on_mass_scrape_games_loaded,
            on_error=self._on_load_error,
        )

    def _show_scraper_setup_hint(self) -> None:
        """No embedded app key and no override: guide the project author."""
        answer = QMessageBox.information(
            self,
            "TheGamesDB no configurado",
            "arkos-companion aún no tiene la API Key de TheGamesDB.\n\n"
            "La clave pertenece a la aplicación (no al usuario final): una vez "
            "obtenida, pégala en DEFAULT_API_KEY dentro de scraper.py y la "
            "usarán todos los usuarios sin configurar nada.\n\n"
            "¿Abrir ahora el asistente para ver cómo solicitarla?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            ApiKeyDialog(self).exec()

    def _on_mass_scrape_games_loaded(self, games: List[GameEntry]) -> None:
        self._set_busy_ui(False)
        candidates = [g for g in games if g.image_path is None or g.video_path is None]
        if not candidates:
            QMessageBox.information(
                self,
                "Scraper Masivo",
                "No hay juegos sin carátula ni vídeo en este sistema.",
            )
            return
        dialog = MassScraperDialog(self.current_system, candidates, self)
        dialog.exec()
        # Refresh the entire view once the batch scraper closes so that
        # any downloaded covers and XML modifications are instantly loaded.
        self._refresh_after_change()

    def _on_scrape_requested(self, entry: GameEntry, include_video: bool = False) -> None:
        """Single-game scrape requested from the details panel."""
        if not self.current_system:
            return
        if not scraper.effective_api_key():
            self._show_scraper_setup_hint()
            return
        self.details.set_scrape_busy(True)
        self.statusBar().showMessage(f"Buscando metadata de {entry.rom_file}…")
        worker = ScrapeWorker(
            self.current_system, entry, include_video=include_video
        )
        self._run_worker(
            worker,
            on_finished=self._on_scrape_done,
            on_error=self._on_scrape_error,
        )

    def _on_scrape_done(self, result: dict) -> None:
        self.details.set_scrape_busy(False)
        if result.get("status") == "not_found":
            QMessageBox.information(
                self,
                "Sin resultados",
                f"No se encontró metadata para {result['rom_base']} en TheGamesDB.",
            )
            return
        updated = self.details.apply_scrape_result(result)
        if updated is not None:
            # Refresh the thumbnail row in the game list with the same entry.
            self.game_list.refresh_entry(updated)
        if result.get("video_warning"):
            self.statusBar().showMessage(
                f"⚠️ {result['video_warning']}"
            )
        else:
            self.statusBar().showMessage(
                f"Metadata actualizada para {result['rom_base']}"
            )

    def _on_scrape_error(self, message: str) -> None:
        self.details.set_scrape_busy(False)
        if any(token in message for token in ("401", "403", "Unauthorized")):
            answer = QMessageBox.warning(
                self,
                "API Key rechazada",
                "TheGamesDB rechazó la API Key (error HTTP 401/403).\n\n"
                "Revisa que la hayas copiado completa desde "
                "api.thegamesdb.net/key.php tras obtener el acceso en el foro.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Open,
            )
            if answer == QMessageBox.StandardButton.Open:
                dialog = ApiKeyDialog(self)
                dialog.exec()
            return
        self._on_operation_error(message)

    # ------------------------------------------------------------------
    # Refresh after any destructive/move operation
    # ------------------------------------------------------------------
    def _refresh_after_change(self) -> None:
        """Re-scan systems (counts) and reload the current system if it remains."""
        if not self.current_roms_root:
            self.statusBar().showMessage("Sin carpeta seleccionada.")
            return
        current_name = self.current_system.name if self.current_system else None
        self._set_busy_ui(True)
        self.statusBar().showMessage("Actualizando sistemas…")

        worker = ScanSystemsWorker(self.current_roms_root)
        self._run_worker(
            worker,
            on_finished=lambda result: self._on_rescan_finished(result, current_name),
            on_error=self._on_fatal_error,
        )

    def _on_rescan_finished(self, result, current_name: Optional[str]) -> None:
        roms_root, systems = result
        self.current_roms_root = roms_root
        self._select_button.setText("Cambiar carpeta EASYROMS…")

        target = None
        for system in systems:
            if system.name == current_name:
                target = system
                break

        if target is not None:
            self.sidebar.set_systems(systems)
            self._on_system_selected(target)
        else:
            self._set_busy_ui(False)
            self.sidebar.set_systems(systems)
            self.current_system = None
            self.games = []
            self.game_list.set_games([])
            self.details.clear()
            self.statusBar().showMessage("Sistemas actualizados.")

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------
    def _on_fatal_error(self, message: str) -> None:
        """Robust error path for scanning/setup failures."""
        self._set_busy_ui(False)
        QMessageBox.critical(self, "Error", message)

    def _on_load_error(self, message: str) -> None:
        self._set_busy_ui(False)
        self.sidebar.set_ui_enabled(True)
        self.game_list.set_ui_enabled(True)
        self.game_list.set_loading(False)
        QMessageBox.critical(self, "Error al cargar", message)

    def _on_operation_error(self, message: str) -> None:
        """Show the exact error raised by a delete/optimize operation."""
        self._set_busy_ui(False)
        QMessageBox.critical(self, "Error en la operación", message)