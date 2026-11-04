from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Route:
    parts: tuple[str, ...]
    reason: str
    unresolved: bool = False


def safe_component(value: str) -> str:
    """Return one safe Windows folder component, or an empty string."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(value))
    value = re.sub(r"\s+", " ", value).strip(" .")
    if (
        not value
        or value in {".", ".."}
        or re.match(r"(?i)^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", value)
    ):
        return ""
    return (
        value.encode("utf-16-le")[:510]
        .decode("utf-16-le", errors="ignore")
        .rstrip(" .")
    )


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


SITES = {
    "mangadex": "MangaDex",
    "asura scans": "Asura Scans",
    "asura comic": "Asura Scans",
    "asuracomic": "Asura Scans",
    "reaper scans": "Reaper Scans",
    "flame scans": "Flame Scans",
    "flame comics": "Flame Scans",
    "luminous scans": "Luminous Scans",
    "void scans": "Void Scans",
    "drake scans": "Drake Scans",
    "night scans": "Night Scans",
    "surya scans": "Surya Scans",
    "webtoon": "Webtoon",
    "webtoons": "Webtoon",
    "bilibili": "Bilibili",
    "tapas": "Tapas",
    "tappytoon": "Tappytoon",
    "mangaplus": "MangaPlus",
    "bato.to": "Bato.to",
    "batoto": "Bato.to",
    "manhuaplus": "ManhuaPlus",
    "mangakakalot": "MangaKakalot",
    "manganelo": "MangaNelo",
    "vyvymanga": "Vyvymanga",
    "mangareader": "MangaReader",
    "mangapark": "MangaPark",
    "kun manga": "Kun Manga",
    "harimanga": "Harimanga",
    "toongod": "ToonGod",
    "hivetoon": "Hivetoon",
}
BROWSERS = r"(?:Google Chrome|Chrome|Mozilla Firefox|Firefox|Microsoft Edge|Edge|Opera GX|Opera|Brave|Vivaldi|Arc|Zen Browser|Zen|Thorium|LibreWolf|Waterfox|Ablaze Floorp|Ablaze Fl|Ablaze|Floorp)"
BROWSER_SUFFIX = re.compile(r"(?i)(?:\s*[-|–—_]\s*|^)" + BROWSERS + r"\s*$")
CHAPTER = re.compile(
    r"(?i)\b(chapter|episode|volume|vol|ch|ep|capitulo|cap)\.?\s*(\d+(?:[.]\d+)?)\b"
)
YOUTUBE = re.compile(r"(?i)(?<!\w)(?:youtube|youtu\.be)(?!\w)")
GAME_ALIASES = {
    "bloodborne": "Bloodborne",
    "shadps4": "Bloodborne",
    "v rising": "VRising",
    "vrising": "VRising",
    "persona 5 royal": "Persona 5 Royal",
}


PLATFORMS = {
    "twitch": ("Video", "Twitch"),
    "reddit": ("Social", "Reddit"),
    "wikipedia": ("Reference", "Wikipedia"),
    "google search": ("Search", "Google Search"),
    "tiktok": ("Video", "TikTok"),
    "x.com": ("Social", "X.com"),
    "twitter": ("Social", "Twitter"),
    "google gemini": ("AI", "Google Gemini"),
    "grok": ("AI", "Grok"),
    "whatsapp": ("Social", "WhatsApp"),
    "colab": ("Docs", "Colab"),
}


GENERIC_TITLES = {"greenshot", "screenshot", "untitled", "new tab", "image", "desktop"}


class Routing:
    def __init__(self, root: Path, corrections: Path | None = None):
        self.root = Path(root)
        data = {}
        if corrections is not None and Path(corrections).exists():
            with Path(corrections).open(encoding="utf-8-sig") as file:
                data = json.load(file)
        self.exact = {}
        for title, parts in data.get("exact_titles", {}).items():
            if not isinstance(parts, list) or any(
                not isinstance(p, str)
                or not p
                or safe_component(p) != p
                or _key(p) == "unsorted"
                for p in parts
            ):
                raise ValueError(f"Invalid correction route for {title!r}")
            self.exact[_key(title)] = tuple(parts)
        self.series_aliases = self._aliases(data.get("series_aliases", {}))
        self.site_aliases = {_key(k): v for k, v in SITES.items()}
        self.site_aliases.update(self._aliases(data.get("site_aliases", {})))
        self.known = {}
        if self.root.is_dir():
            excluded = {
                "browser",
                "browser screenshots",
                "manga",
                "potplayer",
                "unsorted",
                "media",
                "games",
                "applications",
            }
            self.known = {
                _key(p.name): p.name
                for p in self.root.iterdir()
                if p.is_dir()
                and _key(p.name) not in excluded | GENERIC_TITLES
                and safe_component(p.name) == p.name
            }
        manga = self.root / "Browser" / "Manga"
        self.series = (
            {_key(p.name): p.name for p in manga.iterdir() if p.is_dir()}
            if manga.is_dir()
            else {}
        )
        old_manga = self.root / "manga"
        if old_manga.is_dir():
            for p in old_manga.iterdir():
                if p.is_dir():
                    self.series.setdefault(_key(p.name), p.name)

    @staticmethod
    def _aliases(mapping: dict) -> dict[str, str]:
        result = {}
        for alias, target in mapping.items():
            if (
                not _key(alias)
                or not isinstance(target, str)
                or not target
                or safe_component(target) != target
            ):
                raise ValueError(f"Invalid alias target for {alias!r}")
            result[_key(alias)] = target
        return result

    def canonical_series(self, value: str) -> str:
        value = safe_component(value)
        return self.series_aliases.get(_key(value), self.series.get(_key(value), value))

    def canonical_site(self, value: str) -> str:
        value = safe_component(value)
        return self.site_aliases.get(_key(value), value)

    def _site(self, title: str) -> tuple[str, str]:
        text = _key(title)
        for alias in sorted(self.site_aliases, key=len, reverse=True):
            if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text):
                return alias, self.site_aliases[alias]
        return "", ""

    @staticmethod
    def _clean_series(value: str) -> str:
        value = re.sub(r"(?i)^\s*(?:read(?: online)?|raw)\s+", "", value)
        value = re.sub(r"(?i)^\s*\(\s*(?:s\d+|season\s+\d+)[^)]*\)\s*", "", value)
        value = re.sub(r"(?i)\s*[-|–—_]\s*read online\b.*$", "", value)
        return safe_component(value.strip(" -_|–—:·"))

    def classify(self, title: str) -> Route:
        title = title.strip()
        if _key(title) in self.exact:
            return Route(self.exact[_key(title)], "exact correction")
        if not title:
            return Route((), "empty title", True)
        player = re.search(r"(?i)(?:\s*[-|–—_]\s*|^)potplayer\s*$", title)
        if player:
            name = title[: player.start()].strip()
            name = re.sub(r"(?i)\.(?:mkv|mp4|avi|webm|ts|flv)$", "", name)
            name = re.sub(r"^\[[^]]+\]\s*", "", name)
            name = re.sub(
                r"(?i)(?:(?:\s*[-–—_]\s*|\s+)(?:s\d+\s*e\d+|episode\s*\d+|e\d+)|\s+[-–—_]\s+\d{2,3}(?:v\d+)?)\b.*$",
                "",
                name,
            )
            name = re.sub(
                r"(?i)\s*(?:\[(?:\d{3,4}p|[xh]26[45]|hevc|[a-f\d]{8})\]\s*)+$", "", name
            )
            name = safe_component(name)
            return (
                Route(("PotPlayer", name), "media player")
                if name
                else Route(("PotPlayer",), "player without media title", True)
            )

        browser = BROWSER_SUFFIX.search(title)
        web_title = title[: browser.start()].strip() if browser else title
        site_alias, site = self._site(web_title)
        youtube = bool(YOUTUBE.search(web_title))
        other_site = next(
            (
                parts
                for key, parts in PLATFORMS.items()
                if re.search(
                    r"(?:^|[-–—_|]\s*|^\(\d+\)\s*)" + re.escape(key) + r"$",
                    _key(web_title),
                )
            ),
            None,
        )
        is_web = bool(browser or site or youtube or other_site)
        # Native names are exact, including legacy version/build suffix cleanup.
        native = re.sub(
            r"(?i)\s+(?:v\d[\d.\-v]*|plus\s+\d+\s+trainer.*|build\s+\d+.*|patch\s+[\d.]+.*)$",
            "",
            title,
        ).strip()
        native_subject = self.known.get(_key(native)) or GAME_ALIASES.get(_key(native))
        game = next(
            (
                mapped
                for alias, mapped in GAME_ALIASES.items()
                if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", _key(web_title))
            ),
            "",
        )
        if not is_web:
            native_subject = native_subject or game
        if not is_web and native_subject:
            return Route((native_subject,), "known application or game")
        if not is_web:
            # Long release names may be cut off before the player suffix.
            release = re.match(
                r"(?i)^(.+?)\.S\d{1,2}E\d{1,3}\..*(?:\d{3,4}p|web[-.]dl|bluray|\.mkv)",
                title,
            )
            if release:
                show = safe_component(release[1].replace(".", " "))
                if show:
                    return Route(("Media", show), "episode release filename")

        chapter = CHAPTER.search(web_title)
        strong_marker = chapter and _key(chapter.group(1)) in {
            "chapter",
            "ch",
            "capitulo",
            "cap",
        }
        season = re.search(r"(?i)\(\s*(?:season\s*|s)(\d+)\s*\)", web_title)
        known_serial = False
        if browser and chapter:
            names = (web_title[: chapter.start()], web_title[chapter.end() :])
            known_serial = any(
                _key(self._clean_series(n)) in self.series for n in names
            )
            # Reader titles commonly use 'Episode N _ Series' for webtoons.
            known_serial |= bool(
                re.match(r"(?i)^episode\s+\d+(?:\.\d+)?\s+_\s+\S", web_title)
            )
        manga_evidence = (
            not youtube
            and not other_site
            and (site or strong_marker or known_serial or (season and chapter))
        )
        if manga_evidence:
            # Site titles usually appear as a separated final segment; remove only that segment.
            body = web_title
            if site_alias:
                body = re.sub(
                    r"(?i)(?:^|\s*[-|–—_]\s*)"
                    + re.escape(site_alias)
                    + r"(?=$|\s*[-|–—_]).*$",
                    "",
                    body,
                ).strip()
            marker = CHAPTER.search(body)
            series = body
            segment = ()
            if marker:
                before = body[: marker.start()].strip(" -_|–—:")
                after = body[marker.end() :].strip(" -_|–—:")
                # Chapter-first titles can carry a season tag before the series.
                before_clean = self._clean_series(before)
                series = (
                    before
                    if before_clean
                    and (any(c.isalpha() for c in before_clean) or not after)
                    else after
                )
                family = _key(marker.group(1))
                label = (
                    "Episode"
                    if family in {"episode", "ep"}
                    else "Volume"
                    if family in {"volume", "vol"}
                    else "Chapter"
                )
                number = "".join(
                    str(unicodedata.decimal(c)) if c.isdecimal() else c
                    for c in marker.group(2)
                )
                season_number = (
                    "".join(str(unicodedata.decimal(c)) for c in season.group(1))
                    if season
                    else ""
                )
                segment = (
                    (f"Season {season_number} " if season_number else "")
                    + label
                    + " "
                    + number,
                )
            series = self.canonical_series(self._clean_series(series))
            if series and _key(series) not in self.site_aliases:
                return Route(
                    ("Browser", "Manga", series) + segment + ((site,) if site else ()),
                    "manga",
                )
            if site:
                return Route(
                    ("Browser", "Manga", site), "manga site without series", True
                )
        if is_web:
            if game:
                gaming_site = (
                    "YouTube" if youtube else other_site[1] if other_site else ""
                )
                return Route(
                    ("Browser", "Gaming", game)
                    + ((gaming_site,) if gaming_site else ()),
                    "browser game",
                )
            if youtube:
                return Route(("Browser", "Video", "YouTube"), "youtube")
            if other_site:
                return Route(("Browser",) + other_site, "known website")
            return Route(("Browser", "Unsorted"), "unknown browser content", True)
        return Route((), "unknown application", True)
