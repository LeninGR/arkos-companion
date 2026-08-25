"""TheGamesDB-powered metadata scraper (pure Python, no Qt imports).

Fetches game metadata (title, description, year, developer), cover art and
sample videos from the public TheGamesDB v1 API using ONLY the standard
library (``urllib.request``), so the code can run inside background workers
and be unit-tested without a display or a network.

Free tier note: TheGamesDB serves game descriptions in ENGLISH only.  The
app does not translate them; the UI shows them as-is.

TheTheGamesDB v1 API exposes per-game sample videos (``/Games/Videos``);
``fetch_game_video_url`` resolves the first one to its CDN URL.  Saved media
files are discovered by stem, so a ``videos/<rom_base>.mp4`` downloaded next
to an ``images/<rom_base>.png`` is picked up automatically by the app and
by EmulationStation once they are registered in ``gamelist.xml`` via the
``<video>`` tag.
"""

from __future__ import annotations

import difflib
import html
import json
import os
import re
import shutil
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

from arkos_companion import history
from arkos_companion import rom_index
from arkos_companion.compat_db import get_compat

# TheGamesDB API root; each endpoint path below carries its own version
# prefix (``/v1/...`` for v1 endpoints, ``/v1.1/...`` for the search API).
API_BASE = "https://api.thegamesdb.net"
CDN_BASE = "https://cdn.thegamesdb.net/images/"
KEY_PAGE_URL = "https://api.thegamesdb.net/key.php"
API_FORUM_URL = "https://forums.thegamesdb.net/viewforum.php?f=10"
SITE_URL = "https://thegamesdb.net"

# The registered application identity sent to TheGamesDB with every request
# (the API key itself is granted per application/account via key.php).
APP_NAME = "ArkOS Companion"
APP_VERSION = "1.0"
USER_AGENT = f"{APP_NAME}/{APP_VERSION}"
_HTTP_TIMEOUT = 30

# The application's own TheGamesDB API key.  Never committed: the project
# author obtains one via the TheGamesDB forums (api.thegamesdb.net/key.php)
# and keeps it OUT of the repository.  It is supplied at runtime through,
# in order of precedence:
#   1. ``scraper_config.json`` next to the journal (per-machine override),
#   2. the ``THEGAMESDB_API_KEY`` environment variable,
#   3. this constant (left empty in the public repository).
# When no key is configured, scraping is disabled and the UI shows a short
# setup hint instead.
DEFAULT_API_KEY = ""

# Image extensions accepted for cover downloads (mirrors scanner's set).
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})

# Video extensions accepted for the sample-video download.  TheGamesDB serves
# its game videos exclusively as .mp4 (``videos/<game_id>-<n>.mp4``).
_VIDEO_EXTENSIONS = frozenset({".mp4"})

CONFIG_FILENAME = "scraper_config.json"


def effective_api_key(config: Optional["ScraperConfig"] = None) -> str:
    """API key to use: config override wins, then the env var, then embedded.

    Empty when nothing is configured (scraping disabled with a UI hint).
    """
    if config is None:
        config = ScraperConfig.load()
    configured = config.api_key().strip()
    if configured:
        return configured
    from_env = os.environ.get("THEGAMESDB_API_KEY", "").strip()
    if from_env:
        return from_env
    return DEFAULT_API_KEY.strip()


class ScrapeError(Exception):
    """A hard scraping failure (network, HTTP status, invalid response).

    ``status`` carries the HTTP status code when the failure came from an
    HTTP error response (used to decide endpoint fallbacks), else ``None``.
    """

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ScrapeResult:
    """Metadata scraped for one game (fields may be ``None`` when absent)."""

    title: str
    description: Optional[str]
    year: Optional[str]
    developer: Optional[str]
    image_url: Optional[str]
    game_id: Optional[int] = None


def _urlopen(request, timeout: int):
    """Module-level hook around ``urllib.request.urlopen`` (mockable in tests)."""
    return urllib.request.urlopen(request, timeout=timeout)


# ---------------------------------------------------------------------------
# Text normalization (must match scanner._normalize_key so stems align)
# ---------------------------------------------------------------------------

