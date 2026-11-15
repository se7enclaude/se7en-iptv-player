"""Sous-titres pour tous les films et toutes les séries, même quand le fichier du fournisseur n'en a pas.

Au lancement d'un film ou d'un épisode, si la vidéo n'a pas de piste dans tes langues de sous-titres, les meilleurs
sous-titres d'OpenSubtitles (base publique, sans compte) sont cherchés par l'identifiant IMDb de l'œuvre (trouvé par
son numéro TMDB sur Wikidata), ou à défaut par son titre et son année, puis ajoutés au lecteur comme des pistes
normales : « Français · OpenSubtitles 1 », « Français · OpenSubtitles 2 »… (deux par langue : si le premier est décalé,
le second vient souvent d'une autre version du film). Fichiers gardés 30 jours dans ``cache/subs``.
"""
from __future__ import annotations

import gzip
import hashlib
import re
import time
from pathlib import Path
from typing import Any

from ..config import paths
from ..logging_setup import get_logger

log = get_logger("stream.subs")

API = "https://rest.opensubtitles.org/search"
HEADERS = {"X-User-Agent": "TemporaryUserAgent", "User-Agent": "TemporaryUserAgent"}
PER_LANG = 2
# codes ISO 639-2 d'OpenSubtitles ← langues des réglages (« fr,fre,fra »)
OS_LANG = {"fr": "fre", "fre": "fre", "fra": "fre", "en": "eng", "eng": "eng", "es": "spa", "spa": "spa", "it": "ita",
           "ita": "ita", "de": "ger", "ger": "ger", "deu": "ger", "pt": "por", "por": "por", "ar": "ara", "ara": "ara",
           "tr": "tur", "tur": "tur", "nl": "dut", "dut": "dut", "nld": "dut", "pl": "pol", "pol": "pol", "ru": "rus",
           "rus": "rus", "ro": "rum", "el": "ell", "sv": "swe", "da": "dan", "fi": "fin", "no": "nor", "he": "heb",
           "fa": "per", "hi": "hin", "ko": "kor", "ja": "jpn", "zh": "chi"}
LABEL = {"fre": "Français", "eng": "Anglais", "spa": "Espagnol", "ita": "Italien", "ger": "Allemand", "por": "Portugais",
         "ara": "Arabe", "tur": "Turc", "dut": "Néerlandais", "pol": "Polonais", "rus": "Russe", "rum": "Roumain",
         "ell": "Grec", "swe": "Suédois", "dan": "Danois", "fin": "Finnois", "nor": "Norvégien", "heb": "Hébreu",
         "per": "Persan", "hin": "Hindi", "kor": "Coréen", "jpn": "Japonais", "chi": "Chinois"}
# même langue sous d'autres noms dans les pistes de la vidéo
SAME = {"fre": {"fr", "fre", "fra", "french", "francais"}, "eng": {"en", "eng", "english"}, "spa": {"es", "spa", "spanish"},
        "ara": {"ar", "ara", "arabic"}, "tur": {"tr", "tur", "turkish"}, "ita": {"it", "ita", "italian"},
        "ger": {"de", "ger", "deu", "german"}, "por": {"pt", "por", "portuguese"}}


def wanted_langs(setting: str) -> list[str]:
    """Langues à chercher, dans l'ordre des réglages ; l'anglais en secours (le plus souvent disponible)."""
    out: list[str] = []
    for x in (setting or "fr").split(","):
        c = OS_LANG.get(x.strip().lower())
        if c and c not in out:
            out.append(c)
    if "eng" not in out:
        out.append("eng")
    return out[:3]


