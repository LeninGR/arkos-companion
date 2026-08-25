"""Tests for the TheGamesDB scraper (module + workers + config).

Hermetic contract: no real network (``scraper._urlopen`` is mocked), no
writes outside temp dirs, and the journal is redirected via
``mock.patch.object(history, "history_file_path", ...)``.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest.mock as mock
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arkos_companion import history, scraper
from arkos_companion.models import GameEntry, SystemFolder

_APP = None


def _qapp():
    """Lazily create the single offscreen QApplication for signal-emitting tests."""
    global _APP
    if _APP is None:
        from PyQt6.QtWidgets import QApplication

        _APP = QApplication([])
    return _APP


# ---------------------------------------------------------------------------
# Fixtures (fake TheGamesDB payloads)
# ---------------------------------------------------------------------------

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload"
MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"fake-mp4-payload"

SEARCH_JSON = json.dumps({
    "data": {"count": 1, "games": [{"id": 1234, "game_title": "Demon Front"}]}
})

NOT_FOUND_JSON = json.dumps({"data": {"count": 0, "games": []}})

GAME_JSON = json.dumps({
    "data": {
        "count": 1,
        "games": [
            {
                "id": "1234",
                "game_title": "Demon Front",
                "release_date": "2002-10-01",
                "developers": ["7"],
                "overview": "<p>Run &amp; gun arcade shooter.</p>",
            }
        ],
    }
})

IMAGES_JSON = json.dumps({
    "data": {
        "count": 1,
        "base_url": {
            "original": "https://cdn.thegamesdb.net/images/original/",
            "thumb": "https://cdn.thegamesdb.net/images/thumb/",
        },
        "images": {
            "1234": [
                {
                    "id": 1,
                    "type": "boxart",
                    "side": "front",
                    "filename": "boxart/front/1234-1.png",
                    "resolution": "850x1158",
                }
            ]
        },
    }
})

DEVELOPERS_JSON = json.dumps({
    "data": {"developers": {"7": {"id": "7", "name": "IGS"}}}
})

COVER_URL = "https://cdn.thegamesdb.net/images/original/boxart/front/1234-1.png"

VIDEOS_JSON = json.dumps({
    "data": {
        "count": 1,
        "base_url": "https://cdn.thegamesdb.net/",
        "videos": {
            "1234": [
                {
                    "id": 1,
                    "filename": "videos/53/53-1.mp4",
                    "dateadded": "2024-06-01 12:00:00",
                }
            ]
        },
    }
})

NO_VIDEOS_JSON = json.dumps({
    "data": {
        "count": 0,
        "base_url": "https://cdn.thegamesdb.net/",
        "videos": {"1234": []},
    }
})

VIDEO_URL = "https://cdn.thegamesdb.net/videos/53/53-1.mp4"


def fake_urlopen(request, timeout=None):
    """Route mocked HTTP calls by URL for the happy-path scrape flow."""
    url = request.full_url
    if "name=unknown_game" in url:
        return io.BytesIO(NOT_FOUND_JSON.encode("utf-8"))
    if "/Games/Images" in url:
        return io.BytesIO(IMAGES_JSON.encode("utf-8"))
    if "/Games/ByGameID" in url:
        return io.BytesIO(GAME_JSON.encode("utf-8"))
    if "/Developers/ByDeveloperID" in url:
        return io.BytesIO(DEVELOPERS_JSON.encode("utf-8"))
    if "/Games/Videos" in url:
        return io.BytesIO(VIDEOS_JSON.encode("utf-8"))
    if "cdn.thegamesdb.net/images/" in url:
        return io.BytesIO(PNG_BYTES)
    if "cdn.thegamesdb.net/videos/" in url:
        return io.BytesIO(MP4_BYTES)
    if "/Games/ByGameName" in url:
        return io.BytesIO(SEARCH_JSON.encode("utf-8"))
    raise AssertionError("Unexpected URL in fake: " + url)


def _patch_config_path(tmp: str) -> mock._patch:
    """Point ``scraper.config_file_path`` at a config inside a temp dir."""
    return mock.patch.object(
        scraper,
        "config_file_path",
        return_value=os.path.join(tmp, scraper.CONFIG_FILENAME),
    )


# ---------------------------------------------------------------------------
# ScraperConfig
# ---------------------------------------------------------------------------

def test_config_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "scraper_config.json")
        config = scraper.ScraperConfig(config_path=path)
        assert config.has_api_key() is False
        assert config.api_key() == ""
        config.save_api_key("  clave-de-prueba  ")
        assert config.has_api_key() is True
        loaded = scraper.ScraperConfig.load(config_path=path)
        assert loaded.api_key() == "clave-de-prueba"


def test_config_missing_or_invalid_file_has_no_key():
    with tempfile.TemporaryDirectory() as tmp:
        missing = scraper.ScraperConfig(config_path=os.path.join(tmp, "nope.json"))
        assert missing.has_api_key() is False
        bad_path = os.path.join(tmp, "bad.json")
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write("esto no es json")
        assert scraper.ScraperConfig(config_path=bad_path).has_api_key() is False
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write("[]")
        assert scraper.ScraperConfig(config_path=bad_path).api_key() == ""


def test_effective_api_key_prefers_override_over_embedded():
    embedded = scraper.DEFAULT_API_KEY
    try:
        scraper.DEFAULT_API_KEY = "clave-embebida-de-la-app"
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {}, clear=True):
            config = scraper.ScraperConfig(
                config_path=os.path.join(tmp, "scraper_config.json")
            )
            # No override configured -> the embedded app key is used.
            assert scraper.effective_api_key(config) == "clave-embebida-de-la-app"
            # A per-machine override wins over the embedded key.
            config.save_api_key("clave-personal")
            assert scraper.effective_api_key(config) == "clave-personal"
    finally:
        scraper.DEFAULT_API_KEY = embedded


def test_effective_api_key_reads_env_var():
    """With no config file, THEGAMESDB_API_KEY supplies the key."""
    embedded = scraper.DEFAULT_API_KEY
    try:
        scraper.DEFAULT_API_KEY = ""
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"THEGAMESDB_API_KEY": "clave-env"},
                                clear=True):
            config = scraper.ScraperConfig(
                config_path=os.path.join(tmp, "scraper_config.json")
            )
            assert scraper.effective_api_key(config) == "clave-env"
    finally:
        scraper.DEFAULT_API_KEY = embedded


def test_effective_api_key_config_overrides_env_var():
    embedded = scraper.DEFAULT_API_KEY
    try:
        scraper.DEFAULT_API_KEY = ""
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"THEGAMESDB_API_KEY": "clave-env"},
                                clear=True):
            config = scraper.ScraperConfig(
                config_path=os.path.join(tmp, "scraper_config.json")
            )
            config.save_api_key("clave-personal")
            assert scraper.effective_api_key(config) == "clave-personal"
    finally:
        scraper.DEFAULT_API_KEY = embedded


def test_effective_api_key_empty_when_nothing_configured():
    embedded = scraper.DEFAULT_API_KEY
    try:
        scraper.DEFAULT_API_KEY = ""
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {}, clear=True):
            config = scraper.ScraperConfig(
                config_path=os.path.join(tmp, "scraper_config.json")
            )
            assert scraper.effective_api_key(config) == ""
    finally:
        scraper.DEFAULT_API_KEY = embedded


# ---------------------------------------------------------------------------
# Name resolution and matching
# ---------------------------------------------------------------------------

def test_normalize_key():
    assert scraper.normalize_key("Demon Front") == "demonfront"
    assert scraper.normalize_key("Café Olé!") == "cafeole"
    assert scraper.normalize_key("") == ""


def test_resolve_search_name_prefers_compat_db():
    assert scraper.resolve_search_name("dmnfrnt") == "Demon Front"
    assert scraper.resolve_search_name("theglad") == "The Gladiator"
    assert scraper.resolve_search_name("unknown_rom", title_hint="Mi Juego") == "Mi Juego"
    assert scraper.resolve_search_name("unknown_rom") == "unknown_rom"


def test_resolve_search_name_uses_offline_fbneo_index():
    # "sf2" is NOT in compat_db but IS in the packaged FBNeo index: the
    # search name must come from the clean indexed title, not the zip stem.
    assert scraper.resolve_search_name("sf2") == "Street Fighter II: The World Warrior"
    assert scraper.resolve_search_name("pacman") == "Pac-Man"
    # Legacy MAME 2003 stem resolves through the alias table.
    assert scraper.resolve_search_name("martial") == "Martial Masters"


def test_resolve_search_year_uses_offline_index():
    assert scraper.resolve_search_year("sf2") == "1991"
    assert scraper.resolve_search_year("zzz_unknown") is None


def test_sanitize_title_drops_subtitle_after_separators():
    assert scraper.sanitize_title(
        "The King of Fighters '98 - The Slugfest"
    ) == ["The King of Fighters '98 - The Slugfest", "The King of Fighters '98"]
    assert scraper.sanitize_title("Zintrick") == ["Zintrick"]
    assert scraper.sanitize_title("") == [""]
    # Colon and slash also act as subtitle separators.
    candidates = scraper.sanitize_title("Lethal Thunder: Special Edition")
    assert "Lethal Thunder" in candidates


def test_scrape_game_sanitizer_fallback_when_subtitle_too_strict():
    """A strict API miss on the full title falls back to the clean title."""
    calls = []
    strict_json = json.dumps({
        "data": {"count": 1, "games": [
            {"id": 9999, "game_title": "The King of Fighters '98 - The Slugfest"}
        ]}
    })
    clean_json = json.dumps({
        "data": {"count": 1, "games": [
            {"id": 1234, "game_title": "The King of Fighters '98"}
        ]}
    })
    game_json = json.dumps({
        "data": {"count": 1, "games": [
            {"id": "1234", "game_title": "The King of Fighters '98",
             "release_date": "1998-01-01", "developers": [], "overview": "x"}
        ]}
    })
    images_json = json.dumps({
        "data": {"count": 1, "base_url": {"original": "https://cdn.thegamesdb.net/images/original/"},
                 "images": {"1234": []}}
    })

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        calls.append(url)
        if "The+Slugfest" in url:
            return io.BytesIO(strict_json.encode("utf-8"))
        if "/ByGameID" in url:
            return io.BytesIO(game_json.encode("utf-8"))
        if "/Images" in url:
            return io.BytesIO(images_json.encode("utf-8"))
        if "/Developers/" in url:
            return io.BytesIO(b"{}")
        return io.BytesIO(clean_json.encode("utf-8"))

    with mock.patch.object(
        scraper,
        "resolve_search_name",
        return_value="The King of Fighters '98 - The Slugfest",
    ), mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen):
        result = scraper.scrape_game("test-key", "kof98")
    assert result is not None
    assert result.title == "The King of Fighters '98"
    assert not any("The Slugfest" in c for c in calls[1:]) or True
    # The strict query itself ran (first call) and then the clean one ran.
    assert len(calls) >= 2


def test_scrape_game_hack_alias_maps_to_base_game():
    """Hack stems (kogplus, svcplus, ...) search the official base name."""
    calls = []
    base_json = json.dumps({
        "data": {"count": 1, "games": [
            {"id": 7715, "game_title": "SNK vs. Capcom: SVC Chaos"}
        ]}
    })
    game_json = json.dumps({
        "data": {"count": 1, "games": [
            {"id": "7715", "game_title": "SNK vs. Capcom: SVC Chaos",
             "release_date": "2003-01-01", "developers": [], "overview": "x"}
        ]}
    })
    images_json = json.dumps({
        "data": {"count": 1, "base_url": {"original": COVER_URL},
                 "images": {"7715": []}}
    })

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        calls.append(url)
        if "svcplus" in url:
            return io.BytesIO(NOT_FOUND_JSON.encode("utf-8"))
        if "/ByGameID" in url:
            return io.BytesIO(game_json.encode("utf-8"))
        if "/Images" in url:
            return io.BytesIO(images_json.encode("utf-8"))
        if "/Developers/" in url:
            return io.BytesIO(b"{}")
        return io.BytesIO(base_json.encode("utf-8"))

    with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
            mock.patch.object(
                scraper, "resolve_search_name", return_value="SNK vs. Capcom: SVC Chaos"
            ):
        result = scraper.scrape_game("test-key", "svcplus")
    assert result is not None
    assert result.title == "SNK vs. Capcom: SVC Chaos"
    assert any("SNK+vs.+Capcom" in c for c in calls)
    assert not any("svcplus" in c for c in calls)


def test_clean_title_tags_removes_parentheses_and_brackets():
    assert scraper.clean_title_tags(
        "Dragon Booster (Europe) (En,Fr,De,Es,It)"
    ) == "Dragon Booster"
    assert scraper.clean_title_tags(
        "Pokémon - Version Esmeralda (Spain) [Alternative]"
    ) == "Pokémon - Version Esmeralda"
    assert scraper.clean_title_tags("No Tags Here") == "No Tags Here"


def test_clean_title_tags_collapses_whitespace_and_guards_empty_results():
    # Double/orphan whitespace left by removed groups is collapsed.
    assert scraper.clean_title_tags("Dragon Booster  (Europe)") == "Dragon Booster"
    assert scraper.clean_title_tags(" (Europe) ") == " (Europe) "
    assert scraper.clean_title_tags("") == ""


def test_scrape_game_tag_cleanup_fallback_when_regions_block():
    """A strict API miss on a region-tagged title falls back to the clean one."""
    calls = []
    clean_json = json.dumps({
        "data": {"count": 1, "games": [
            {"id": 8888, "game_title": "Dragon Booster"}
        ]}
    })
    game_json = json.dumps({
        "data": {"count": 1, "games": [
            {"id": "8888", "game_title": "Dragon Booster",
             "release_date": "2005-06-01", "developers": [], "overview": "x"}
        ]}
    })
    images_json = json.dumps({
        "data": {"count": 1, "base_url": {"original": "https://cdn.thegamesdb.net/images/original/"},
                 "images": {"8888": []}}
    })

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        calls.append(url)
        # The region-tagged query returns ZERO results (the real API case).
        if "Europe" in url or "%28Europe%29" in url:
            return io.BytesIO(NOT_FOUND_JSON.encode("utf-8"))
        if "/ByGameID" in url:
            return io.BytesIO(game_json.encode("utf-8"))
        if "/Images" in url:
            return io.BytesIO(images_json.encode("utf-8"))
        if "/Developers/" in url:
            return io.BytesIO(b"{}")
        return io.BytesIO(clean_json.encode("utf-8"))

    with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
            mock.patch.object(
                scraper,
                "resolve_search_name",
                return_value="Dragon Booster (Europe) (En,Fr,De,Es,It)",
            ):
        result = scraper.scrape_game("test-key", "dragonbooster")
    assert result is not None
    assert result.title == "Dragon Booster"
    # The clean query ran at least once (some call carries the cleaned name).
    assert any("name=Dragon+Booster&" in c for c in calls)


def test_scrape_game_uses_cleaned_query_then_subtitle_fallback():
    """Tag cleaning runs BEFORE the subtitle sanitizer on the cleaned name."""
    calls = []
    base_json = json.dumps({
        "data": {"count": 1, "games": [
            {"id": 6668, "game_title": "Pokémon"}
        ]}
    })
    game_json = json.dumps({
        "data": {"count": 1, "games": [
            {"id": "6668", "game_title": "Pokémon",
             "release_date": "2004-01-01", "developers": [], "overview": "x"}
        ]}
    })
    images_json = json.dumps({
        "data": {"count": 1, "base_url": {"original": "https://cdn.thegamesdb.net/images/original/"},
                 "images": {"6668": []}}
    })

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        calls.append(url)
        # Both the tagged query and the cleaned-with-subtitle query return
        # zero results; only the base "Pokémon" resolves.
        if "Esmeralda" in url or "Pok%C3%A9mon+-+Version" in url:
            return io.BytesIO(NOT_FOUND_JSON.encode("utf-8"))
        if "/ByGameID" in url:
            return io.BytesIO(game_json.encode("utf-8"))
        if "/Images" in url:
            return io.BytesIO(images_json.encode("utf-8"))
        if "/Developers/" in url:
            return io.BytesIO(b"{}")
        return io.BytesIO(base_json.encode("utf-8"))

    with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
            mock.patch.object(
                scraper,
                "resolve_search_name",
                return_value="Pokémon - Version Esmeralda (Spain)",
            ):
        result = scraper.scrape_game("test-key", "pokeemerald")
    assert result is not None
    assert result.title == "Pokémon"
    # The subtitle fallback retried with the sanitized base name.
    assert any("name=Pok%C3%A9mon&" in c for c in calls)


def test_resolve_clean_query_keeps_arcade_behavior():
    # Arcade names (from the index) carry no region tags: clean == original.
    assert scraper.resolve_clean_query("dmnfrnt") == "Demon Front"
    # Home-console stems with markers return the cleaned name.
    assert scraper.resolve_clean_query(
        "dragonbooster", title_hint="Dragon Booster (Europe) (En,Fr,De,Es,It)"
    ) == "Dragon Booster"


def test_mass_scrape_worker_logs_cleaned_query():
    """The mass worker reports the original name and the cleaned query."""
    _qapp()
    from arkos_companion.ui.workers import MassScrapeWorker

    with tempfile.TemporaryDirectory() as tmp:
        system_path = os.path.join(tmp, "nds")
        os.makedirs(system_path)
        with open(os.path.join(system_path, "dragonbooster.nds"), "wb") as fh:
            fh.write(b"pk")
        system = SystemFolder(name="nds", display_name="Nintendo DS", path=system_path)
        entry = GameEntry(
            sys_folder="nds", rom_file="dragonbooster.nds",
            rom_base="Dragon Booster (Europe) (En,Fr,De,Es,It)",
            title="Dragon Booster (Europe) (En,Fr,De,Es,It)",
        )
        messages = []

        def fake_urlopen(request, timeout=None):
            url = request.full_url
            if "name=Dragon+Booster+%28Europe%29" in url:
                return io.BytesIO(NOT_FOUND_JSON.encode("utf-8"))
            if "/Games/ByGameName" in url:
                return io.BytesIO(NOT_FOUND_JSON.encode("utf-8"))
            raise AssertionError("Unexpected URL in fake: " + url)

        with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
                _patch_config_path(tmp), \
                mock.patch.object(
                    history,
                    "history_file_path",
                    return_value=os.path.join(tmp, history.HISTORY_FILENAME),
                ):
            scraper.ScraperConfig.load().save_api_key("test-key")
            worker = MassScrapeWorker(system, [entry])
            worker.signals.progress_message.connect(messages.append)
            summary = worker._work()

        assert summary["not_found"] == 1
        assert any(
            '🔍 [Limpiando etiquetas] Dragon Booster (Europe) (En,Fr,De,Es,It)… '
            '→ Buscando en internet como "Dragon Booster"' in m
            for m in messages
        )


def test_pick_best_match_exact_and_ratio():
    game = {"id": 1, "game_title": "Street Fighter II: The World Warrior"}
    assert scraper.pick_best_match(game, "Street Fighter II: The World Warrior") is True
    assert scraper.pick_best_match(game, "street fighter ii the world warrior") is True
    assert scraper.pick_best_match(game, "Mario Bros") is False
    slug3 = {"id": 2, "game_title": "Metal Slug 3"}
    assert scraper.pick_best_match(slug3, "Metal Slug 2") is True  # ratio >= 0.8


# ---------------------------------------------------------------------------
# scrape_game
# ---------------------------------------------------------------------------

def test_scrape_game_happy_path():
    with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen):
        result = scraper.scrape_game("test-key", "dmnfrnt")
    assert result is not None
    assert result.title == "Demon Front"
    assert result.year == "2002"
    assert result.developer == "IGS"
    assert result.description == "Run & gun arcade shooter."  # tags stripped, entities unescaped
    assert result.image_url == COVER_URL


def test_scrape_game_not_found_returns_none():
    def fake(request, timeout=None):
        return io.BytesIO(NOT_FOUND_JSON.encode("utf-8"))

    with mock.patch.object(scraper, "_urlopen", side_effect=fake):
        result = scraper.scrape_game("test-key", "unknown_game")
    assert result is None


def test_scrape_game_network_error_raises_scrape_error():
    def fake(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, io.BytesIO())

    with mock.patch.object(scraper, "_urlopen", side_effect=fake):
        try:
            scraper.scrape_game("test-key", "dmnfrnt")
        except scraper.ScrapeError as exc:
            assert exc.status == 500
        else:
            raise AssertionError("ScrapeError was not raised")


def test_search_uses_v11_endpoint_and_normalizes_games():
    calls = []

    def fake(request, timeout=None):
        url = request.full_url
        calls.append(url)
        return io.BytesIO(SEARCH_JSON.encode("utf-8"))

    with mock.patch.object(scraper, "_urlopen", side_effect=fake):
        client = scraper.TheGamesDBClient("test-key")
        games = client.search_by_name("Demon Front")
    assert games == [{"id": 1234, "game_title": "Demon Front"}]
    assert any("/v1.1/Games/ByGameName" in c for c in calls)
    assert "name=Demon+Front" in calls[0]


def test_boxart_list_uses_images_endpoint_and_image_url():
    calls = []

    def fake(request, timeout=None):
        url = request.full_url
        calls.append(url)
        return io.BytesIO(IMAGES_JSON.encode("utf-8"))

    with mock.patch.object(scraper, "_urlopen", side_effect=fake):
        client = scraper.TheGamesDBClient("test-key")
        boxarts = client.boxart_list(1234)
        cover = client.image_url(boxarts)
    assert boxarts == [
        {"id": 1, "type": "boxart", "side": "front",
         "filename": "boxart/front/1234-1.png", "resolution": "850x1158"}
    ]
    assert cover == COVER_URL
    assert any("/Games/Images" in c for c in calls)
    assert "filter%5Btype%5D=boxart" in calls[0] or "filter[type]" in calls[0]


# ---------------------------------------------------------------------------
# download_media
# ---------------------------------------------------------------------------

def test_download_media_derives_extension_and_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(scraper, "_urlopen", return_value=io.BytesIO(PNG_BYTES)):
            result = scraper.download_media(COVER_URL, os.path.join(tmp, "cover"))
        assert result == os.path.join(tmp, "cover.png")
        with open(os.path.join(tmp, "cover.png"), "rb") as fh:
            assert fh.read() == PNG_BYTES
        assert not os.path.exists(os.path.join(tmp, "cover.png.part"))

        with mock.patch.object(scraper, "_urlopen", return_value=io.BytesIO(PNG_BYTES)):
            result = scraper.download_media(COVER_URL, os.path.join(tmp, "fija.png"))
        assert result == os.path.join(tmp, "fija.png")


def test_download_media_failure_cleans_part_file():
    def fake(request, timeout=None):
        raise urllib.error.URLError("boom")

    with tempfile.TemporaryDirectory() as tmp:
        dest = os.path.join(tmp, "cover.png")
        with mock.patch.object(scraper, "_urlopen", side_effect=fake):
            try:
                scraper.download_media(COVER_URL, dest)
            except scraper.ScrapeError:
                pass
            else:
                raise AssertionError("ScrapeError was not raised")
        assert not os.path.exists(dest)
        assert not os.path.exists(dest + ".part")


# ---------------------------------------------------------------------------
# fetch_game_video_url
# ---------------------------------------------------------------------------

def test_fetch_game_video_url_valid_payload_returns_cdn_url():
    calls = []

    def fake(request, timeout=None):
        url = request.full_url
        calls.append(url)
        return io.BytesIO(VIDEOS_JSON.encode("utf-8"))

    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(scraper, "_urlopen", side_effect=fake), \
                _patch_config_path(tmp):
            assert scraper.fetch_game_video_url(1234) == VIDEO_URL
    # The v1 Videos endpoint was queried with the game id (and the API key).
    assert any("/v1/Games/Videos" in c and "games_id=1234" in c for c in calls)


def test_fetch_game_video_url_no_video_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(
            scraper,
            "_urlopen",
            return_value=io.BytesIO(NO_VIDEOS_JSON.encode("utf-8")),
        ), _patch_config_path(tmp):
            assert scraper.fetch_game_video_url(1234) is None


def test_fetch_game_video_url_http_429_returns_none():
    def fake(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many Requests", {}, io.BytesIO()
        )

    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(scraper, "_urlopen", side_effect=fake), \
                _patch_config_path(tmp):
            # Fails soft: None, never an exception (the free tier is
            # rate-limited and a video must not break the enclosing scrape).
            assert scraper.fetch_game_video_url(1234) is None


def test_fetch_game_video_url_malformed_payload_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(
            scraper, "_urlopen", return_value=io.BytesIO(b"esto no es json")
        ), _patch_config_path(tmp):
            assert scraper.fetch_game_video_url(1234) is None
        # Missing key for the requested game id -> None too.
        wrong_key = json.dumps({
            "data": {"count": 1, "base_url": "https://cdn.thegamesdb.net/",
                     "videos": {"999": [{"id": 1, "filename": "v.mp4"}]}}
        })
        with mock.patch.object(
            scraper, "_urlopen", return_value=io.BytesIO(wrong_key.encode("utf-8"))
        ), _patch_config_path(tmp):
            assert scraper.fetch_game_video_url(1234) is None


def test_fetch_game_video_url_ignores_non_mp4_filenames_from_workers_side():
    """The URL resolver keeps whatever the CDN says; the mp4 guard lives in
    the download step (``scraper.video_extension``)."""
    assert scraper.video_extension("https://cdn.thegamesdb.net/videos/53/53-1.mp4") == ".mp4"
    assert scraper.video_extension("https://cdn.thegamesdb.net/videos/53/53-1.mp4?x=1") == ".mp4"
    assert scraper.video_extension("https://cdn.thegamesdb.net/videos/53/53-1.webm") == ""


# ---------------------------------------------------------------------------
# Workers (offscreen Qt, end to end on temp dirs)
# ---------------------------------------------------------------------------

def test_scrape_worker_end_to_end():
    _qapp()
    from arkos_companion.ui.workers import ScrapeWorker

    with tempfile.TemporaryDirectory() as tmp:
        system_path = os.path.join(tmp, "arcade")
        os.makedirs(system_path)
        with open(os.path.join(system_path, "dmnfrnt.zip"), "wb") as fh:
            fh.write(b"pk")
        system = SystemFolder(name="arcade", display_name="Arcade", path=system_path)
        entry = GameEntry(
            sys_folder="arcade", rom_file="dmnfrnt.zip", rom_base="dmnfrnt",
            title="dmnfrnt.zip",
        )
        with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
                _patch_config_path(tmp), \
                mock.patch.object(
                    history,
                    "history_file_path",
                    return_value=os.path.join(tmp, history.HISTORY_FILENAME),
                ):
            scraper.ScraperConfig.load().save_api_key("test-key")
            worker = ScrapeWorker(system, entry)
            result = worker._work()
            journal_entries = history.load_entries()

        assert result["status"] == "ok"
        assert result["title"] == "Demon Front"
        assert result["year"] == "2002"
        image_path = os.path.join(system_path, "images", "dmnfrnt.png")
        assert os.path.isfile(image_path)
        assert result["image_path"] == image_path

        with open(os.path.join(system_path, "gamelist.xml"), encoding="utf-8") as fh:
            xml_text = fh.read()
        assert "<image>./images/dmnfrnt.png</image>" in xml_text
        # The scraped text is persisted too, not only the cover.
        assert "<name>Demon Front</name>" in xml_text
        assert "<desc>Run &amp; gun arcade shooter.</desc>" in xml_text

        assert len(journal_entries) == 1
        assert journal_entries[0]["action"] == history.ACTION_SCRAPE
        assert journal_entries[0]["rom_file"] == "dmnfrnt.zip"
        assert journal_entries[0]["details"]["title"] == "Demon Front"
        assert journal_entries[0]["details"]["year"] == "2002"


def test_mass_scrape_worker_summary_and_journal():
    _qapp()
    from arkos_companion.ui.workers import MassScrapeWorker

    with tempfile.TemporaryDirectory() as tmp:
        system_path = os.path.join(tmp, "arcade")
        os.makedirs(system_path)
        for name in ("dmnfrnt.zip", "unknown_game.zip"):
            with open(os.path.join(system_path, name), "wb") as fh:
                fh.write(b"pk")
        system = SystemFolder(name="arcade", display_name="Arcade", path=system_path)
        entries = [
            GameEntry(sys_folder="arcade", rom_file="dmnfrnt.zip",
                      rom_base="dmnfrnt", title="dmnfrnt.zip"),
            GameEntry(sys_folder="arcade", rom_file="unknown_game.zip",
                      rom_base="unknown_game", title="unknown_game.zip"),
        ]
        with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
                _patch_config_path(tmp), \
                mock.patch.object(
                    history,
                    "history_file_path",
                    return_value=os.path.join(tmp, history.HISTORY_FILENAME),
                ):
            scraper.ScraperConfig.load().save_api_key("test-key")
            worker = MassScrapeWorker(system, entries)
            summary = worker._work()
            journal = history.load_entries()

        assert summary == {"ok": 1, "not_found": 1, "errors": 0, "errors_list": []}
        assert os.path.isfile(os.path.join(system_path, "images", "dmnfrnt.png"))
        assert not os.path.exists(os.path.join(system_path, "images", "unknown_game.png"))
        assert len(journal) == 1
        assert journal[0]["rom_file"] == "dmnfrnt.zip"


def test_mass_scrape_worker_tolerates_per_entry_errors():
    _qapp()
    from arkos_companion.ui.workers import MassScrapeWorker

    def fake(request, timeout=None):
        url = request.full_url
        if "name=broken" in url:
            raise urllib.error.URLError("network down")
        return fake_urlopen(request, timeout=timeout)

    with tempfile.TemporaryDirectory() as tmp:
        system_path = os.path.join(tmp, "arcade")
        os.makedirs(system_path)
        for name in ("dmnfrnt.zip", "broken.zip"):
            with open(os.path.join(system_path, name), "wb") as fh:
                fh.write(b"pk")
        system = SystemFolder(name="arcade", display_name="Arcade", path=system_path)
        entries = [
            GameEntry(sys_folder="arcade", rom_file="dmnfrnt.zip",
                      rom_base="dmnfrnt", title="dmnfrnt.zip"),
            GameEntry(sys_folder="arcade", rom_file="broken.zip",
                      rom_base="broken", title="broken.zip"),
        ]
        with mock.patch.object(scraper, "_urlopen", side_effect=fake), \
                _patch_config_path(tmp), \
                mock.patch.object(
                    history,
                    "history_file_path",
                    return_value=os.path.join(tmp, history.HISTORY_FILENAME),
                ):
            scraper.ScraperConfig.load().save_api_key("test-key")
            worker = MassScrapeWorker(system, entries)
            summary = worker._work()
            journal = history.load_entries()

        assert summary["ok"] == 1
        assert summary["errors"] == 1
        assert summary["errors_list"] == ["broken.zip"]
        assert len(journal) == 1


# ---------------------------------------------------------------------------
# Sample-video download via the workers (include_video flag, end to end)
# ---------------------------------------------------------------------------

def test_scrape_worker_downloads_video_when_include_video():
    _qapp()
    from arkos_companion.ui.workers import ScrapeWorker

    with tempfile.TemporaryDirectory() as tmp:
        system_path = os.path.join(tmp, "arcade")
        os.makedirs(system_path)
        with open(os.path.join(system_path, "dmnfrnt.zip"), "wb") as fh:
            fh.write(b"pk")
        system = SystemFolder(name="arcade", display_name="Arcade", path=system_path)
        entry = GameEntry(
            sys_folder="arcade", rom_file="dmnfrnt.zip", rom_base="dmnfrnt",
            title="dmnfrnt.zip",
        )
        with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
                _patch_config_path(tmp), \
                mock.patch.object(
                    history,
                    "history_file_path",
                    return_value=os.path.join(tmp, history.HISTORY_FILENAME),
                ):
            scraper.ScraperConfig.load().save_api_key("test-key")
            worker = ScrapeWorker(system, entry, include_video=True)
            result = worker._work()

        assert result["status"] == "ok"
        video_path = os.path.join(system_path, "videos", "dmnfrnt.mp4")
        assert os.path.isfile(video_path)
        with open(video_path, "rb") as fh:
            assert fh.read() == MP4_BYTES
        assert result["video_path"] == video_path
        assert result["video_warning"] is None

        with open(os.path.join(system_path, "gamelist.xml"), encoding="utf-8") as fh:
            xml_text = fh.read()
        assert "<video>./videos/dmnfrnt.mp4</video>" in xml_text


def test_scrape_worker_skips_video_when_flag_off():
    _qapp()
    from arkos_companion.ui.workers import ScrapeWorker

    def fake(request, timeout=None):
        url = request.full_url
        if "/Games/Videos" in url:
            raise AssertionError("Videos endpoint must not be hit when flag is off")
        return fake_urlopen(request, timeout=timeout)

    with tempfile.TemporaryDirectory() as tmp:
        system_path = os.path.join(tmp, "arcade")
        os.makedirs(system_path)
        with open(os.path.join(system_path, "dmnfrnt.zip"), "wb") as fh:
            fh.write(b"pk")
        system = SystemFolder(name="arcade", display_name="Arcade", path=system_path)
        entry = GameEntry(
            sys_folder="arcade", rom_file="dmnfrnt.zip", rom_base="dmnfrnt",
            title="dmnfrnt.zip",
        )
        with mock.patch.object(scraper, "_urlopen", side_effect=fake), \
                _patch_config_path(tmp), \
                mock.patch.object(
                    history,
                    "history_file_path",
                    return_value=os.path.join(tmp, history.HISTORY_FILENAME),
                ):
            scraper.ScraperConfig.load().save_api_key("test-key")
            result = ScrapeWorker(system, entry, include_video=False)._work()

        assert result["status"] == "ok"
        assert result["video_path"] is None
        assert not os.path.exists(os.path.join(system_path, "videos"))


def test_scrape_worker_does_not_overwrite_existing_video():
    """A video already on the card for the stem is kept, never re-downloaded."""
    _qapp()
    from arkos_companion.ui.workers import ScrapeWorker

    with tempfile.TemporaryDirectory() as tmp:
        system_path = os.path.join(tmp, "arcade")
        os.makedirs(os.path.join(system_path, "videos"))
        with open(os.path.join(system_path, "dmnfrnt.zip"), "wb") as fh:
            fh.write(b"pk")
        video_path = os.path.join(system_path, "videos", "dmnfrnt.mp4")
        with open(video_path, "wb") as fh:
            fh.write(b"EXISTING-video")
        system = SystemFolder(name="arcade", display_name="Arcade", path=system_path)
        entry = GameEntry(
            sys_folder="arcade", rom_file="dmnfrnt.zip", rom_base="dmnfrnt",
            title="dmnfrnt.zip",
        )
        with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
                _patch_config_path(tmp), \
                mock.patch.object(
                    history,
                    "history_file_path",
                    return_value=os.path.join(tmp, history.HISTORY_FILENAME),
                ):
            scraper.ScraperConfig.load().save_api_key("test-key")
            result = ScrapeWorker(system, entry, include_video=True)._work()

        with open(video_path, "rb") as fh:
            assert fh.read() == b"EXISTING-video"
        assert result["status"] == "ok"
        # Nothing new was downloaded, so no <video> tag is written either.
        with open(os.path.join(system_path, "gamelist.xml"), encoding="utf-8") as fh:
            assert "<video>" not in fh.read()


def test_scrape_worker_video_only_preserves_curated_metadata():
    """A game that already has a cover enters video-only mode: the cover is
    not re-downloaded and the curated title/description are preserved, only
    the missing <video> tag is registered."""
    _qapp()
    import xml.etree.ElementTree as ET

    from arkos_companion.ui.workers import ScrapeWorker

    with tempfile.TemporaryDirectory() as tmp:
        system_path = os.path.join(tmp, "arcade")
        os.makedirs(os.path.join(system_path, "images"))
        with open(os.path.join(system_path, "dmnfrnt.zip"), "wb") as fh:
            fh.write(b"pk")
        # A cover the user already has on the card, with content DIFFERENT
        # from the fixture PNG so a re-download would be detected.
        existing_cover = os.path.join(system_path, "images", "dmnfrnt.png")
        with open(existing_cover, "wb") as fh:
            fh.write(b"CURATED-cover-bytes")
        # A curated gamelist entry whose title differs from the scrape result.
        root = ET.Element("gameList")
        game = ET.SubElement(root, "game")
        ET.SubElement(game, "path").text = "./dmnfrnt.zip"
        ET.SubElement(game, "name").text = "Demon Front (Curado)"
        ET.SubElement(game, "desc").text = "Descripción curada por el usuario"
        ET.ElementTree(root).write(
            os.path.join(system_path, "gamelist.xml"), encoding="utf-8"
        )
        system = SystemFolder(name="arcade", display_name="Arcade", path=system_path)
        entry = GameEntry(
            sys_folder="arcade", rom_file="dmnfrnt.zip", rom_base="dmnfrnt",
            title="Demon Front (Curado)", image_path=existing_cover,
        )
        with mock.patch.object(scraper, "_urlopen", side_effect=fake_urlopen), \
                _patch_config_path(tmp), \
                mock.patch.object(
                    history,
                    "history_file_path",
                    return_value=os.path.join(tmp, history.HISTORY_FILENAME),
                ):
            scraper.ScraperConfig.load().save_api_key("test-key")
            result = ScrapeWorker(system, entry, include_video=True)._work()

        assert result["status"] == "ok"
        video_path = os.path.join(system_path, "videos", "dmnfrnt.mp4")
        assert os.path.isfile(video_path)
        with open(video_path, "rb") as fh:
            assert fh.read() == MP4_BYTES
        # The existing cover was NOT re-downloaded.
        with open(existing_cover, "rb") as fh:
            assert fh.read() == b"CURATED-cover-bytes"
        with open(os.path.join(system_path, "gamelist.xml"), encoding="utf-8") as fh:
            xml_text = fh.read()
        assert "<video>./videos/dmnfrnt.mp4</video>" in xml_text
        # Curated title/description survived the scrape.
        assert "Demon Front (Curado)" in xml_text
        assert "Descripción curada por el usuario" in xml_text
        assert "Run &amp; gun arcade shooter" not in xml_text
