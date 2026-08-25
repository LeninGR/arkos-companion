"""Offline index of full arcade game titles, built from the official FBNeo DAT.

The archive ships with ``roms_index.tsv`` (generated once from the official
``FinalBurn Neo (ClrMame Pro XML, Arcade only)`` DAT), mapping each ROM zip
stem to its real full name, release year and manufacturer.  This lets the
scraper search TheGamesDB with a human title ("Martial Masters") instead of
the raw zip code ("martmast"), matching the first result on the first try
without spending API quota on blind queries.

Pure Python (no Qt, no third-party deps), lazy-loads the index on first use,
and never raises: a missing or corrupt file simply degrades to an empty
lookup, keeping the old behaviour.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

ROM_INDEX_FILENAME = "roms_index.tsv"
_INDEX: Optional[Dict[str, dict]] = None

# Small safety net for MAME 2003 sets.  The FBNeo DAT covers the vast
# majority of arcade roms on the R36S, but some very old/retro MAME 2003
# sets carry zip stems that FBNeo addresses under a different name.  This
# table maps those legacy stems to their canonical FBNeo identifier BEFORE
# falling back to the raw stem search.
MAME_CLASSICS_ALIASES: Dict[str, str] = {
    "jojo": "jojo",          # JoJo's Bizarre Adventure
    "jojoba": "jojobanr",     # JoJo no Kimyou na Bouken (set ban)
    "ddonpachj": "ddonpchj",  # DoDonPachi (Japan)
    "galaxyfg": "galaxyfg",   # Galaxy Fight
    "wjammers": "wjammers",   # Windjammers
    "martial": "martmast",    # Martial Masters (MAME 2003 stem variant)
    "ketsui": "ketsui",       # Ketsui: Kizuna Jigoku Tachi
    "esprade": "esprade",     # ESP Ra.De.
    "ddf": "ddp2",            # DoDonPachi II
    "spaceinv": "invader4",   # Space Invaders (classic cabinet naming)
}

# Direct search-name overrides for bootlegs / fan hacks that are NOT part of
# the official FBNeo DAT.  These stems would otherwise never resolve (the
# hack title is not on TheGamesDB), so we point them at the official base
# game name: the user still gets a real cover + description for the base
# game.  Keyed by zip stem (lowercase).
#
# NOTE: values were verified against the live TheGamesDB API on 2026-08-06;
# several strict names need apostrophe-free or exact-match phrasing.
HACK_ALIASES: Dict[str, str] = {
    # KOF 2002 "Plus" bootleg/hack -> base game.  A leading apostrophe
    # ("'2002") misleads the API's fuzzy search, so we use the plain year.
    "kogplus": "The King of Fighters 2002",
    # SNK vs. Capcom: SVC Chaos "Plus" bootlegs (Chinese) -> base game.
    "svcplus": "SNK vs. Capcom: SVC Chaos",
    "svcsplus": "SNK vs. Capcom: SVC Chaos",
    # Dragon's Heaven (dated naming).  The GamesDB has no exact "Dragon's
    # Heaven" entry; the apostrophe is dropped and a fuzzy match is used.
    "dragonsh": "Dragon's Heaven",
    # Zintrick: the DB title carries the full slash "Oshidashi Zentrix" --
    # an exact-case query inside that full title resolves cleanly.
    "zintrkcd": "Zintrick / Oshidashi Zentrix",
    # Tecmo World Soccer '96.
    "tws96": "Tecmo World Soccer '96",
    # IGS Mahjong.  The GamesDB does not list "Sankyo Honkaku Mahjong", so
    # we fall back to a generic tile-game search the API does understand.
    "mosyougi": "Mahjong IGS",
    # Lethal Thunder (Irem, 1991).
    "ltorb1": "Lethal Thunder",
}


def hack_alias(rom_base: str) -> Optional[str]:
    """Search-name override for an unofficial/hack rom (``None`` when unknown)."""
    if not rom_base:
        return None
    return HACK_ALIASES.get(rom_base.strip().lower())


def _index_path() -> str:
    """Absolute path of the packaged index (sibling of this module)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ROM_INDEX_FILENAME)