def cache_dir() -> Path:
    d = paths().cache / "subs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str) -> Any:
    import httpx
    r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def search(imdb: str | None, lang: str, *, title: str = "", year: int | None = None, season: int | None = None,
           episode: int | None = None, fetch=_get) -> list[dict]:
    """Sous-titres d'une langue, les plus téléchargés d'abord, en un seul fichier (pas de « CD1 / CD2 »)."""
    parts = []
    if episode is not None:
        parts.append(f"episode-{int(episode)}")
    if imdb:
        parts.append(f"imdbid-{int(re.sub(r'[^0-9]', '', imdb))}")
    elif title:
        parts.append(f"query-{_slug(title)}")
    else:
        return []
    if season is not None:
        parts.append(f"season-{int(season)}")
    parts.append(f"sublanguageid-{lang}")
    try:
        rows = fetch(f"{API}/{'/'.join(parts)}") or []
    except Exception as e:  # noqa: BLE001 — hors ligne, service indisponible
        log.info("OpenSubtitles unavailable: %s", e)
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if str(r.get("SubSumCD") or "1") != "1" or str(r.get("SubFormat") or "srt").lower() not in ("srt", "sub", "ssa", "ass", "vtt"):
            continue
        if not imdb and year and r.get("MovieYear") and abs(int(r["MovieYear"] or 0) - year) > 1:
            continue
        if season is not None and episode is not None and (str(r.get("SeriesSeason")) != str(season)
                                                           or str(r.get("SeriesEpisode")) != str(episode)):
            continue
        out.append({"id": str(r.get("IDSubtitleFile")), "lang": lang, "release": (r.get("MovieReleaseName") or r.get("SubFileName") or "").strip(),
                    "downloads": int(r.get("SubDownloadsCnt") or 0), "hi": str(r.get("SubHearingImpaired")) == "1",
                    "encoding": r.get("SubEncoding") or "", "link": r.get("SubDownloadLink") or "",
                    "format": str(r.get("SubFormat") or "srt").lower(), "imdb": str(r.get("IDMovieImdb") or "") or None})
    # malentendants après (indications de bruits), puis les plus téléchargés
    out.sort(key=lambda s: (s["hi"], -s["downloads"]))
    return out


def _slug(title: str) -> str:
    from urllib.parse import quote
    return quote(re.sub(r"\s+", " ", title.strip().lower()), safe="")


def download(sub: dict, fetch_bytes=None) -> Path | None:
    """Le fichier du sous-titre en UTF-8 (gardé : le même n'est jamais retéléchargé)."""
    if not sub.get("link"):
        return None
    path = cache_dir() / f"os-{sub['id']}.{sub.get('format') or 'srt'}"
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        raw = (fetch_bytes or _bytes)(sub["link"])
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        text = strip_ads(decode(raw, sub.get("encoding") or ""))
        if len(text.strip()) < 20:
            return None
        path.write_text(text, encoding="utf-8")
        return path
    except Exception as e:  # noqa: BLE001
        log.info("Subtitle download failed: %s", e)
        return None


def _bytes(url: str) -> bytes:
    import httpx
    r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.content


def decode(raw: bytes, hint: str = "") -> str:
    """UTF-8 d'abord (avec ou sans BOM), puis l'encodage annoncé, puis les anciens encodages Windows courants."""
    for enc in ("utf-8-sig", hint, "cp1252", "cp1256", "cp1254", "latin-1"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


_AD = re.compile(r"opensubtitles|osdb\.link|subtitles by|advertise your product|sous-titres? (?:par|r[ée]alis[ée]s? par)|"
                 r"www\.|https?://", re.I)


def strip_ads(text: str) -> str:
    """Retire les blocs publicitaires (« Do you want subtitles… OpenSubtitles.com ») et renumérote."""
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    if not blocks or "-->" not in text:
        return text
    kept = []
    for b in blocks:
        lines = b.strip().splitlines()
        body = "\n".join(l for l in lines if "-->" not in l and not l.strip().isdigit())
        if "-->" in b and _AD.search(body):
            continue
        kept.append(lines)
    out = []
    for i, lines in enumerate(kept, 1):
        if lines and lines[0].strip().isdigit():
            lines = [str(i)] + lines[1:]
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def has_track(tracks: list[dict], lang: str) -> bool:
    names = SAME.get(lang, {lang})
    for t in tracks:
        if t.get("type") != "sub":
            continue
        tl = (t.get("lang") or "").lower()
        title = (t.get("title") or "").lower()
        if tl in names or any(n in title for n in names if len(n) > 3):
            return True
    return False


def prune(max_age_days: int = 30) -> None:
    limit = time.time() - max_age_days * 86400
    for p in cache_dir().glob("os-*"):
        try:
            if p.stat().st_mtime < limit:
                p.unlink()
        except OSError:
            pass


def key_for(item: dict) -> str:
    return hashlib.sha1(repr(sorted(item.items())).encode()).hexdigest()[:12]
