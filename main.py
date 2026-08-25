"""ArkOS R36S ROM Manager & Optimizer — application entry point."""

from __future__ import annotations

import sys


def _make_application():
    """Build the QApplication, apply the dark theme and open the main window."""
    from PyQt6.QtWidgets import QApplication

    from arkos_companion.ui.main_window import MainWindow
    from arkos_companion.ui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("ArkOS R36S ROM Manager & Optimizer")
    app.setOrganizationName("arkos-companion")
    apply_theme(app)

    window = MainWindow()
    window.show()
    return app, window


def main() -> int:
    """Run the application and return its exit code."""
    try:
        app, _window = _make_application()
    except ImportError as exc:
        print(
            "No se pudo iniciar el programa: falta PyQt6.\n"
            "Instala las dependencias con: pip install -r requirements.txt",
            file=sys.stderr,
        )
        print(f"Detalle: {exc}", file=sys.stderr)
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())