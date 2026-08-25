"""Modal dialog to edit the metadata of the currently selected game."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arkos_companion import scraper
from arkos_companion.models import GameEntry, SystemFolder
from arkos_companion.ui.workers import ScrapeWorker

# Emulators per system folder, following the ArkOS wiki (each console system
# maps to exactly what the R36S image ships).  Arcade and every libretro-only
# system run under RetroArch; only systems with a real standalone emulator
# offer another option.  Fallback (unknown system) is RetroArch only.
_KNOWN_EMULATORS_BY_SYSTEM = {
    "psp": ("retroarch", "PPSSPP"),
    "n64": ("retroarch", "mupen64plus"),
    "nds": ("retroarch", "melonDS"),
}
_DEFAULT_EMULATORS = ("retroarch",)

# Libretro core names the ArkOS image actually installs per system folder.
# Source: ArkOS wiki (christianhaitian/arkos, "ArkOS Emulators and Ports
# information").  The Arcade system runs the FBNeo-family already included
# plus current MAME; the old MAME cores live in their OWN folders
# (mame2003 -> romset 0.78/0.188, mame -> romset 0.139), so they are not
# offered as Arcade options.  Editable combos still let the user type any
# other value; unknown systems start with just the current value.
_KNOWN_CORES_BY_SYSTEM = {
    "arcade": (
        "fbneo_libretro",       # default in ArkOS
        "fbalpha2012_libretro",
        "fbalpha2016_libretro",
        "fbalpha2018_libretro",
        "mame_libretro",        # MAME current (romset 0.266)
    ),
    "fbneo": (
        "fbneo_libretro",
        "fbalpha2012_libretro",
        "fbalpha2016_libretro",
        "fbalpha2018_libretro",
    ),
    "mame2003": (
        "mame2003_plus_libretro",
        "mame2003_libretro",
    ),
    "mame": (
        "mame2010_libretro",
        "mame2003_plus_libretro",
        "mame2003_libretro",
    ),
    "neogeo": (
        "fbneo_libretro",
        "fbalpha2012_libretro",
        "mame2003_plus_libretro",
    ),
    "gb": ("gambatte_libretro", "sameboy_libretro"),
    "gbc": ("gambatte_libretro", "sameboy_libretro"),
    "gba": ("mgba_libretro", "vba_next_libretro"),
    "nes": ("nestopia_libretro", "fceumm_libretro", "mesen_libretro"),
    "snes": ("snes9x_libretro", "snes9x2010_libretro", "bsnes_libretro"),
    "mastersystem": ("genesis_plus_gx_libretro", "picodrive_libretro"),
    "megadrive": ("genesis_plus_gx_libretro", "picodrive_libretro"),
    "genesis": ("genesis_plus_gx_libretro", "picodrive_libretro"),
    "gamegear": ("genesis_plus_gx_libretro", "picodrive_libretro"),
    "psx": ("pcsx_rearmed_libretro",),
    "psp": ("ppsspp_libretro",),
    "n64": ("parallel_n64_libretro", "mupen64plus_next_libretro"),
    "saturn": ("yabasanshiro_libretro", "beetle_saturn_libretro", "kronos_libretro"),
    "dreamcast": ("flycast_libretro",),
    "nds": ("melonds_libretro",),
}


def _populate_combo(combo: QComboBox, options: tuple, current: str) -> None:
    """Fill an editable combo with ``options`` and keep ``current`` selected.

    ``current`` is inserted too (when missing) so the value that is already
    stored on disk always appears as a selectable entry.
    """
    seen = set(options)
    for option in options:
        combo.addItem(option)
    if current and current not in seen:
        combo.addItem(current)
    if current:
        combo.setCurrentText(current)


class EditMetadataDialog(QDialog):
    """Dark-modal form with the editable fields of a ``GameEntry``.

    Emulator and core are editable dropdowns populated with the values ES-DE
    accepts for the game's system folder; the current values are preserved and
    any other value can still be typed.  The dialog disables the parent
    application through modality and is unstyled where the Tokyo Night QSS has
    no matching rule; the app-wide theme (``#1a1b26`` background, ``#c0caf5``
    text) is inherited from ``theme.py``.
    """

    def __init__(
        self,
        entry: GameEntry,
        system: Optional[SystemFolder] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editar metadatos")
        self.setMinimumWidth(480)

        self._entry = entry
        self._system = system
        self._active_workers: List = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self._title_edit = QLineEdit(entry.title or "", self)

        self._emulator_combo = self._make_combo(
            entry.emulator or "",
            _KNOWN_EMULATORS_BY_SYSTEM.get(entry.sys_folder, _DEFAULT_EMULATORS),
        )
        self._core_combo = self._make_combo(
            entry.core or "",
            _KNOWN_CORES_BY_SYSTEM.get(entry.sys_folder, ()),
        )
        self._description_edit = QPlainTextEdit(entry.description or "", self)
        self._description_edit.setFixedHeight(120)

        emulator_row = QWidget(self)
        emulator_row_layout = QHBoxLayout(emulator_row)
        emulator_row_layout.setContentsMargins(0, 0, 0, 0)
        emulator_row_layout.setSpacing(8)
        emulator_row_layout.addWidget(QLabel("Emulador", emulator_row))
        emulator_row_layout.addWidget(self._emulator_combo, 1)
        emulator_row_layout.addWidget(QLabel("Core", emulator_row))
        emulator_row_layout.addWidget(self._core_combo, 1)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Título", self._title_edit)
        form.addRow("Emulador / Core", emulator_row)
        form.addRow("Descripción", self._description_edit)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("Cancelar", self)
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("Guardar", self)
        save_button.setProperty("btnRole", "success")
        save_button.setDefault(True)
        save_button.clicked.connect(self.accept)

        self._scrape_button = QPushButton("Buscar Metadata en Internet", self)
        self._scrape_button.setToolTip(
            "Busca título y descripción en TheGamesDB y descarga la carátula. "
            "Revisa los campos antes de Guardar."
        )
        self._include_video_checkbox = QCheckBox("Descargar vídeo de muestra", self)
        self._include_video_checkbox.setToolTip(
            "Descarga el vídeo promocional del juego desde TheGamesDB como "
            "mp4 local en videos/ (requiere red; los vídeos pueden pesar "
            "varios MB)."
        )
        self._include_video_checkbox.setChecked(False)
        self._scrape_status_label = QLabel(self)
        self._scrape_status_label.setStyleSheet("color: #7dcfff;")
        self._scrape_status_label.setWordWrap(True)
        self._scrape_status_label.hide()
        if system is None:
            self._scrape_button.hide()
            self._include_video_checkbox.hide()
        else:
            self._scrape_button.clicked.connect(self._on_scrape_clicked)

        buttons.addWidget(self._scrape_button)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)
        layout.addWidget(self._include_video_checkbox)
        layout.addWidget(self._scrape_status_label)

    def _on_scrape_clicked(self) -> None:
        """Scrape this game in the background, then pre-fill the fields."""
        if self._system is None:
            return
        if not scraper.effective_api_key():
            self._set_scrape_status(
                "La app aún no tiene la API Key de TheGamesDB: pégala en "
                "DEFAULT_API_KEY dentro de scraper.py."
            )
            return
        self._scrape_button.setEnabled(False)
        self._scrape_button.setText("Buscando…")
        self._set_scrape_status("Buscando en TheGamesDB…")
        worker = ScrapeWorker(
            self._system,
            self._entry,
            include_video=self._include_video_checkbox.isChecked(),
        )
        self._active_workers.append(worker)

        def _done(result: dict) -> None:
            try:
                self._scrape_button.setEnabled(True)
                self._scrape_button.setText("Buscar Metadata en Internet")
                if result.get("status") == "not_found":
                    self._set_scrape_status(
                        "No se encontró metadata para {} en TheGamesDB.".format(
                            self._entry.rom_base
                        )
                    )
                    return
                title = result.get("title")
                description = result.get("description")
                if title:
                    self._title_edit.setText(title)
                if description:
                    self._description_edit.setPlainText(description)
                extra = []
                if result.get("year"):
                    extra.append("Año: " + result["year"])
                if result.get("developer"):
                    extra.append("Desarrollador: " + result["developer"])
                if result.get("image_path"):
                    extra.append("Carátula descargada")
                self._set_scrape_status(" · ".join(extra) or "Metadata aplicada")
            finally:
                self._release_worker(worker)

        def _failed(message: str) -> None:
            try:
                self._scrape_button.setEnabled(True)
                self._scrape_button.setText("Buscar Metadata en Internet")
                self._set_scrape_status(message, error=True)
            finally:
                self._release_worker(worker)

        worker.signals.finished.connect(_done)
        worker.signals.error.connect(_failed)
        QThreadPool.globalInstance().start(worker)

    def _set_scrape_status(self, text: str, error: bool = False) -> None:
        """Show a non-blocking status line under the form."""
        self._scrape_status_label.setText(text)
        self._scrape_status_label.setStyleSheet(
            "color: #f7768e;" if error else "color: #7dcfff;"
        )
        self._scrape_status_label.setVisible(bool(text))

    def _release_worker(self, worker) -> None:
        """Drop the Python reference to a finished worker (avoids GC races)."""
        try:
            self._active_workers.remove(worker)
        except ValueError:
            pass

    def _make_combo(self, current: str, options: tuple) -> QComboBox:
        """Create an editable dropdown: known options + current value + free text."""
        combo = QComboBox(self)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        _populate_combo(combo, options, current)
        return combo

    def values(self) -> dict:
        """Return the (possibly edited) metadata as a plain dict."""
        return {
            "title": self._title_edit.text().strip(),
            "emulator": self._emulator_combo.currentText().strip(),
            "core": self._core_combo.currentText().strip(),
            "description": self._description_edit.toPlainText().strip(),
        }
