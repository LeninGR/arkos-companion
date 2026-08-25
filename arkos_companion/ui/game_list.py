"""Center panel: the game list for the active system, colour-coded by status."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from arkos_companion.compat_db import recommended_action
from arkos_companion.models import GameEntry, OptimizationStatus

_COLOR_OK = QColor("#9ece6a")
_COLOR_BAD = QColor("#f7768e")
_COLOR_WARN = QColor("#e0af68")
_COLOR_NEUTRAL = QColor("#c0caf5")


class GameListPanel(QWidget):
    """Scrollable list of games for the current system with status colours."""

    gameSelected = pyqtSignal(object)  # GameEntry or None

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._games: List[GameEntry] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        empty = QLabel("Selecciona un sistema para ver sus juegos.", self)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        empty.setStyleSheet("color: #565f89; padding: 16px;")
        self._empty_label = empty
        layout.addWidget(empty)

        self._list = QListWidget(self)
        self._list.setWordWrap(False)
        layout.addWidget(self._list, 1)

        self._list.currentItemChanged.connect(self._on_current_changed)

    def _on_current_changed(self, current, _previous) -> None:
        if current is None:
            self.gameSelected.emit(None)
            return
        entry = current.data(Qt.ItemDataRole.UserRole)
        self.gameSelected.emit(entry)

    @staticmethod
    def _status_colors(entry: GameEntry) -> QColor:
        if entry.status == OptimizationStatus.CORRECT:
            return _COLOR_OK
        if entry.status == OptimizationStatus.WARNING:
            return _COLOR_WARN
        if entry.status == OptimizationStatus.NEEDS_OPTIMIZATION:
            return _COLOR_BAD
        return _COLOR_NEUTRAL

    @staticmethod
    def _status_label(entry: GameEntry) -> str:
        if entry.status == OptimizationStatus.CORRECT:
            return "Correcto"
        if entry.status == OptimizationStatus.WARNING:
            return "Se recomienda optimizar"
        if entry.status == OptimizationStatus.NEEDS_OPTIMIZATION:
            return "Requiere optimización"
        return "Desconocido"

    def _item_text(self, entry: GameEntry) -> str:
        if entry.title and entry.title != entry.rom_file:
            return f"{entry.rom_file} — {entry.title}"
        return entry.rom_file

    def _item_tooltip(self, entry: GameEntry) -> str:
        lines = [f"Ruta: {entry.sys_folder}/{entry.rom_file}"]
        lines.append(f"Estado: {self._status_label(entry)}")
        if entry.compat and entry.compat.get("note"):
            lines.append(entry.compat["note"])
        return "\n".join(lines)

    def _load_icon(self, entry: GameEntry) -> Optional[QIcon]:
        """Try to build a small thumbnail icon without ever blocking the UI."""
        if not entry.image_path:
            return None
        try:
            pixmap = QPixmap(entry.image_path)
            if pixmap.isNull():
                return None
            scaled = pixmap.scaled(
                32, 32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            return QIcon(scaled)
        except Exception:  # noqa: BLE001 - a broken image must never crash the list
            return None

    def set_games(self, games: List[GameEntry]) -> None:
        """Populate the list, sorted alphabetically by ROM file (case-insensitive)."""
        self._games = sorted(games, key=lambda g: g.rom_file.lower())

        self._list.blockSignals(True)
        self._list.clear()

        for entry in self._games:
            self._list.addItem(self._build_item(entry))

        self._list.blockSignals(False)

        self._empty_label.setVisible(len(games) == 0)
        self._list.setVisible(len(games) > 0)

    def _build_item(self, entry: GameEntry) -> QListWidgetItem:
        """Create a list item for ``entry`` (status colour, icon, tooltip)."""
        item = QListWidgetItem(self._item_text(entry))
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setForeground(self._status_colors(entry))
        item.setToolTip(self._item_tooltip(entry))

        if entry.status in (
            OptimizationStatus.NEEDS_OPTIMIZATION,
            OptimizationStatus.WARNING,
        ):
            target = recommended_action(entry.sys_folder, entry.rom_base)
            if target:
                item.setText(item.text() + f"  [⚠ optimizar → {target}]")

        icon = self._load_icon(entry)
        if icon is not None:
            item.setIcon(icon)
        return item

    def refresh_entry(self, entry: GameEntry) -> None:
        """Update one game in place after async changes (e.g. a scraped cover).

        Replaces the stored ``GameEntry`` and rebuilds its row without
        touching the selection or re-sorting the rest of the list.
        """
        for index, stored in enumerate(self._games):
            if stored.rom_file == entry.rom_file:
                self._games[index] = entry
                item = self._list.item(index)
                if item is not None:
                    self._list.blockSignals(True)
                    item.setText(self._item_text(entry))
                    item.setForeground(self._status_colors(entry))
                    item.setToolTip(self._item_tooltip(entry))
                    opt_target = (
                        recommended_action(entry.sys_folder, entry.rom_base)
                        if entry.status
                        in (OptimizationStatus.NEEDS_OPTIMIZATION, OptimizationStatus.WARNING)
                        else None
                    )
                    icon = self._load_icon(entry)
                    item.setIcon(icon if icon is not None else QIcon())
                    self._list.blockSignals(False)
                return

    def set_loading(self, loading: bool) -> None:
        """Show a 'loading' hint in place of the list while games are scanned.

        Called by the main window when a system is selected (loading=True) and
        when its scan finishes or fails (loading=False).  Visibility of the
        empty hint after that is owned by ``set_games``.
        """
        if loading:
            self._empty_label.setText("Cargando juegos…")
            self._empty_label.setVisible(True)
            self._list.setVisible(False)
        else:
            self._empty_label.setText("Selecciona un sistema para ver sus juegos.")

    def set_ui_enabled(self, enabled: bool) -> None:
        self._list.setEnabled(enabled)

    def current_game(self) -> Optional[GameEntry]:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)