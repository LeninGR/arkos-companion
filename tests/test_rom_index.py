"""Tests for the packaged FBNeo offline ROM index (roms_index.tsv).

Hermetic contract: reads only the packaged ``roms_index.tsv`` shipped inside
``arkos_companion/`` (no network), and asserts the index resolves real-world
zip stems to clean game titles, years and manufacturers.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arkos_companion import rom_index


def test_index_file_is_packaged():
    """The app must ship roms_index.tsv next to rom_index.py."""
    assert os.path.isfile(rom_index._index_path())
    entries = rom_index.load_index()
    assert len(entries) > 5000  # FBNeo Arcade-only DAT has ~8.3k sets


def test_lookup_returns_clean_title_year_manufacturer():
    entry = rom_index.lookup("sf2")
    assert entry == {
        "name": "Street Fighter II: The World Warrior (World 910522)",
        "year": "1991",
        "manufacturer": "Capcom",
    }


def test_identify_cleans_region_suffix_and_slash_list():
    identified = rom_index.identify("martmast")
    assert identified["title"] == "Martial Masters"
    assert identified["year"] == "2001"
    assert identified["manufacturer"] == "IGS"


def test_identify_handles_multilingual_slash_titles():
    # "Demon Front / Moyu Zhanxian (...)" keeps only the first title.
    identified = rom_index.identify("dmnfrnt")
    assert identified["title"] == "Demon Front"
    assert identified["year"] == "2002"


def test_identify_unknown_rom_returns_none():
    assert rom_index.identify("zzz_never_a_game_zzz") is None


def test_mame_alias_resolves_legacy_stem():
    # "martial" is a MAME 2003 legacy stem; the alias maps it to martmast.
    identified = rom_index.identify("martial")
    assert identified is not None
    assert identified["title"] == "Martial Masters"
    assert identified.get("aliased") is True


def test_display_title_handles_empty_and_parentheticals():
    assert rom_index.display_title("") == ""
    assert rom_index.display_title("Galaga (Namco rev. B)") == "Galaga"
    assert rom_index.display_title("Pac-Man (Midway)") == "Pac-Man"


def test_hack_aliases_map_to_base_game_name():
    assert rom_index.hack_alias("kogplus") == "The King of Fighters 2002"
    assert rom_index.hack_alias("svcplus") == "SNK vs. Capcom: SVC Chaos"
    assert rom_index.hack_alias("svcsplus") == "SNK vs. Capcom: SVC Chaos"
    assert rom_index.hack_alias("dragonsh") == "Dragon's Heaven"
    assert rom_index.hack_alias("zintrkcd") == "Zintrick / Oshidashi Zentrix"
    assert rom_index.hack_alias("tws96") == "Tecmo World Soccer '96"
    assert rom_index.hack_alias("mosyougi") == "Mahjong IGS"
    assert rom_index.hack_alias("ltorb1") == "Lethal Thunder"
    assert rom_index.hack_alias("zzz_unknown") is None


def test_identify_flags_hack_stems():
    identified = rom_index.identify("svcplus")
    assert identified is not None
    assert identified["title"] == "SNK vs. Capcom: SVC Chaos"
    assert identified.get("hack") is True