def load_index() -> Dict[str, dict]:
    """Load (and cache) the packed ROM index; never raises.

    Each entry is ``{"name", "year", "manufacturer"}`` keyed by the
    lowercased zip stem.  The file is parsed lazily on first access so the
    application start-up cost stays at zero until the scraper needs it.
    """
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    index: Dict[str, dict] = {}
    path = _index_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            header = next(fh, None)  # skip the header line
            if header is None:
                return index
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                rom, name = parts[0].strip().lower(), parts[1].strip()
                if not rom or not name:
                    continue
                index[rom] = {
                    "name": name,
                    "year": parts[2].strip() if len(parts) > 2 else "",
                    "manufacturer": parts[3].strip() if len(parts) > 3 else "",
                }
    except OSError:
        pass
    _INDEX = index
    return index


def lookup(rom_base: str) -> Optional[dict]:
    """Return the FBNeo index entry for a zip stem, or ``None``.

    Returns a defensive copy so callers can never mutate the cached index.
    """
    if not rom_base:
        return None
    entry = load_index().get(rom_base.strip().lower())
    if entry is None:
        return None
    return dict(entry)


def resolve_alias(rom_base: str) -> str:
    """Resolve a legacy MAME 2003 zip stem to its canonical FBNeo stem.

    Returns the input stem unchanged when no alias exists (the raw stem is
    then legitimately used for a final plain-name search).
    """
    if not rom_base:
        return rom_base
    return MAME_CLASSICS_ALIASES.get(rom_base.strip().lower(), rom_base)


def display_title(index_name: str) -> str:
    """Turn a raw DAT description into a clean query-friendly title.

    FBNeo DAT descriptions carry region/version markers in parentheses and
    multi-language slash lists, e.g.::

      "Street Fighter II: The World Warrior (World 910522)"
      "Martial Masters / Xing Yi Quan (ver. 104, 102, 102US)"
      "Demon Front / Moyu Zhanxian (68k label V105, ...)"

    Only the first (primary) name is kept and parenthetical markers are
    removed so the result is a normal title TheGamesDB matches directly.
    """
    if not index_name:
        return index_name
    primary = index_name.split(" /", 1)[0]  # keep the first localized title
    primary = re.sub(r"\s*\([^)]*\)", "", primary).strip()
    return primary or index_name


def identify(rom_base: str) -> Optional[dict]:
    """Best offline identification for a zip stem (``None`` when unknown).

    Resolution order: explicit hack/bootleg overrides (they point the
    illegally-named stem at the official base game), then the packaged FBNeo
    index, then the small MAME 2003 alias table.  The returned dict carries
    ``{"name", "year", "manufacturer"}`` with a cleaned ``title`` ready to
    search (``None`` keys allowed).
    """
    # Hack/bootleg overrides map to the official base-game name; they must
    # win over the DAT because bootleg DAT titles (e.g. "SVC Chaos Plus")
    # are not what TheGamesDB indexes.
    hack = hack_alias(rom_base)
    if hack:
        return {
            "name": hack,
            "title": hack,
            "year": "",
            "manufacturer": "",
            "hack": True,
        }
    entry = lookup(rom_base)
    if entry is not None:
        return {
            "name": entry["name"],
            "title": display_title(entry["name"]),
            "year": entry["year"],
            "manufacturer": entry["manufacturer"],
        }
    aliased = resolve_alias(rom_base)
    if aliased and aliased != rom_base:
        entry = lookup(aliased)
        if entry is not None:
            return {
                "name": entry["name"],
                "title": display_title(entry["name"]),
                "year": entry["year"],
                "manufacturer": entry["manufacturer"],
                "aliased": True,
            }
    return None