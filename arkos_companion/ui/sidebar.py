"""Left panel: the list of detected game systems with their ROM counts."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from arkos_companion.models import SystemFolder


class SidebarPanel(QWidget):
    """Left sidebar listing every detected system folder on the card."""

    systemSelected = pyqtSignal(object)  # SystemFolder or None

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._systems: List[SystemFolder] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        empty = QLabel(
            "No se detectaron sistemas.\nSelecciona EASYROMS.",
            self,
        )
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        empty.setStyleSheet("color: #565f89; padding: 16px;")
        empty.setObjectName("emptyHint")
        self._empty_label = empty
        layout.addWidget(empty)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self._list, 1)

        self._list.currentItemChanged.connect(self._on_current_changed)

    def _on_current_changed(self, current, _previous) -> None:
        """Forward the currently selected system (or None) to listeners."""
        if current is None:
            self.systemSelected.emit(None)
            return
        system = current.data(Qt.ItemDataRole.UserRole)
        self.systemSelected.emit(system)

    def set_systems(self, systems: List[SystemFolder]) -> None:
        """Replace the system list, preserving the current selection by name."""
        selected_name = None
        current = self.current_system()
        if current is not None:
            selected_name = current.name

        self._systems = list(systems)
        self._list.blockSignals(True)
        self._list.clear()

        for system in systems:
            display = system.display_name or system.name
            item = QListWidgetItem(f"  {display}   ({system.rom_count})")
            item.setData(Qt.ItemDataRole.UserRole, system)
            self._list.addItem(item)

        # Restore the previous selection if the system still exists.
        if selected_name is not None:
            for i in range(self._list.count()):
                item = self._list.item(i)
                system = item.data(Qt.ItemDataRole.UserRole)
                if system is not None and system.name == selected_name:
                    self._list.setCurrentRow(i)
                    break
        self._list.blockSignals(False)

        self._empty_label.setVisible(len(systems) == 0)
        self._list.setVisible(len(systems) > 0)

    def current_system(self) -> Optional[SystemFolder]:
        """Return the currently selected SystemFolder, or None."""
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def set_ui_enabled(self, enabled: bool) -> None:
        """Enable/disable user interaction with the sidebar."""
        self._list.setEnabled(enabled)