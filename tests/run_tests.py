"""Tiny assert-based test runner (no pytest dependency).

Discovers ``tests/test_*.py`` modules, runs every ``test_*`` function in each
and reports a pass count.  Exits non-zero when any test fails.

Usage::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/run_tests.py
"""

from __future__ import annotations

import importlib
import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

# UI smoke tests must never try to open a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    sys.path.insert(0, _PROJECT_ROOT)
    sys.path.insert(0, _TESTS_DIR)

    module_names = sorted(
        name[:-3]
        for name in os.listdir(_TESTS_DIR)
        if name.startswith("test_") and name.endswith(".py")
    )
    if not module_names:
        print("No se encontraron módulos de test en tests/")
        return 1

    total = 0
    passed = 0
    failures = []

    for module_name in module_names:
        module = importlib.import_module(module_name)
        test_functions = sorted(
            name
            for name in dir(module)
            if name.startswith("test_") and callable(getattr(module, name))
        )
        for test_name in test_functions:
            total += 1
            try:
                getattr(module, test_name)()
            except Exception:  # noqa: BLE001 - report every failing test
                failures.append(
                    "{}.{}:\n{}".format(
                        module_name, test_name, traceback.format_exc()
                    )
                )
            else:
                passed += 1
                print("PASS {}.{}".format(module_name, test_name))

    print("\nResultado: {}/{} tests pasaron".format(passed, total))
    for failure in failures:
        print("\nFAIL {}".format(failure))

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
