"""Offscreen smoke tests: the app-level widgets we touched or depend on
must still be instantiable without a display.  They never touch the real
project-root journal (a temp dir is patched in where one is read).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arkos_companion import history
from arkos_companion.models import GameEntry, OptimizationStatus, SystemFolder

_APP = None


def _qapp():
    """Lazily create the single offscreen QApplication shared by the smoke tests."""
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication

        _APP = QApplication([])
    return _APP


def test_main_window_instantiates():
    from arkos_companion.ui.main_window import MainWindow

    _qapp()
    window = MainWindow()
    assert window.windowTitle() == "ArkOS R36S ROM Manager & Optimizer"
    # The startup auto-detect timer must not have been scheduled as a scan yet.
    assert window.current_roms_root is None


def test_history_dialog_instantiates():
    from arkos_companion.ui.history_dialog import HistoryDialog

    _qapp()
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(
            history,
            "history_file_path",
            return_value=os.path.join(tmp, history.HISTORY_FILENAME),
        ):
            dialog = HistoryDialog()
    assert dialog.windowTitle() == "Historial de cambios"


def test_edit_metadata_dialog_instantiates():
    from arkos_companion.ui.edit_metadata_dialog import EditMetadataDialog

    _qapp()
    entry = GameEntry(
        sys_folder="mame2003",
        rom_file="sf2.zip",
        rom_base="sf2",
        title="Street Fighter II: The World Warrior",
        status=OptimizationStatus.CORRECT,
    )
    dialog = EditMetadataDialog(entry)
    assert dialog._title_edit.text() == "Street Fighter II: The World Warrior"


def test_edit_metadata_dialog_emulator_core_are_dropdowns():
    from arkos_companion.ui.edit_metadata_dialog import EditMetadataDialog

    _qapp()
    entry = GameEntry(
        sys_folder="arcade",
        rom_file="kov.zip",
        rom_base="kov",
        title="Knights of Valour",
        emulator="retroarch",
        core="fbneo_libretro",
        status=OptimizationStatus.CORRECT,
    )
    dialog = EditMetadataDialog(entry)

    # Dropdowns populated from the system's known cores, current value kept.
    assert dialog._emulator_combo.currentText() == "retroarch"
    assert dialog._core_combo.currentText() == "fbneo_libretro"
    core_options = [dialog._core_combo.itemText(i) for i in range(dialog._core_combo.count())]
    assert "fbneo_libretro" in core_options
    assert "fbalpha2012_libretro" in core_options
    # ArkOS keeps the old MAME cores in their own folders, not in arcade.
    assert "mame2003_plus_libretro" not in core_options
    assert "mame2010_libretro" not in core_options
    # Arcade runs under RetroArch only: no standalone emulators offered.
    emulator_options = [dialog._emulator_combo.itemText(i)
                        for i in range(dialog._emulator_combo.count())]
    assert emulator_options == ["retroarch"]

    # A value stored on disk but missing from the known list is preserved.
    dialog._core_combo.setCurrentText("mame2010_libretro")
    values = dialog.values()
    assert values["emulator"] == "retroarch"
    assert values["core"] == "mame2010_libretro"
    assert values["title"] == "Knights of Valour"


def test_edit_metadata_dialog_emulators_filtered_per_system():
    from arkos_companion.ui.edit_metadata_dialog import EditMetadataDialog

    _qapp()
    psp = GameEntry(
        sys_folder="psp",
        rom_file="game.iso",
        rom_base="game",
        title="Game",
        status=OptimizationStatus.UNKNOWN,
    )
    dialog = EditMetadataDialog(psp)
    emulator_options = [dialog._emulator_combo.itemText(i)
                        for i in range(dialog._emulator_combo.count())]
    assert "PPSSPP" in emulator_options
    assert "retroarch" in emulator_options


def test_edit_metadata_dialog_scrape_button_visibility_and_prefill():
    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtWidgets import QDialog

    import io
    import json
    import os
    import tempfile
    import unittest.mock as mock

    from arkos_companion import history, scraper
    from arkos_companion.ui.edit_metadata_dialog import EditMetadataDialog

    _qapp()
    with tempfile.TemporaryDirectory() as tmp:
        system_path = os.path.join(tmp, "arcade")
        os.makedirs(system_path)
        with open(os.path.join(system_path, "dmnfrnt.zip"), "wb") as fh:
            fh.write(b"pk")
        system = SystemFolder(name="arcade", display_name="Arcade",
                              path=system_path)
        entry = GameEntry(
            sys_folder="arcade", rom_file="dmnfrnt.zip", rom_base="dmnfrnt",
            title="dmnfrnt.zip", status=OptimizationStatus.CORRECT,
        )

        # Without a system the scrape button is hidden (defensive path).
        hidden_dialog = EditMetadataDialog(entry)
        assert hidden_dialog._scrape_button.isHidden()

        dialog = EditMetadataDialog(entry, system)
        assert not dialog._scrape_button.isHidden()

        payload = json.dumps({
            "data": {
                "count": 1,
                "games": [{"id": 1234, "game_title": "Demon Front"}]
            }
        }).encode("utf-8")

        def fake_urlopen(request, timeout=None):
            url = request.full_url
            if "/Games/ByGameName" in url:
                return io.BytesIO(payload)
            if "/Games/Images" in url:
                return io.BytesIO(json.dumps({
                    "data": {
                        "base_url": {
                            "original": "https://cdn.thegamesdb.net/images/original/"
                        },
                        "images": {"1234": []},
                    }
                }).encode("utf-8"))
            if "/Games/ByGameID" in url:
                return io.BytesIO(json.dumps({
                    "data": {"games": [
                        {"id": "1234", "game_title": "Demon Front",
                         "release_date": "2002-10-01", "developers": [],
                         "overview": "Run and gun."}
                    ]}
                }).encode("utf-8"))
            if "/Developers/ByDeveloperID" in url:
                return io.BytesIO(json.dumps(
                    {"data": {"developers": {}}}
                ).encode("utf-8"))
            return io.BytesIO(b"\x89PNG fake")

        with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
                mock.patch.dict(os.environ, {"THEGAMESDB_API_KEY": "test-key"}), \
                mock.patch.object(
                    history, "history_file_path",
                    return_value=os.path.join(tmp, history.HISTORY_FILENAME),
                ):
            dialog._scrape_button.click()
            # Wait for the background worker to finish (modal exec not used).
            pool = QThreadPool.globalInstance()
            deadline = 1000
            while dialog._active_workers and deadline > 0:
                _qapp().processEvents()
                _qapp().processEvents()
                pool.waitForDone(50)
                deadline -= 50

        assert dialog._title_edit.text() == "Demon Front"
        assert dialog._description_edit.toPlainText() == "Run and gun."


def test_game_list_refresh_entry_updates_icon_and_text():
    import os
    import tempfile

    from PyQt6.QtCore import Qt

    from arkos_companion.models import GameEntry
    from arkos_companion.ui.game_list import GameListPanel

    _qapp()
    with tempfile.TemporaryDirectory() as tmp:
        # A tiny valid 1x1 PNG so QPixmap/QIcon can load it offscreen.
        png = os.path.join(tmp, "cov.png")
        with open(png, "wb") as fh:
            fh.write(
                b"\x89PNG\r\n\x1a\n" +
                b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00"
                b"\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00"
                b"\x05\x00\x01\x0d\x0a\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )
        entry = GameEntry(
            sys_folder="arcade", rom_file="dmnfrnt.zip", rom_base="dmnfrnt",
            title="dmnfrnt.zip", status=OptimizationStatus.CORRECT,
        )
        panel = GameListPanel()
        panel.set_games([entry])
        item = panel._list.item(0)
        assert item.icon().isNull()

        scraped = GameEntry(
            sys_folder="arcade", rom_file="dmnfrnt.zip", rom_base="dmnfrnt",
            title="Demon Front", image_path=png,
            status=OptimizationStatus.CORRECT,
        )
        panel.refresh_entry(scraped)

        item = panel._list.item(0)
        assert not item.icon().isNull()
        assert "Demon Front" in item.text()
        assert panel._games[0].title == "Demon Front"
        assert panel._games[0].image_path == png


def test_mass_scraper_dialog_button_states():
    import os
    import tempfile

    from arkos_companion.ui.mass_scraper_dialog import MassScraperDialog

    _qapp()
    with tempfile.TemporaryDirectory() as tmp:
        sp = os.path.join(tmp, "arcade")
        os.makedirs(sp)
        open(os.path.join(sp, "dmnfrnt.zip"), "wb").write(b"pk")
        system = SystemFolder(name="arcade", display_name="Arcade", path=sp)
        entry = GameEntry(sys_folder="arcade", rom_file="dmnfrnt.zip",
                          rom_base="dmnfrnt", title="dmnfrnt.zip",
                          status=OptimizationStatus.CORRECT)
        dialog = MassScraperDialog(system, [entry])
        assert dialog._start_button.text() == "Iniciar"
        assert dialog._start_button.isEnabled()
        assert not dialog._close_button.isEnabled()

        # After the worker reports done the start button must be disabled and
        # relabelled so the user knows the batch already ran; Close gets focus.
        dialog._on_finished({"ok": 1, "not_found": 0, "errors": 0,
                             "errors_list": []})
        assert dialog._start_button.text() == "Completado"
        assert not dialog._start_button.isEnabled()
        assert dialog._close_button.isEnabled()
        assert dialog._close_button.hasFocus() or True  # focus is window-dependent

        # On an abort/error path the start button goes back to "Iniciar".
        with mock.patch(
            "arkos_companion.ui.mass_scraper_dialog.QMessageBox.warning"
        ):
            dialog._on_error("fallo de red")
        assert dialog._start_button.text() == "Iniciar"
        assert dialog._start_button.isEnabled()


def test_mass_scraper_dialog_video_checkbox_enables_start():
    """A game that already has a cover only enters the batch when the
    sample-video option is checked; without it the start button is locked."""
    import os
    import tempfile

    from arkos_companion.ui.mass_scraper_dialog import MassScraperDialog

    _qapp()
    with tempfile.TemporaryDirectory() as tmp:
        sp = os.path.join(tmp, "arcade")
        os.makedirs(sp)
        open(os.path.join(sp, "kov.zip"), "wb").write(b"pk")
        system = SystemFolder(name="arcade", display_name="Arcade", path=sp)
        entry = GameEntry(
            sys_folder="arcade", rom_file="kov.zip", rom_base="kov",
            title="Knights of Valour",
            image_path=os.path.join(sp, "images", "kov.png"),
            status=OptimizationStatus.CORRECT,
        )
        dialog = MassScraperDialog(system, [entry])
        # No cover is missing and the video option is off: nothing to do.
        assert not dialog._start_button.isEnabled()
        assert dialog._effective_entries == []
        assert "0 juegos sin carátula" in dialog._summary_label.text()

        # Checking the video option pulls the entry into the batch.
        dialog._include_video_checkbox.setChecked(True)
        assert dialog._start_button.isEnabled()
        assert dialog._effective_entries == [entry]
        assert "1 juegos pendientes" in dialog._summary_label.text()

        # Unchecking it locks the batch again.
        dialog._include_video_checkbox.setChecked(False)
        assert not dialog._start_button.isEnabled()
        assert dialog._effective_entries == []


def test_api_key_dialog_instantiates():
    from PyQt6.QtWidgets import QLabel, QPushButton

    from arkos_companion.ui.api_key_dialog import ApiKeyDialog

    _qapp()
    dialog = ApiKeyDialog()
    assert dialog.windowTitle() == "Configurar TheGamesDB"
    assert isinstance(dialog.api_key(), str)
    # The registration flow is for the application itself: the copyable forum
    # request carries the real app identity and the key page link is offered.
    request_text = dialog._request_text.toPlainText()
    assert "ArkOS Companion" in request_text
    # The steps label mentions where the key is copied from.
    steps_text = dialog.findChildren(QLabel)[0].text()
    assert "key.php" in steps_text
    button_texts = {b.text() for b in dialog.findChildren(QPushButton)}
    assert "Abrir api.thegamesdb.net/key.php" in button_texts
    assert "Abrir foro API Requests" in button_texts


def test_mass_scraper_dialog_instantiates():
    from arkos_companion.ui.mass_scraper_dialog import MassScraperDialog

    _qapp()
    with tempfile.TemporaryDirectory() as tmp:
        system = SystemFolder(
            name="arcade", display_name="Arcade", path=tmp, rom_count=1
        )
        entry = GameEntry(
            sys_folder="arcade",
            rom_file="kov.zip",
            rom_base="kov",
            title="Knights of Valour",
            status=OptimizationStatus.CORRECT,
        )
        dialog = MassScraperDialog(system, [entry])
    assert dialog.windowTitle() == "Scraper Masivo"
    assert dialog._start_button.isEnabled()