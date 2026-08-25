"""Append-only journal of every modification made to the card.

Every delete, optimize and metadata edit is recorded as one JSON line in
``arkos_history.jsonl`` (project root, next to ``main.py``).  The journal:

  * is append-only -- entries are never rewritten or removed by the app;
  * is human-readable and greppable (one compact JSON object per line);
  * never blocks an operation -- a failing history write is logged to stderr
    and ignored, because the ROM operation itself must always win;
  * is consumed by the HistoryDialog in the UI and by the user directly
    (the file can be opened in any editor).

Entry shape (all values optional except ``action``):
  {
    "ts": "2026-08-05T07:10:00",      # local time, ISO 8601
    "action":  "optimize" | "delete" | "edit_metadata" | "scrape" |
               "media_manual",
    "system":  "mame2003",                 # ArkOS system folder involved
    "rom_file": "dmnfrnt.zip",              # ROM file that was touched
    "title":   "Demon Front",              # human-readable name at the time
    "details": { ... }                     # action-specific structure
  }
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from typing import List

HISTORY_FILENAME = "arkos_history.jsonl"

# Canonical action identifiers (the UI maps them to Spanish labels).
ACTION_OPTIMIZE = "optimize"
ACTION_DELETE = "delete"
ACTION_EDIT = "edit_metadata"
ACTION_SCRAPE = "scrape"
ACTION_MEDIA_MANUAL = "media_manual"


def history_file_path() -> str:
    """Absolute path of the journal (deterministic: project root)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, HISTORY_FILENAME)


def _now() -> str:
    """Local timestamp in ISO 8601 (second precision)."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def append_entry(
    action: str,
    system: str,
    rom_file: str,
    title: str,
    details: dict,
) -> None:
    """Append one journal entry (best effort, never raises).

    ``details`` is action-specific; see the module docstring.  The write is
    flushed immediately so a power cut cannot lose the entry.
    """
    entry = {
        "ts": _now(),
        "action": action,
        "system": system,
        "rom_file": rom_file,
        "title": title,
        "details": details or {},
    }
    try:
        with open(history_file_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
    except OSError as exc:
        # The journal must never break the operation that triggered it.
        print(
            "[historial] no se pudo escribir el archivo de historial: {}".format(exc),
            file=sys.stderr,
        )


def load_entries() -> List[dict]:
    """Read all journal entries (oldest first), skipping malformed lines."""
    try:
        with open(history_file_path(), "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []

    entries: List[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # corrupted line: skip, never crash the viewer
        if isinstance(entry, dict):
            entries.append(entry)
    return entries
