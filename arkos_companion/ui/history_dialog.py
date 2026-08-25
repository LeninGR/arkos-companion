"""Read-only viewer dialog for the operation journal (arkos_history.jsonl)."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arkos_companion import history

_ACTION_LABELS = {
    history.ACTION_OPTIMIZE: "Optimizado",
    history.ACTION_DELETE: "Eliminado",
    history.ACTION_EDIT: "Metadatos editados",
    history.ACTION_SCRAPE: "Metadata de internet",
    history.ACTION_MEDIA_MANUAL: "Carátula manual",
}

_EMPTY_MESSAGE = "Aún no hay cambios registrados."


class HistoryDialog(QDialog):
    """``Historial de cambios`` viewer showing the journal newest entry first.

    ``history.load_entries`` never raises (malformed lines are skipped), so
    the dialog can safely render a journal file that is missing, empty or
    partially corrupted without crashing.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Historial de cambios")
        self.resize(760, 520)
        self._entries: List[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setStyleSheet(
            "QPlainTextEdit { background-color: #16161e; color: #c0caf5;"
            " border: 1px solid #2f3549; border-radius: 6px;"
            " font-family: Menlo, Consolas, monospace; }"
        )
        layout.addWidget(self._text, 1)

        self._reload()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Cerrar", self)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _reload(self) -> None:
        """Load every journal entry and render it newest first."""
        self._entries = list(reversed(history.load_entries()))
        if not self._entries:
            self._text.setPlainText(_EMPTY_MESSAGE)
            return
        rendered = "\n\n".join(
            self._format_entry(entry) for entry in self._entries
        )
        self._text.setPlainText(rendered)

    @staticmethod
    def _format_entry(entry: dict) -> str:
        """One human-readable block per journal entry (never raises)."""
        if not isinstance(entry, dict):
            return str(entry)
        timestamp = entry.get("ts") or "—"
        action = entry.get("action")
        action_label = _ACTION_LABELS.get(action, action if action else "¿?")
        system = entry.get("system") or "—"
        rom_file = entry.get("rom_file") or "—"
        title = entry.get("title")

        lines = [
            "[{}] {} — {} / {}".format(
                timestamp, action_label, system, rom_file
            )
        ]
        if title:
            lines.append("    {}".format(title))
        details = entry.get("details")
        if details:
            lines.append("    Detalles: {}".format(
                HistoryDialog._render_details(details)
            ))
        return "\n".join(lines)

    @staticmethod
    def _render_details(details: object) -> str:
        """Flatten the action-specific ``details`` dict into readable text."""
        if not isinstance(details, dict):
            return str(details)
        pieces = []
        for key, value in details.items():
            if isinstance(value, dict):
                rendered = ", ".join(
                    "{}={}".format(k, v) for k, v in value.items()
                )
            else:
                rendered = str(value)
            pieces.append("{}: {}".format(key, rendered))
        return "; ".join(pieces)