def _strip_diacritics(text: str) -> str:
    """Remove combining diacritics from ``text`` (e.g. "á" -> "a")."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize_key(s: str) -> str:
    """Normalize a string for fuzzy comparison: lowercase, no diacritics, alnum only.

    Reuses the same idea as ``scanner._normalize_key`` so scraped titles and
    ROM stems compare consistently.
    """
    clean = _strip_diacritics(s or "")
    return "".join(ch for ch in clean if ch.isalnum()).lower()


def pick_best_match(game: dict, query: str) -> bool:
    """True when a search result is a good match for ``query``.

    An exact normalized match always wins; otherwise a
    ``difflib.SequenceMatcher`` ratio of at least 0.8 is accepted.
    """
    title = str(game.get("game_title") or "")
    query_key = normalize_key(query)
    if not query_key:
        return False
    if normalize_key(title) == query_key:
        return True
    ratio = difflib.SequenceMatcher(None, normalize_key(title), query_key).ratio()
    return ratio >= 0.8


def resolve_search_name(rom_base: str, title_hint: Optional[str] = None) -> str:
    """Pick the search name for a ROM: compat DB > offline title > hint > raw.

    ``compat_db`` overrides remain authoritative for curated games; then the
    packaged FBNeo index (``roms_index.tsv``) provides the real title for
    the bulk of arcade ROMs; finally ``title_hint`` (user/ES metadata) and
    the raw stem are used.  The offline index wins over ``title_hint`` so a
    "martmast" zip still searches for "Martial Masters" even when the
    gamelist title is still the zip name.
    """
    compat = get_compat(rom_base)
    if compat and compat.get("name"):
        return compat["name"]
    identified = rom_index.identify(rom_base)
    if identified and identified.get("title"):
        return identified["title"]
    if title_hint:
        return title_hint
    return rom_base


def resolve_search_year(rom_base: str) -> Optional[str]:
    """Year known from the offline index (used for extra precision)."""
    identified = rom_index.identify(rom_base)
    return (identified or {}).get("year") or None


# Separators that introduce a subtitle in arcade titles.  Everything after
# the first occurrence is dropped when a plain-title search is too strict
# for TheGamesDB (e.g. "The King of Fighters '98 - The Slugfest").
_SUBTITLE_SEPARATORS = ("-", ":", "/")

# Universal region/language/misc marker groups in home-console ROM names
# (e.g. "Dragon Booster (Europe) (En,Fr,De,Es,It)" or "[Alternative]").
_TAG_GROUP_PATTERN = re.compile(r"[\(\[][^\)\]]*[\)\]]")


def clean_title_tags(title: str) -> str:
    """Remove region/language/misc markers and collapse whitespace.

    Universal cleaner for home-console ROM names: every ``(...)`` and
    ``[...]`` group is removed entirely, then whitespace runs are collapsed
    and the result stripped.  Examples:

      * "Dragon Booster (Europe) (En,Fr,De,Es,It)" -> "Dragon Booster"
      * "Pokémon - Version Esmeralda (Spain) [Alternative]" ->
        "Pokémon - Version Esmeralda"

    Titles without markers (the normal case for arcade) are returned
    unchanged.  When every non-empty character is inside markers (e.g. a
    filename that is only "(Europe)"), the input is returned unchanged so
    the search never degrades into an empty query.
    """
    if not title:
        return ""
    cleaned = _TAG_GROUP_PATTERN.sub("", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else title


def resolve_clean_query(rom_base: str, title_hint: Optional[str] = None) -> str:
    """Resolved search name with the universal tag cleanup already applied.

    Used by workers so their logs report the ORIGINAL processed name next
    to the cleaned name that will actually be queried against TheGamesDB.
    """
    query = resolve_search_name(rom_base, title_hint)
    cleaned = clean_title_tags(query)
    return cleaned or query


def sanitize_title(title: str) -> List[str]:
    """Return progressively shorter title candidates for match fallbacks.

    The first candidate is the full title; each following candidate removes
    everything after the first subtitle separator (``-``, ``:`` or ``/``).
    Only shorter, non-empty, distinct candidates are produced, preserving
    order so callers can retry the exact match first and only then fall back
    to the simplified title.
    """
    if not title:
        return [""]
    candidates: List[str] = [title]
    for separator in _SUBTITLE_SEPARATORS:
        if separator in title:
            simplified = title.split(separator, 1)[0].strip()
            if simplified and simplified != candidates[-1]:
                candidates.append(simplified)
    return candidates


def _strip_html(text: str) -> str:
    """Strip HTML tags, unescape entities and collapse whitespace."""
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_year(release_date: str) -> Optional[str]:
    """First 4-digit group of a release date (e.g. "2002-10-01" -> "2002")."""
    match = re.search(r"\d{4}", release_date or "")
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# TheGamesDB v1 API client
# ---------------------------------------------------------------------------

class TheGamesDBClient:
    """Minimal stdlib-only client for the public TheGamesDB v1 API.

    All failures surface as ``ScrapeError`` so callers never deal with raw
    urllib exceptions.  The CDN base URL is learned from each response and
    falls back to the well-known default when absent.
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = (api_key or "").strip()
        self._base_url = CDN_BASE

    # -- HTTP plumbing ------------------------------------------------------
    def _get(self, path: str, params: dict) -> dict:
        """GET ``path`` with ``params`` (+ apikey), returning the parsed JSON."""
        params = dict(params)
        params["apikey"] = self.api_key
        url = API_BASE + path + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with _urlopen(request, _HTTP_TIMEOUT) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise ScrapeError(
                "TheGamesDB respondió con error HTTP {} ({}).".format(exc.code, path),
                status=exc.code,
            ) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ScrapeError(
                "No se pudo contactar con TheGamesDB: {}".format(exc)
            ) from None
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise ScrapeError(
                "Respuesta inválida de TheGamesDB: {}".format(exc)
            ) from None
        if not isinstance(data, dict):
            raise ScrapeError("Respuesta inválida de TheGamesDB: no es un objeto JSON.")
        base = (data.get("data") or {}).get("base_url")
        if isinstance(base, str) and base:
            self._base_url = base
        elif isinstance(base, dict):
            # v1.1 images responses carry a dict of CDN sizes; prefer original.
            original = (base.get("original") or "").strip()
            if original:
                self._base_url = original
        return data

    # -- Endpoints ----------------------------------------------------------
    def search_by_name(self, name: str) -> List[dict]:
        """Search games by name; returns normalized ``[{id, game_title}]``.

        Uses the v1.1 search endpoint (natural-language search is only
        triggered when ``mode=natural`` is passed; plain name matching is the
        default and works well for exact arcade titles).
        """
        return self._search("/v1.1/Games/ByGameName", {"name": name})

    def _search(self, path: str, params: dict) -> List[dict]:
        data = self._get(path, params)
        games = (data.get("data") or {}).get("games") or []
        return self._normalize_games(games)

    @staticmethod
    def _normalize_games(games: object) -> List[dict]:
        """Normalize any ``games`` payload shape into ``[{id, game_title}]``."""
        if isinstance(games, dict):
            games = list(games.values())
        normalized: List[dict] = []
        for game in games or []:
            if not isinstance(game, dict):
                continue
            try:
                game_id = int(game.get("id"))
            except (TypeError, ValueError):
                continue
            title = game.get("game_title")
            if not title:
                continue
            normalized.append({"id": game_id, "game_title": str(title)})
        return normalized

    def get_game_by_id(self, game_id) -> dict:
        """Fetch the full record for one game id (title, date, overview, ...).

        ``data.games`` is keyed by id, so the first available game object is
        returned.  ``fields=overview`` is requested explicitly because the v1
        detail endpoint does not return the free-text description by default.
        Raises ``ScrapeError`` when the payload has no game data.
        """
        data = self._get("/v1/Games/ByGameID", {"id": game_id, "fields": "overview"})
        games = (data.get("data") or {}).get("games") or {}
        if isinstance(games, dict):
            games = list(games.values())
        for game in games or []:
            if isinstance(game, dict):
                return game
        raise ScrapeError(
            "TheGamesDB no devolvió el juego solicitado (id {}).".format(game_id)
        )

    def developers_by_ids(self, ids: list) -> Dict[str, str]:
        """Map developer ids -> names (tolerant: any failure returns ``{}``)."""
        if not ids:
            return {}
        try:
            data = self._get(
                "/v1/Developers/ByDeveloperID", {"id": ",".join(str(i) for i in ids)}
            )
        except ScrapeError:
            return {}
        developers = (data.get("data") or {}).get("developers") or {}
        if isinstance(developers, dict):
            items = developers.items()
        else:
            items = [
                (item.get("id"), item)
                for item in developers
                if isinstance(item, dict) and item.get("name")
            ]
        result: Dict[str, str] = {}
        for key, dev in items:
            if isinstance(dev, dict) and dev.get("name"):
                result[str(key)] = str(dev["name"])
        return result

    def image_url(self, boxart_list) -> Optional[str]:
        """Build the CDN URL of the best boxart (front) of a game, or ``None``.

        Prefers ``type == "boxart"`` with ``side == "front"``; falls back to
        any other box art; returns ``None`` when there is no usable art.
        """
        boxarts = [b for b in (boxart_list or []) if isinstance(b, dict)]
        if not boxarts:
            return None

        def score(boxart: dict) -> int:
            art_type = (boxart.get("type") or "").lower()
            side = (boxart.get("side") or "").lower()
            if art_type == "boxart" and side == "front":
                return 0
            if art_type == "boxart":
                return 1
            return 2

        best = min(boxarts, key=score)
        filename = best.get("filename")
        if not filename:
            return None
        base = (self._base_url or CDN_BASE).rstrip("/") + "/"
        return base + str(filename).lstrip("/")

    def boxart_list(self, game_id) -> List[dict]:
        """Fetch the boxart entries of a game via the /Games/Images endpoint.

        The v1 detail endpoint does not include art, so images require a
        dedicated call.  Returns normalized ``[{type, side, filename}]``.
        """
        try:
            data = self._get(
                "/v1/Games/Images",
                {"games_id": str(game_id), "filter[type]": "boxart"},
            )
        except ScrapeError:
            return []
        images = (data.get("data") or {}).get("images") or {}
        if isinstance(images, dict):
            values = [v for v in images.values()]
        elif isinstance(images, list):
            values = images
        else:
            values = []
        entries: List[dict] = []
        for value in values:
            if isinstance(value, list):
                entries.extend(
                    item for item in value if isinstance(item, dict)
                )
        return entries


