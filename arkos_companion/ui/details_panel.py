"""Right panel: cover image, video preview, metadata and game actions."""

from __future__ import annotations

import dataclasses
import html
import os
from typing import Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from arkos_companion.compat_db import display_name, recommended_action
from arkos_companion.models import GameEntry, OptimizationStatus
from arkos_companion.ui.edit_metadata_dialog import EditMetadataDialog

_MAX_COVER_SIZE = 360
_MAX_DESCRIPTION_CHARS = 400

# Qt 6 renamed QMediaPlayer::MediaStatus::EndedMedia (Qt 5 name) to EndOfMedia.
# Resolve defensively at import time so a future Qt rename can never crash the
# handler again; None never equals a real enum member.
_MEDIA_ENDED = getattr(QMediaPlayer.MediaStatus, "EndOfMedia", None)
_MEDIA_INVALID = getattr(QMediaPlayer.MediaStatus, "InvalidMedia", None)

_STATUS_META = {
    OptimizationStatus.CORRECT: ("Correcto", "#9ece6a"),
    OptimizationStatus.WARNING: ("Se recomienda optimizar", "#e0af68"),
    OptimizationStatus.NEEDS_OPTIMIZATION: ("Requiere optimización", "#f7768e"),
    OptimizationStatus.UNKNOWN: ("Desconocido", "#565f89"),
}


