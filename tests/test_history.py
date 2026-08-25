"""Unit tests for the journal module (``arkos_companion.history``).

All writes are redirected to a temporary directory; the real project-root
journal (``arkos_history.jsonl``) is never touched by tests.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arkos_companion import history


def _patch_journal_path(tmp: str) -> mock._patch:
    """Redirect ``history.history_file_path`` to a temp dir journal."""
    return mock.patch.object(
        history,
        "history_file_path",
        return_value=os.path.join(tmp, history.HISTORY_FILENAME),
    )


def test_append_load_roundtrip_with_unicode_title():
    """Append and reload an entry whose title uses non-ASCII characters."""
    with tempfile.TemporaryDirectory() as tmp:
        with _patch_journal_path(tmp):
            history.append_entry(
                history.ACTION_EDIT,
                "pgm",
                "dmnfrnt.zip",
                "Demon Front",
                {"title": {"old": "dmnfrnt.zip", "new": "Demon Front"}},
            )
            entries = history.load_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == history.ACTION_EDIT
        assert entry["title"] == "Demon Front"
        assert entry["system"] == "pgm"
        assert entry["rom_file"] == "dmnfrnt.zip"
        assert entry["details"]["title"]["new"] == "Demon Front"
        assert entry["ts"]


def test_load_skips_malformed_lines_without_raising():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, history.HISTORY_FILENAME)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("esto no es json\n")
            fh.write("{'comillas': 'simples'}\n")
            fh.write("{}\n")
            fh.write("\n")
            fh.write(
                json.dumps({"action": "delete", "title": "Mario"}) + "\n"
            )
        with _patch_journal_path(tmp):
            entries = history.load_entries()
        # {} is valid JSON and a dict -> kept; the two malformed lines -> dropped.
        # load_entries preserves file order, so the valid empty object comes first.
        assert len(entries) == 2
        assert entries[1]["title"] == "Mario"


def test_load_missing_file_returns_empty_list():
    with tempfile.TemporaryDirectory() as tmp:
        with _patch_journal_path(tmp):
            entries = history.load_entries()
        assert entries == []


def test_append_never_raises_on_unwritable_location():
    with _patch_journal_path("/no/such/dir/arkos_history.jsonl"):
        # Must swallow the OSError and return, never propagate.
        history.append_entry("delete", "nes", "mario.nes", "Mario", {})