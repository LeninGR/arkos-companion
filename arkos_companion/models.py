"""Data model classes shared by the whole application.

This module is pure Python (no Qt imports) so it can be used and unit-tested
without a display.  It only depends on the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OptimizationStatus(Enum):
    """Compatibility verdict for a ROM inside a given system folder."""

    CORRECT = "correct"
    WARNING = "warning"
    NEEDS_OPTIMIZATION = "needs_optimization"
    UNKNOWN = "unknown"


@dataclass
class GameEntry:
    """A single ROM file (and its attached metadata) found in a system folder."""

    sys_folder: str
    rom_file: str
    rom_base: str
    title: str
    description: Optional[str] = None
    emulator: Optional[str] = None
    core: Optional[str] = None
    image_path: Optional[str] = None
    video_path: Optional[str] = None
    in_gamelist: bool = False
    status: OptimizationStatus = OptimizationStatus.UNKNOWN
    compat: Optional[dict] = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"GameEntry({self.rom_file!r} in {self.sys_folder!r}, "
            f"status={self.status.value})"
        )


@dataclass
class SystemFolder:
    """A scanned game system folder (e.g. ``mame2003``) inside ``roms/``."""

    name: str
    display_name: str
    path: str
    rom_count: int = 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SystemFolder({self.name!r}, {self.rom_count} roms)"


@dataclass
class DeleteResult:
    """Outcome of a destructive delete operation."""

    removed: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    gamelist_updated: bool = False


@dataclass
class OptimizeResult:
    """Outcome of a move/optimize operation."""

    moved: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    target_folder: str = ""
    bios_checked: bool = False