def fetch_game_video_url(game_id: int) -> Optional[str]:
    """Return the absolute CDN URL of the first sample video, or ``None``.

    Queries the v1 ``/Games/Videos`` endpoint and returns
    ``base_url + filename`` for the first video of ``game_id``.  Fails SOFT:
    every problem (no video, ``count == 0``, malformed payload, HTTP error
    such as the rate-limited 429) returns ``None`` so the caller logs and
    continues -- a missing video must never break an enclosing scrape.
    """
    try:
        client = TheGamesDBClient(effective_api_key())
        data = client._get("/v1/Games/Videos", {"games_id": str(game_id)})
    except ScrapeError:
        return None
    payload = data.get("data") or {}
    if not isinstance(payload, dict):
        return None
    videos = payload.get("videos")
    if not isinstance(videos, dict):
        return None
    group = videos.get(str(game_id))
    if not isinstance(group, list) or not group:
        return None
    first = group[0]
    if not isinstance(first, dict) or not first.get("filename"):
        return None
    base_url = payload.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        return None
    return base_url.rstrip("/") + "/" + str(first["filename"]).lstrip("/")


# ---------------------------------------------------------------------------
# Media download
# ---------------------------------------------------------------------------

def url_extension(url: str) -> str:
    """Lowercased image extension of a URL basename, or ``""`` when unknown."""
    basename = (url or "").split("?")[0].rsplit("/", 1)[-1]
    ext = os.path.splitext(basename)[1].lower()
    return ext if ext in _IMAGE_EXTENSIONS else ""


