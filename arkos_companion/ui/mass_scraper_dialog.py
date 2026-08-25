"""Dialog that runs the TheGamesDB scraper over every game without a cover.

Self-contained: it owns its ``MassScrapeWorker`` on the global thread pool
and reports progress through a progress bar, a status label and a small log.
Nothing is emitted back to the main window -- the user rescans manually to
see the new covers.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arkos_companion.compat_db import display_name
from arkos_companion.models import GameEntry, SystemFolder
from arkos_companion.ui.workers import MassScrapeWorker


class MassScraperDialog(QDialog):
    """Run the batch scraper and stream its progress to the user."""

    def __init__(
        self,
        system: SystemFolder,
        entries: List[GameEntry],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scraper Masivo")
        self.resize(560, 420)
        self.system = system
        self.entries = entries
        self._no_cover_entries = [g for g in entries if g.image_path is None]
        self._no_video_entries = [g for g in entries if g.video_path is None]
        self._effective_entries: List[GameEntry] = []
        # Kept alive while running so Qt never GCs the worker's signals.
        self._worker: Optional[MassScrapeWorker] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self._summary_label = QLabel("", self)
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        self._progress = QProgressBar(self)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._progress_label = QLabel("", self)
        self._progress_label.setWordWrap(True)
        layout.addWidget(self._progress_label)

        self._include_video_checkbox = QCheckBox("Descargar vídeo de muestra", self)
        self._include_video_checkbox.setToolTip(
            "Descarga el vídeo promocional de cada juego desde TheGamesDB "
            "como mp4 local en su carpeta videos/ (requiere red; los vídeos "
            "pueden pesar varios MB)."
        )
        self._include_video_checkbox.setChecked(False)
        self._include_video_checkbox.stateChanged.connect(lambda: self._update_effective_entries())
        layout.addWidget(self._include_video_checkbox)

        self._log = QPlainTextEdit(self)
        self._log.setReadOnly(True)
        layout.addWidget(self._log, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._start_button = QPushButton("Iniciar", self)
        self._start_button.setProperty("btnRole", "success")
        self._start_button.clicked.connect(self._on_start_clicked)
        self._close_button = QPushButton("Cerrar", self)
        self._close_button.setEnabled(False)
        self._close_button.clicked.connect(self.accept)
        buttons.addWidget(self._start_button)
        buttons.addWidget(self._close_button)
        layout.addLayout(buttons)

        self._update_effective_entries()

    def _update_effective_entries(self) -> None:
        """Update the effective list of games to scrape and refresh the UI states."""
        if self._include_video_checkbox.isChecked():
            self._effective_entries = self.entries
            self._summary_label.setText(
                "{} juegos pendientes (sin carátula o vídeo) en {}".format(
                    len(self._effective_entries), display_name(self.system.name)
                )
            )
        else:
            self._effective_entries = self._no_cover_entries
            self._summary_label.setText(
                "{} juegos sin carátula en {}".format(
                    len(self._effective_entries), display_name(self.system.name)
                )
            )

        self._progress.setRange(0, max(len(self._effective_entries), 1))
        if self._worker is None:
            self._start_button.setEnabled(len(self._effective_entries) > 0)

    # ------------------------------------------------------------------
    # Worker orchestration
    # ------------------------------------------------------------------
    def _on_start_clicked(self) -> None:
        if not self._effective_entries or self._worker is not None:
            return
        self._start_button.setEnabled(False)
        self._progress_label.setText("")
        worker = MassScrapeWorker(
            self.system,
            self._effective_entries,
            include_video=self._include_video_checkbox.isChecked(),
        )
        self._worker = worker
        worker.signals.progress_value.connect(self._on_progress_value)
        worker.signals.progress_message.connect(self._on_progress_message)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    def _on_progress_value(self, index: int, total: int) -> None:
        self._progress.setRange(0, total)
        self._progress.setValue(index)

    def _on_progress_message(self, text: str) -> None:
        self._progress_label.setText(text)
        self._log.appendPlainText(text)

    def _on_finished(self, summary: dict) -> None:
        self._worker = None
        self._progress.setValue(self._progress.maximum())
        self._log.appendPlainText(
            "\n=== PROCESO COMPLETADO ===\n"
            "Guardados: {} · Sin metadata: {} · Errores: {}".format(
                summary.get("ok", 0),
                summary.get("not_found", 0),
                summary.get("errors", 0),
            )
        )
        self._start_button.setText("Completado")
        self._start_button.setEnabled(False)
        self._close_button.setEnabled(True)
        self._close_button.setText("Cerrar")
        self._close_button.setFocus()

    def _on_error(self, message: str) -> None:
        self._worker = None
        QMessageBox.warning(self, "Error en el scraper", message)
        self._start_button.setText("Iniciar")
        self._start_button.setEnabled(len(self._effective_entries) > 0)
        self._close_button.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.set_cancelled()
        event.accept()