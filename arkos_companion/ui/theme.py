"""Dark theme for the application (a hand-written Tokyo-Night-style QSS)."""

from __future__ import annotations

# Palette constants (used directly by QSS).
BASE = "#1a1b26"
PANEL = "#24283b"
SURFACE = "#16161e"
SUBTLE = "#565f89"
TEXT = "#c0caf5"
ACCENT = "#7aa2f7"
ACCENT_ALT = "#bb9af7"
SUCCESS = "#9ece6a"
DANGER = "#f7768e"
WARNING = "#e0af68"

TOKYO_NIGHT_QSS = """
QMainWindow {
    background-color: #1a1b26;
    color: #c0caf5;
}

QWidget {
    background-color: #1a1b26;
    color: #c0caf5;
    font-size: 14px;
}

QToolBar, QWidget#toolbar {
    background-color: #16161e;
    border: none;
    border-bottom: 1px solid #2f3549;
    padding: 6px;
    spacing: 8px;
}

QListWidget {
    background-color: #24283b;
    border: 1px solid #2f3549;
    border-radius: 6px;
    padding: 4px;
    outline: 0;
    alternate-background-color: #1f2335;
}
QListWidget::item {
    padding: 6px 8px;
    border-radius: 4px;
    margin: 1px 2px;
}
QListWidget::item:selected {
    background-color: #7aa2f7;
    color: #1a1b26;
}
QListWidget::item:hover:!selected {
    background-color: #2f3549;
}

QScrollArea {
    background-color: #24283b;
    border: 1px solid #2f3549;
    border-radius: 6px;
}

QLabel {
    background-color: transparent;
    color: #c0caf5;
}

QPushButton, QToolButton {
    background-color: #2f3549;
    color: #c0caf5;
    border: 1px solid #3b4261;
    border-radius: 6px;
    padding: 6px 14px;
}
QPushButton:hover, QToolButton:hover {
    background-color: #3b4261;
}
QPushButton:pressed, QToolButton:pressed {
    background-color: #292e42;
}
QPushButton:disabled, QToolButton:disabled,
QPushButton[btnRole="danger"]:disabled,
QPushButton[btnRole="success"]:disabled {
    color: #565f89;
    background-color: #1f2335;
    border-color: #2f3549;
}

QPushButton[btnRole="danger"] {
    background-color: rgba(247, 118, 142, 0.14);
    border-color: #f7768e;
    color: #f7768e;
}
QPushButton[btnRole="danger"]:hover {
    background-color: rgba(247, 118, 142, 0.30);
}

QPushButton[btnRole="success"] {
    background-color: rgba(158, 206, 106, 0.14);
    border-color: #9ece6a;
    color: #9ece6a;
}
QPushButton[btnRole="success"]:hover {
    background-color: rgba(158, 206, 106, 0.30);
}

QLineEdit {
    background-color: #16161e;
    border: 1px solid #2f3549;
    border-radius: 4px;
    padding: 4px 6px;
}

QSplitter::handle {
    background-color: #16161e;
}
QSplitter::handle:hover {
    background-color: #7aa2f7;
}

QStatusBar {
    background-color: #16161e;
    color: #a9b1d6;
    border-top: 1px solid #2f3549;
}
QStatusBar::item {
    border: none;
}

QProgressBar {
    background-color: #1f2335;
    border: 1px solid #2f3549;
    border-radius: 4px;
    min-height: 10px;
    max-height: 10px;
}
QProgressBar::chunk {
    background-color: #7aa2f7;
    border-radius: 3px;
}

QMenu {
    background-color: #24283b;
    color: #c0caf5;
    border: 1px solid #2f3549;
}
QMenu::item {
    padding: 6px 24px;
}
QMenu::item:selected {
    background-color: #7aa2f7;
    color: #1a1b26;
}

QMessageBox {
    background-color: #24283b;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #3b4261;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #7aa2f7;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #3b4261;
    border-radius: 5px;
    min-width: 24px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
"""


def apply_theme(app) -> None:
    """Apply the dark theme to a QApplication.

    Uses the Fusion style so the QSS sheet is actually honoured by Qt.
    """
    app.setStyle("Fusion")
    app.setStyleSheet(TOKYO_NIGHT_QSS)