def video_extension(url: str) -> str:
    """Lowercased video extension of a URL basename, or ``""`` when unknown.

    Only accepted video extensions are returned (TheGamesDB serves its
    samples as ``.mp4``), so a malformed CDN filename is never written to
    the card under a bogus extension.
    """
    basename = (url or "").split("?", 1)[0].rsplit("/", 1)[-1]
    ext = os.path.splitext(basename)[1].lower()
    return ext if ext in _VIDEO_EXTENSIONS else ""


def download_media(url: str, dest_path: str) -> str:
    """Download ``url`` into ``dest_path`` atomically (via a ``.part`` file).

    When ``dest_path`` has no extension, one is derived from the URL basename
    (only for known image extensions).  Raises ``ScrapeError`` on any failure
    and never leaves the ``.part`` file behind.
    """
    if not url:
        raise ScrapeError("No hay URL de media para descargar.")
    ext = url_extension(url)
    if ext and not os.path.splitext(dest_path)[1]:
        dest_path += ext
    part_path = dest_path + ".part"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _urlopen(request, _HTTP_TIMEOUT) as response:
            with open(part_path, "wb") as out:
                shutil.copyfileobj(response, out)
        os.replace(part_path, dest_path)
    except Exception as exc:  # noqa: BLE001 - any failure becomes a ScrapeError
        _quiet_remove(part_path)
        raise ScrapeError("No se pudo descargar el media: {}".format(exc)) from None
    return dest_path


def _quiet_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# High-level scraping entry point
# ---------------------------------------------------------------------------

