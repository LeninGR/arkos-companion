"""Modal dialog to view or replace the TheGamesDB API key.

By default the application ships with its OWN embedded TheGamesDB API key
(``scraper.DEFAULT_API_KEY``) and end users never touch this screen.  This
dialog is the ADVANCED path: the project author uses it once to obtain and
paste the application key, and any user can override the embedded key with a
personal one (e.g. to use their own per-IP quota).  The key is stored in
``scraper_config.json`` (next to the journal) through ``ScraperConfig``.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arkos_companion import scraper
from arkos_companion.scraper import ScraperConfig

# Copyable forum post the user can paste into the "API Requests" board.
_FORUM_REQUEST_TEMPLATE = (
    "Hi! I would like API access for my application:\n"
    "Name: {app_name} (version {app_version})\n"
    "Purpose: Desktop ROM manager for ArkOS handhelds. It reads the local "
    "game folders and queries TheGamesDB for game titles, descriptions and "
    "cover art links. No public service, no bulk downloads."
)


class ApiKeyDialog(QDialog):
    """Step-by-step setup of the TheGamesDB scraper key."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configurar TheGamesDB")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        steps = QLabel(
            "La app ya trae su propia API Key de TheGamesDB y funciona sin "
            "configurar nada. Usa esta ventana solo para:\n\n"
            "• Sustituir la clave de la app (si se renovó o se agotó la cuota).\n"
            "• Usar tu clave personal (cada clave pública tiene cuota por IP).\n\n"
            "Para obtener una clave:\n"
            "1. Crea una cuenta en thegamesdb.net y pide acceso a la API en el "
            "foro (API Requests), describiendo tu aplicación.\n"
            "2. Cuando te lo aprueben, copia la clave de "
            "api.thegamesdb.net/key.php y pégala aquí.",
            self,
        )
        steps.setWordWrap(True)
        layout.addWidget(steps)

        site_button = QPushButton("Abrir thegamesdb.net", self)
        site_button.clicked.connect(self._open_site)
        forum_button = QPushButton("Abrir foro API Requests", self)
        forum_button.clicked.connect(self._open_forum)
        key_button = QPushButton("Abrir api.thegamesdb.net/key.php", self)
        key_button.clicked.connect(self._open_key_page)
        links = QHBoxLayout()
        links.addWidget(site_button)
        links.addWidget(forum_button)
        links.addWidget(key_button)
        layout.addLayout(links)

        layout.addWidget(
            QLabel("Solicitud de ejemplo (cópiala en el foro):", self)
        )
        self._request_text = QPlainTextEdit(
            _FORUM_REQUEST_TEMPLATE.format(
                app_name=scraper.APP_NAME,
                app_version=scraper.APP_VERSION,
            ),
            self,
        )
        self._request_text.setFixedHeight(110)
        self._request_text.setReadOnly(True)
        layout.addWidget(self._request_text)

        copy_button = QPushButton("Copiar solicitud", self)
        copy_button.clicked.connect(self._copy_request)
        layout.addWidget(copy_button)

        self._key_edit = QLineEdit(self)
        config = ScraperConfig.load()
        if config.has_api_key():
            self._key_edit.setText(config.api_key())
        layout.addWidget(QLabel("API Key:", self))
        layout.addWidget(self._key_edit)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("Cancelar", self)
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("Guardar", self)
        save_button.setProperty("btnRole", "success")
        save_button.setDefault(True)
        save_button.clicked.connect(self.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)

    def _open_site(self) -> None:
        QDesktopServices.openUrl(QUrl(scraper.SITE_URL))

    def _open_forum(self) -> None:
        QDesktopServices.openUrl(QUrl(scraper.API_FORUM_URL))

    def _open_key_page(self) -> None:
        QDesktopServices.openUrl(QUrl(scraper.KEY_PAGE_URL))

    def _copy_request(self) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._request_text.toPlainText())

    def accept(self) -> None:
        ScraperConfig().save_api_key(self.api_key())
        super().accept()

    def api_key(self) -> str:
        """Return the (stripped) key currently in the field."""
        return self._key_edit.text().strip()