class DetailsPanel(QScrollArea):
    """Scrollable detail view for the currently selected game."""

    optimizeRequested = pyqtSignal(object, str)  # GameEntry, target folder
    deleteRequested = pyqtSignal(object)         # GameEntry
    editRequested = pyqtSignal(object, object)   # GameEntry, metadata dict
    scrapeRequested = pyqtSignal(object, bool)   # GameEntry, include_video flag
    coverRequested = pyqtSignal(object, str)     # GameEntry, source image path

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._entry: Optional[GameEntry] = None
        self._system: Optional[SystemFolder] = None
        self._last_video_path: Optional[str] = None
        self._playing = False
        self._scraped_image_path: Optional[str] = None

        container = QWidget(self)
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # --- Cover image ---------------------------------------------------
        self._cover_label = QLabel("—", container)
        self._cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_label.setMinimumHeight(120)
        self._cover_label.setStyleSheet("color: #565f89;")
        layout.addWidget(self._cover_label)

        self._cover_button = QPushButton("Subir Carátula…", container)
        self._cover_button.setToolTip(
            "Asigna una imagen local como carátula del juego "
            "(útil cuando no existe en internet)."
        )
        self._cover_button.clicked.connect(self._on_cover_clicked)
        layout.addWidget(self._cover_button)

        # --- Video preview --------------------------------------------------
        self._video_widget = QVideoWidget(container)
        self._video_widget.setMinimumHeight(160)
        self._video_widget.setStyleSheet("background-color: #16161e;")
        self._video_widget.hide()

        video_row = QHBoxLayout()
        self._play_button = QPushButton("⏯ Reproducir/Pausar", container)
        self._play_button.clicked.connect(self._toggle_playback)
        video_row.addWidget(self._play_button)
        video_row.addStretch(1)

        layout.addWidget(self._video_widget)
        layout.addLayout(video_row)

        self._media_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._audio_output.setMuted(True)  # muted by default, per spec
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.setVideoOutput(self._video_widget)
        self._media_player.mediaStatusChanged.connect(self._on_media_status)

        # --- Metadata -------------------------------------------------------
        self._meta_label = QLabel(container)
        self._meta_label.setTextFormat(Qt.TextFormat.RichText)
        self._meta_label.setWordWrap(True)
        self._meta_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._meta_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._meta_label)

        # --- Action buttons --------------------------------------------------
        actions = QHBoxLayout()
        self._optimize_button = QPushButton("Optimizar Emulador/Core", container)
        self._optimize_button.setProperty("btnRole", "success")
        self._optimize_button.clicked.connect(self._on_optimize_clicked)

        self._scrape_button = QPushButton("Buscar Metadata en Internet", container)
        self._scrape_button.setToolTip(
            "Busca título y descripción en TheGamesDB y descarga la carátula. "
            "Guarda los textos con Actualizar Metadata."
        )
        self._scrape_button.clicked.connect(self._on_scrape_clicked)

        self._edit_button = QPushButton("Actualizar Metadata", container)
        self._edit_button.clicked.connect(self._on_edit_clicked)

        self._delete_button = QPushButton("Eliminar juego por completo", container)
        self._delete_button.setProperty("btnRole", "danger")
        self._delete_button.clicked.connect(self._on_delete_clicked)

        actions.addWidget(self._optimize_button)
        actions.addWidget(self._scrape_button)
        actions.addWidget(self._edit_button)
        actions.addWidget(self._delete_button)
        layout.addLayout(actions)

        self._include_video_checkbox = QCheckBox(
            "Descargar vídeo de muestra", container
        )
        self._include_video_checkbox.setToolTip(
            "Descarga el vídeo promocional del juego desde TheGamesDB como "
            "mp4 local en videos/ (requiere red; los vídeos pueden pesar "
            "varios MB)."
        )
        self._include_video_checkbox.setChecked(False)
        layout.addWidget(self._include_video_checkbox)

        self._scrape_status_label = QLabel("Buscando metadata en internet…", container)
        self._scrape_status_label.setStyleSheet("color: #565f89;")
        self._scrape_status_label.hide()
        layout.addWidget(self._scrape_status_label)

        layout.addStretch(1)

    # ------------------------------------------------------------------
    # Public API used by the main window
    # ------------------------------------------------------------------
    def set_system(self, system: Optional[SystemFolder]) -> None:
        """Remember the current system folder (needed by the edit dialog)."""
        self._system = system

    def show_entry(self, entry: GameEntry) -> None:
        """Render the details (cover, video, metadata, actions) for a game."""
        self._entry = entry

        self._show_cover(entry)
        self._show_video(entry.video_path)
        self._meta_label.setText(self._metadata_html(entry))

        can_optimize = (
            entry.status
            in (
                OptimizationStatus.NEEDS_OPTIMIZATION,
                OptimizationStatus.WARNING,
            )
            and recommended_action(entry.sys_folder, entry.rom_base) is not None
        )
        self._optimize_button.setEnabled(can_optimize)
        self._delete_button.setEnabled(True)
        self._edit_button.setEnabled(True)
        self._scrape_button.setEnabled(True)
        self._cover_button.setEnabled(True)
        self._include_video_checkbox.setEnabled(True)

    def clear(self) -> None:
        """Reset the panel: stop video and show the empty state."""
        self._entry = None
        self._stop_video()
        self._cover_label.setPixmap(QPixmap())
        self._cover_label.setText("—")
        self._meta_label.setText(
            "<span style='color:#565f89;'>Selecciona un juego para ver sus "
            "detalles.</span>"
        )
        self._optimize_button.setEnabled(False)
        self._delete_button.setEnabled(False)
        self._edit_button.setEnabled(False)
        self._scrape_button.setEnabled(False)
        self._cover_button.setEnabled(False)
        self._include_video_checkbox.setEnabled(False)
        self._scrape_status_label.hide()

    def set_actions_enabled(self, enabled: bool) -> None:
        """Enable/disable the action buttons (used while a task is running)."""
        entry = self._entry
        can_optimize = (
            enabled
            and entry is not None
            and entry.status
            in (
                OptimizationStatus.NEEDS_OPTIMIZATION,
                OptimizationStatus.WARNING,
            )
            and recommended_action(entry.sys_folder, entry.rom_base) is not None
        )
        self._optimize_button.setEnabled(can_optimize)
        self._delete_button.setEnabled(enabled and entry is not None)
        self._edit_button.setEnabled(enabled and entry is not None)
        self._scrape_button.setEnabled(enabled and entry is not None)
        self._cover_button.setEnabled(enabled and entry is not None)
        self._include_video_checkbox.setEnabled(enabled and entry is not None)

    def set_scrape_busy(self, busy: bool) -> None:
        """Show/hide the scrape hint and lock the scrape button while busy."""
        self._scrape_status_label.setVisible(busy)
        self._scrape_button.setEnabled(not busy and self._entry is not None)

    def apply_scrape_result(self, result: dict) -> GameEntry:
        """Render a successful scrape: new cover and metadata in the panel.

        Returns the updated ``GameEntry`` so the caller can refresh the game
        list with the same object.  The metadata is already persisted in
        ``gamelist.xml`` by the worker; this only updates the in-memory view.
        """
        if self._entry is None:
            return None
        self._scraped_image_path = result.get("image_path")
        video_path = result.get("video_path") or self._entry.video_path
        updated = dataclasses.replace(
            self._entry,
            title=result.get("title") or self._entry.title,
            description=result.get("description") or self._entry.description,
            image_path=self._scraped_image_path or self._entry.image_path,
            video_path=video_path,
        )
        self._entry = updated
        self._show_cover(self._entry)
        self._show_video(self._entry.video_path)
        self._meta_label.setText(self._metadata_html(self._entry))
        self._edit_button.setEnabled(True)
        return updated

    def apply_cover_result(self, result: dict) -> GameEntry:
        """Render a manual cover upload: new image path in the panel.

        Returns the updated ``GameEntry`` so the caller can refresh the game
        list with the same object.  The image is already persisted on disk
        and registered in ``gamelist.xml`` by the worker; this only updates
        the in-memory view.
        """
        if self._entry is None:
            return None
        image_path = result.get("image_path") or self._entry.image_path
        updated = dataclasses.replace(self._entry, image_path=image_path)
        self._entry = updated
        self._scraped_image_path = image_path
        self._show_cover(self._entry)
        self._meta_label.setText(self._metadata_html(self._entry))
        self._edit_button.setEnabled(True)
        return updated

    # ------------------------------------------------------------------
    # Cover + video
    # ------------------------------------------------------------------
    def _show_cover(self, entry: GameEntry) -> None:
        self._cover_label.setPixmap(QPixmap())
        if not entry.image_path:
            self._cover_label.setText("—")
            return
        try:
            pixmap = QPixmap(entry.image_path)
            if pixmap.isNull():
                self._cover_label.setText("—")
                return
            scaled = pixmap.scaled(
                _MAX_COVER_SIZE, _MAX_COVER_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._cover_label.setPixmap(scaled)
        except Exception:  # noqa: BLE001 - broken image must not crash the panel
            self._cover_label.setText("—")

    def _show_video(self, video_path: Optional[str]) -> None:
        if not video_path:
            self._stop_video()
            return
        self._video_widget.show()
        self._play_button.setEnabled(True)
        if self._last_video_path == video_path:
            self._sync_play_button()
            return
        try:
            self._media_player.setSource(QUrl.fromLocalFile(video_path))
        except Exception:  # noqa: BLE001 - codec/backends failures are non-fatal
            self._video_widget.hide()
            self._play_button.setEnabled(False)
            return
        self._last_video_path = video_path
        self._media_player.play()
        self._playing = True
        self._sync_play_button()

    def _stop_video(self) -> None:
        self._media_player.stop()
        self._media_player.setSource(QUrl())
        self._last_video_path = None
        self._playing = False
        self._video_widget.hide()
        self._play_button.setEnabled(False)
        self._sync_play_button()

    def _toggle_playback(self) -> None:
        if self._playing:
            self._media_player.pause()
            self._playing = False
        else:
            self._media_player.play()
            self._playing = True
        self._sync_play_button()

    def _on_media_status(self, status) -> None:
        """Keep the play button in sync when playback ends or errors out."""
        if status == _MEDIA_ENDED or status == _MEDIA_INVALID:
            self._playing = False
            self._sync_play_button()

    def _sync_play_button(self) -> None:
        self._play_button.setText(
            "⏸ Pausar" if self._playing else "⏯ Reproducir"
        )

    # ------------------------------------------------------------------
    # Metadata rendering
    # ------------------------------------------------------------------
    def _metadata_html(self, entry: GameEntry) -> str:
        e = html.escape
        status_text, status_color = _STATUS_META[entry.status]

        def value(text: Optional[str], fallback: str = "—") -> str:
            return e(text) if text else fallback

        def folder_label(folder: str) -> str:
            return e(display_name(folder))

        note = "—"
        if entry.compat and entry.compat.get("note"):
            note = e(entry.compat["note"])

        description = entry.description
        if description and len(description) > _MAX_DESCRIPTION_CHARS:
            description = description[:_MAX_DESCRIPTION_CHARS].rstrip() + "…"
        description_html = value(description, "No asignada")

        parts = [
            f"<h2 style='margin:0; color:#c0caf5;'>{e(entry.title)}</h2>",
            "<table cellspacing='6' style='font-size:14px;'>",
            f"<tr><td style='color:#565f89;'>Juego:</td><td>{e(entry.rom_file)}</td></tr>",
            f"<tr><td style='color:#565f89; vertical-align:top;'>Descripción:</td>"
            f"<td style='max-width:380px;'>{description_html}</td></tr>",
        ]

        if entry.compat and entry.compat.get("system"):
            parts.append(
                f"<tr><td style='color:#565f89;'>Sistema:</td>"
                f"<td>{e(entry.compat['system'])}</td></tr>"
            )
        else:
            parts.append(
                f"<tr><td style='color:#565f89;'>Sistema:</td><td>—</td></tr>"
            )

        parts.append(
            f"<tr><td style='color:#565f89;'>Emulador:</td>"
            f"<td>{value(entry.emulator, 'No asignado')}</td></tr>"
        )
        parts.append(
            f"<tr><td style='color:#565f89;'>Core:</td>"
            f"<td>{value(entry.core, 'No asignado')}</td></tr>"
        )
        parts.append(
            f"<tr><td style='color:#565f89;'>Carpeta:</td>"
            f"<td>{folder_label(entry.sys_folder)}</td></tr>"
        )
        parts.append(
            f"<tr><td style='color:#565f89;'>Estado:</td>"
            f"<td><b style='color:{status_color};'>{status_text}</b></td></tr>"
        )
        parts.append(
            f"<tr><td style='color:#565f89; vertical-align:top;'>Nota:</td>"
            f"<td>{note}</td></tr>"
        )
        parts.append("</table>")
        return "".join(parts)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_optimize_clicked(self) -> None:
        entry = self._entry
        if entry is None or entry.status not in (
            OptimizationStatus.NEEDS_OPTIMIZATION,
            OptimizationStatus.WARNING,
        ):
            return
        target = recommended_action(entry.sys_folder, entry.rom_base)
        if not target:
            return

        answer = QMessageBox.question(
            self,
            "Optimizar",
            f"¿Mover {entry.title} de {entry.sys_folder} a {target}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.optimizeRequested.emit(entry, target)

    def _on_delete_clicked(self) -> None:
        entry = self._entry
        if entry is None:
            return

        lines = ["Se eliminarán los siguientes archivos:"]
        lines.append(f"• {entry.rom_file}")
        if entry.image_path:
            lines.append(f"• {os.path.basename(entry.image_path)}")
        if entry.video_path:
            lines.append(f"• {os.path.basename(entry.video_path)}")
        lines.append("")
        lines.append("Nota: se eliminará su entrada en gamelist.xml.")

        answer = QMessageBox.question(
            self,
            "Eliminar juego",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.deleteRequested.emit(entry)

    def _on_scrape_clicked(self) -> None:
        entry = self._entry
        if entry is None:
            return
        # No configuration gate here: the app uses its embedded API key by
        # default (main_window shows the setup hint when no key exists).
        self.scrapeRequested.emit(
            self._entry, self._include_video_checkbox.isChecked()
        )

    def _on_cover_clicked(self) -> None:
        """Pick a local image file and emit it as the new cover."""
        entry = self._entry
        if entry is None:
            return
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Seleccionar carátula",
            "",
            "Imágenes (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;"
            "Todos los archivos (*)",
        )
        if path:
            self.coverRequested.emit(entry, path)

    def _on_edit_clicked(self) -> None:
        entry = self._entry
        if entry is None:
            return
        dialog = EditMetadataDialog(entry, self._system, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.editRequested.emit(entry, dialog.values())