def scrape_game(
    api_key: str,
    rom_base: str,
    title_hint: Optional[str] = None,
) -> Optional[ScrapeResult]:
    """Scrape metadata for one ROM base name from TheGamesDB.

    Returns ``None`` when no acceptable match exists (NOT an error); raises
    ``ScrapeError`` for real failures (network, invalid responses).  The
    search name is resolved through the offline FBNeo index first (zip stem
    -> clean game title), then the internal compatibility database, then
    ``title_hint`` and finally the raw stem.

    Match fallback chain (in order, each only when the previous found no
    acceptable ``pick_best_match``):

      1. full title (e.g. "The King of Fighters '98 - The Slugfest");
      2. tag-cleaned title: every ``(...)``/``[...]`` region/language group
         is dropped, then whitespace is collapsed (universal cleaner for
         home consoles, e.g. "Dragon Booster (Europe) (En,Fr,De,Es,It)" ->
         "Dragon Booster");
      3. sanitized title (everything after the first ``-`` / ``:`` / ``/``
         is dropped from the cleaned name, e.g. "The King of Fighters '98");
      4. "Title Year" (e.g. "Martial Masters 2000") when the offline index
         knows the release year.

    Descriptions come from the free tier, which is ENGLISH ONLY (no
    translation is attempted).
    """
    client = TheGamesDBClient(api_key)
    query = resolve_search_name(rom_base, title_hint)

    def find_match(candidate: str) -> Optional[dict]:
        games = client.search_by_name(candidate)
        return next((g for g in games if pick_best_match(g, candidate)), None)

    match = find_match(query)

    # Universal tag cleaner: drop regions/languages in parentheses or
    # brackets and retry (e.g. "Dragon Booster (Europe) (En,Fr,De,Es,It)").
    cleaned_query = clean_title_tags(query)
    if match is None and cleaned_query != query:
        match = find_match(cleaned_query)
        if match is not None:
            query = cleaned_query

    # Sanitizer fallback: drop subtitles introduced by -, : or / and retry.
    if match is None:
        for simplified in sanitize_title(cleaned_query):
            if simplified == cleaned_query:
                continue
            match = find_match(simplified)
            if match is not None:
                query = simplified
                break

    # Extra precision pass: the plain-title search was not conclusive but the
    # offline index knows the release year - retry once with "Name Year".
    if match is None:
        year = resolve_search_year(rom_base)
        if year:
            query_with_year = "{} {}".format(query, year)
            match = find_match(query_with_year)
            if match is not None:
                query = query_with_year

    if match is None:
        return None

    game = client.get_game_by_id(match["id"])
    title = str(game.get("game_title") or query)
    description = _strip_html(str(game.get("overview") or "")) or None
    year = _extract_year(str(game.get("release_date") or ""))

    developer = None
    developer_ids = game.get("developers") or []
    if developer_ids:
        by_id = client.developers_by_ids(developer_ids)
        developer = by_id.get(str(developer_ids[0]))

    return ScrapeResult(
        title=title,
        description=description,
        year=year,
        developer=developer,
        image_url=client.image_url(client.boxart_list(match["id"])),
        game_id=match["id"],
    )


# ---------------------------------------------------------------------------
# Scraper configuration (stored next to the journal)
# ---------------------------------------------------------------------------

def config_file_path() -> str:
    """Absolute path of the scraper config (same directory as the journal)."""
    return os.path.join(os.path.dirname(history.history_file_path()), CONFIG_FILENAME)


class ScraperConfig:
    """Persistent scraper settings (the TheGamesDB API key).

    ``config_path`` can be injected so tests redirect writes to a temp dir.
    Every operation is best-effort: a missing/corrupt file behaves as an
    empty config and a failing write is swallowed (never crashes the app).
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._path = config_path or config_file_path()

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "ScraperConfig":
        """Load the config (never raises; missing/invalid file -> empty config)."""
        return cls(config_path)

    def _read(self) -> dict:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def api_key(self) -> str:
        """Return the configured API key (stripped), or ``""`` when unset."""
        value = self._read().get("api_key")
        return str(value).strip() if value else ""

    def has_api_key(self) -> bool:
        """True when a non-empty API key is stored."""
        return bool(self.api_key())

    def save_api_key(self, key: str) -> None:
        """Persist the API key atomically (temp file + ``os.replace``).

        Best effort: ``OSError`` while writing is swallowed so a read-only
        config location can never crash the UI flow.
        """
        tmp_path = self._path + ".tmp"
        data = {"api_key": (key or "").strip()}
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp_path, self._path)
        except OSError:
            pass
