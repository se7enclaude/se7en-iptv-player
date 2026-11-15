"""Toutes les versions d'un même film ou d'une même série, dans toutes les langues et toutes les qualités.

Le catalogue range « |FR| Le Parrain », « |FR| Le Parrain (4K) », « |IT| Il padrino (BLURAY) », « |ES| El padrino »…
dans des catégories différentes, avec des titres différents. Ce qui les relie :

1. le numéro TMDB, quand le fournisseur le donne dans sa liste ou dans la fiche du film (``tmdb_id``) ;
2. sinon, les titres de l'œuvre dans toutes les langues (Wikidata, public et sans compte), comparés aux titres du
   catalogue débarrassés de leur langue et de leurs marques de qualité ; les candidats sont ensuite vérifiés par leur
   propre fiche (même numéro TMDB) quand le fournisseur en a une.

Chaque version a une langue (« |FR| », « [EN] », « TR: », ou la catégorie), une qualité (4K UHD, 1080p, 720p, SD) et
des marques (Blu-ray, HEVC, Light, VOST, Multi…). Calculé à chaque mise à jour du catalogue dans la table ``works``.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable

from ..logging_setup import get_logger
from . import quality as Q
from .store import Store, norm

log = get_logger("stream.versions")

TABLE = {"movie": "vod", "series": "series"}
VERIFY_SECONDS = 8

LANG_NAMES = {
    "fr": "Français", "en": "Anglais", "es": "Espagnol", "it": "Italien", "de": "Allemand", "pt": "Portugais",
    "tr": "Turc", "ar": "Arabe", "nl": "Néerlandais", "pl": "Polonais", "ru": "Russe", "ro": "Roumain", "el": "Grec",
    "sv": "Suédois", "no": "Norvégien", "da": "Danois", "fi": "Finnois", "hu": "Hongrois", "cs": "Tchèque",
    "sq": "Albanais", "sr": "Serbe", "bg": "Bulgare", "he": "Hébreu", "fa": "Persan", "hi": "Hindi", "ur": "Ourdou",
    "ko": "Coréen", "ja": "Japonais", "zh": "Chinois", "ku": "Kurde", "so": "Somali", "af": "Afrikaans", "uk": "Ukrainien",
    "multi": "Multilingue",
}
# jeton de langue des fournisseurs → langue (les pays comptent : « UK », « US », « BR », « LAT »…)
CODES = {
    "FR": "fr", "FRA": "fr", "FRE": "fr", "FRENCH": "fr", "VF": "fr", "VFF": "fr", "VFQ": "fr", "QC": "fr", "TRUEFRENCH": "fr",
    "BE": "fr", "CH": "fr", "EN": "en", "ENG": "en", "UK": "en", "US": "en", "GB": "en", "CA": "en", "AU": "en",
    "ES": "es", "ESP": "es", "SPA": "es", "LAT": "es", "LATINO": "es", "MX": "es", "AR": "ar", "ARA": "ar",
    "ARAB": "ar", "ARABIC": "ar", "IT": "it", "ITA": "it", "DE": "de", "GER": "de", "DEU": "de", "AT": "de",
    "TR": "tr", "TUR": "tr", "PT": "pt", "POR": "pt", "BR": "pt", "NL": "nl", "PL": "pl", "RU": "ru", "RO": "ro",
    "GR": "el", "SE": "sv", "NO": "no", "DK": "da", "FI": "fi", "HU": "hu", "CZ": "cs", "AL": "sq", "EXYU": "sr",
    "EX-YU": "sr", "RS": "sr", "BG": "bg", "IL": "he", "IR": "fa", "IN": "hi", "HI": "hi", "PK": "ur", "KR": "ko",
    "JP": "ja", "CN": "zh", "KU": "ku", "KURD": "ku", "SO": "so", "UA": "uk", "MULTI": "multi", "MULTISUB": "multi",
}
_PREFIX = re.compile(r"^\s*[\|\[\(«]?\s*([A-Za-z]{2,10}(?:-[A-Za-z]{2,3})?)\s*(?:[\|\]\)»:]|\s-\s|-\s)\s*")
# marques gardées pour l'affichage (en plus de la qualité 4K / 1080p / 720p / SD)
TAGS = (
    ("Blu-ray", {"bluray", "blu-ray", "bdrip", "brrip", "bd"}), ("Remux", {"remux"}), ("HEVC", {"hevc", "h265", "x265"}),
    ("HDR", {"hdr", "hdr10", "hdr10+"}), ("Dolby Vision", {"dv", "dovi"}), ("Light", {"light"}), ("3D", {"3d"}),
    ("IMAX", {"imax"}), ("VOST", {"vost", "vostfr", "vosta", "subbed"}), ("Multi", {"multi", "multisub"}),
)
_TAG_WORDS = {w for _l, ws in TAGS for w in ws} | {t for _l, _r, toks in Q.LEVELS for t in toks} | Q.NOISE | {
    "vf", "vff", "vfq", "vo", "truefrench", "french", "sub", "subs", "dual", "audio", "web", "webrip", "webdl", "web-dl",
    "hdrip", "dvdrip", "cam", "ts", "4k+", "uhd+", "new", "nouveau"}
QUALITY = {4: "4K UHD", 3: "1080p", 2: "720p", 1: "SD"}


def lang_of(text: str) -> str:
    """« |FR| Le Parrain » → « fr » ; « » si le texte ne commence pas par une langue."""
    m = _PREFIX.match(text or "")
    if m:
        return CODES.get(m.group(1).upper().replace(" ", ""), "")
    return ""


def strip_lang(text: str) -> str:
    m = _PREFIX.match(text or "")
    if m and m.group(1).upper() in CODES:
        return text[m.end():]
    return text or ""


def title_key(text: str) -> str:
    """Titre comparable : sans langue, sans année ni marques de qualité, minuscules, sans accents ni ponctuation.
    « |FR| Le Parrain (4K) » et Wikidata « Le Parrain » → « le parrain »."""
    s = strip_lang(text)
    s = re.sub(r"[\(\[][^\)\]]{0,30}[\)\]]", " ", s)          # (4K), [MULTI], (2008)
    s = re.sub(r"\|\s*(?:19|20)\d{2}\s*$", " ", s)             # « | 2008 » en fin de nom
    s = re.sub(r"\b(?:19|20)\d{2}\s*$", " ", s.strip())        # année en fin de nom
    words = [w for w in re.split(r"[^\w+]+", norm(s)) if w]
    words = [w for w in words if w not in _TAG_WORDS]
    return " ".join(words)


def year_of(name: str, year: int | None) -> int | None:
    if year:
        return year
    m = re.search(r"(?:\(|\|\s*|\s)((?:19|20)\d{2})\)?\s*(?:\(|$|\|)", name or "")
    return int(m.group(1)) if m else None


def tags_of(text: str) -> list[str]:
    words = set(re.split(r"[^\w+\-]+", norm(text)))
    words |= {w.replace("-", "") for w in words}
    return [label for label, toks in TAGS if words & toks]


def describe(name: str, category: str) -> dict:
    """Langue, qualité et marques d'une version, d'après son nom puis sa catégorie."""
    lang = lang_of(name) or lang_of(category)
    rank = Q.parse(name)[2] or Q.parse(category)[2]
    tags = tags_of(name)
    for t in tags_of(category):
        if t not in tags and t not in ("VOST", "Multi"):
            tags.append(t)
    return {"lang": lang, "rank": rank, "tags": tags}


# ------------------------------------------------------------------ index (à chaque mise à jour du catalogue)
def index(store: Store, aid: str, typ: str, tmdb: dict[str, str] | None = None) -> int:
    """(Re)calcule ``works`` pour un compte. ``tmdb`` : numéros donnés par la liste du fournisseur ; ceux appris
    depuis (fiches ouvertes) sont gardés."""
    if typ not in TABLE:
        return 0
    known = {r["id"]: r["tmdb"] for r in store.q("SELECT id, tmdb FROM works WHERE account=? AND type=? AND tmdb IS NOT NULL",
                                                   (aid, typ))}
    if tmdb:
        known.update({k: v for k, v in tmdb.items() if v})
    cats = {r["id"]: r["name"] for r in store.q("SELECT id, name FROM categories WHERE account=? AND type=?", (aid, typ))}
    rows = store.q(f"SELECT id, name, category, year FROM {TABLE[typ]} WHERE account=?", (aid,))
    out = []
    for r in rows:
        d = describe(r["name"], cats.get(r["category"]) or "")
        out.append((aid, typ, r["id"], known.get(r["id"]), title_key(r["name"]), d["lang"], json.dumps(d["tags"]),
                    year_of(r["name"], r["year"]), d["rank"]))
    with store.write_lock:
        con = store.conn()
        con.execute("BEGIN")
        con.execute("DELETE FROM works WHERE account=? AND type=?", (aid, typ))
        con.executemany("INSERT OR REPLACE INTO works VALUES (?,?,?,?,?,?,?,?,?)", out)
        con.commit()
    return len(out)


def ensure(store: Store, aid: str, typ: str) -> None:
    """Catalogue synchronisé avant cette version : l'index est fait à la première demande."""
    if not store.one("SELECT 1 AS x FROM works WHERE account=? AND type=? LIMIT 1", (aid, typ)) and \
            store.one(f"SELECT 1 AS x FROM {TABLE[typ]} WHERE account=? LIMIT 1", (aid,)):
        index(store, aid, typ)


def remember_tmdb(store: Store, aid: str, typ: str, item: str, tmdb: Any) -> None:
    t = str(tmdb or "").strip()
    if typ in TABLE and t.isdigit() and t != "0":
        store.run("UPDATE works SET tmdb=? WHERE account=? AND type=? AND id=?", (t, aid, typ, item))


# ------------------------------------------------------------------ titres de l'œuvre dans toutes les langues
WIKIDATA = "https://query.wikidata.org/sparql"
_PROP = {"movie": "P4947", "series": "P4983"}  # identifiant TMDB (film, série) sur Wikidata


def work_info(store: Store, typ: str, tmdb: str, fetch: Callable[[str], dict] | None = None) -> dict:
    """Titres (toutes langues) et identifiant IMDb d'une œuvre, gardés 30 jours (1 jour si Wikidata ne répond pas)."""
    key = f"wd:{typ}:{tmdb}"
    row = store.one("SELECT data, fetched FROM wd_titles WHERE key=?", (key,))
    if row:
        data = json.loads(row["data"])
        if time.time() - row["fetched"] < (30 if data.get("titles") else 1) * 86400:
            return data
    query = (f'SELECT ?l ?imdb WHERE {{ ?m wdt:{_PROP[typ]} "{int(tmdb)}" . OPTIONAL {{ ?m wdt:P345 ?imdb }} '
             "{ ?m rdfs:label ?l } UNION { ?m skos:altLabel ?l } UNION { ?m wdt:P1476 ?l } }")
    data: dict = {"titles": [], "imdb": None}
    try:
        res = (fetch or _sparql)(query)
        titles, imdb = set(), None
        for b in res.get("results", {}).get("bindings", []):
            if b.get("l", {}).get("value"):
                titles.add(b["l"]["value"])
            if b.get("imdb", {}).get("value"):
                imdb = b["imdb"]["value"]
        data = {"titles": sorted(titles), "imdb": imdb}
    except Exception as e:  # noqa: BLE001 — hors ligne : on garde ce qu'on a
        log.info("Wikidata unavailable for %s: %s", key, e)
        if row:
            return json.loads(row["data"])
    store.run("INSERT OR REPLACE INTO wd_titles VALUES (?,?,?)", (key, json.dumps(data, ensure_ascii=False), time.time()))
    return data


def _sparql(query: str) -> dict:
    import httpx
    r = httpx.get(WIKIDATA, params={"query": query, "format": "json"}, timeout=15,
                  headers={"User-Agent": "se7en-iptv-player/1.2 (https://github.com/se7enclaude/se7en-iptv-player)",
                           "Accept": "application/sparql-results+json"})
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------ toutes les versions d'un titre
def versions(store: Store, aid: str, typ: str, item: str, *, tmdb_of: Callable[[str], str | None] | None = None,
             details_of: Callable[[str], dict] | None = None, fetch: Callable[[str], dict] | None = None,
             pref_langs: list[str] | None = None, show_adult: bool = True, verify_limit: int = 30) -> dict:
    """Les versions de ``item`` dans toutes les langues. ``tmdb_of(id)`` lit (et met en cache) le numéro TMDB d'une
    version depuis sa fiche chez le fournisseur ; ``details_of(id)`` sa fiche gardée (pour la résolution réelle)."""
    ensure(store, aid, typ)
    me = store.one("SELECT * FROM works WHERE account=? AND type=? AND id=?", (aid, typ, item))
    if not me:
        return {"tmdb": None, "versions": []}
    tmdb = me["tmdb"]
    if not tmdb and me["tkey"]:
        # « Le Parrain (4K) » sans fiche : « Le Parrain » (même titre, même langue) a un numéro → c'est le même film
        same = {r["tmdb"] for r in store.q("SELECT tmdb, year FROM works WHERE account=? AND type=? AND tkey=? AND lang=? "
                                           "AND tmdb IS NOT NULL", (aid, typ, me["tkey"], me["lang"]))
                if not (me["year"] and r["year"] and abs(me["year"] - r["year"]) > 1)}
        if len(same) == 1:
            tmdb = same.pop()
    if not tmdb and tmdb_of:
        tmdb = _within(tmdb_of, item, VERIFY_SECONDS)
        if tmdb:
            remember_tmdb(store, aid, typ, item, tmdb)
    ids: dict[str, bool] = {item: True}  # id → vérifié (même numéro TMDB)
    keys = {me["tkey"]} if me["tkey"] else set()
    imdb = None
    if tmdb:
        for r in store.q("SELECT id FROM works WHERE account=? AND type=? AND tmdb=?", (aid, typ, tmdb)):
            ids[r["id"]] = True
        info = work_info(store, typ, tmdb, fetch)
        imdb = info.get("imdb")
        # titres trop courts (« Pi », « Baba ») : seulement s'ils sont aussi vérifiés par la fiche (plus bas)
        keys |= {k for k in (title_key(t) for t in info.get("titles", [])) if len(k) >= 4}
    if keys:
        ks = sorted(keys)
        cand = store.q(f"SELECT id, tmdb, year FROM works WHERE account=? AND type=? AND tkey IN ({','.join('?' * len(ks))})",
                       [aid, typ, *ks])
        unchecked = []
        for c in cand:
            if c["id"] in ids:
                continue
            if c["tmdb"]:
                if tmdb and c["tmdb"] != tmdb:
                    continue  # une autre œuvre du même titre (remake)
                if not tmdb:
                    ids[c["id"]] = False
                continue
            if me["year"] and c["year"] and abs(me["year"] - c["year"]) > 1:
                continue
            unchecked.append(c["id"])
        if tmdb and tmdb_of and unchecked:
            # vérification par la fiche de chaque candidat (en parallèle, avec une limite)
            # au plus VERIFY_SECONDS : un fournisseur lent ou injoignable ne bloque pas la liste (non vérifiés)
            ex = ThreadPoolExecutor(max_workers=6)
            futs = {ex.submit(_safe(tmdb_of), cid): cid for cid in unchecked[:verify_limit]}
            done, _late = wait(futs, timeout=VERIFY_SECONDS)
            ex.shutdown(wait=False, cancel_futures=True)
            got = {futs[f]: f.result() for f in done}
            for cid, t in got.items():
                if t:
                    remember_tmdb(store, aid, typ, cid, t)
                if t == tmdb:
                    ids[cid] = True
            for cid in unchecked:
                if got.get(cid) in (None, ""):
                    ids.setdefault(cid, False)  # pas de fiche (ou trop lente) : même titre, même année → « non vérifié »
        else:
            for cid in unchecked:
                ids.setdefault(cid, False)
    return {"tmdb": tmdb, "imdb": imdb, "versions": _shape(store, aid, typ, item, ids, details_of, pref_langs, show_adult)}


def _within(fn: Callable[[str], str | None], arg: str, seconds: float) -> str | None:
    ex = ThreadPoolExecutor(max_workers=1)
    f = ex.submit(_safe(fn), arg)
    done, _ = wait([f], timeout=seconds)
    ex.shutdown(wait=False, cancel_futures=True)
    return f.result() if done else None


def _safe(fn: Callable[[str], str | None]) -> Callable[[str], str | None]:
    def run(x: str) -> str | None:
        try:
            return fn(x)
        except Exception:  # noqa: BLE001
            return None
    return run


def _shape(store: Store, aid: str, typ: str, item: str, ids: dict[str, bool], details_of, pref_langs, show_adult) -> list[dict]:
    if not ids:
        return []
    table = TABLE[typ]
    q = ",".join("?" * len(ids))
    adult = "" if show_adult else (" AND COALESCE(t.category, '') NOT IN (SELECT id FROM categories WHERE account=? "
                                   "AND type=? AND adult=1)")
    rows = store.q(f"SELECT t.id, t.name, t.logo, t.year, t.position, c.name AS category, w.lang, w.rank, w.tags "
                   f"FROM {table} t JOIN works w ON w.account=t.account AND w.type=? AND w.id=t.id "
                   f"LEFT JOIN categories c ON c.account=t.account AND c.type=? AND c.id=t.category "
                   f"WHERE t.account=? AND t.id IN ({q})" + adult,
                   [typ, typ, aid, *ids] + ([] if show_adult else [aid, typ]))
    pref = [p for p in (pref_langs or []) if p]
    out = []
    for r in rows:
        video = _video(details_of(r["id"]) if details_of else None)
        rank = r["rank"] or video.get("rank") or 0
        out.append({"id": r["id"], "name": r["name"], "logo": r["logo"], "year": r["year"], "category": r["category"],
                    "lang": r["lang"] or "", "lang_label": LANG_NAMES.get(r["lang"] or "", "Autre langue"),
                    "quality": QUALITY.get(rank, ""), "rank": rank, "tags": json.loads(r["tags"] or "[]"),
                    "video": video.get("label", ""), "current": r["id"] == item, "verified": ids.get(r["id"], False),
                    "_pos": r["position"] or 0})
    langs_order = {l: i for i, l in enumerate(pref)}
    out.sort(key=lambda v: (langs_order.get(v["lang"], len(pref) + (1 if v["lang"] else 2)), v["lang_label"], -v["rank"],
                            len(v["tags"]), v["_pos"]))
    for v in out:
        v.pop("_pos", None)
    return out


def _video(info: dict | None) -> dict:
    """« 3840×2160 · HEVC · 18 Mb/s » depuis la fiche du fournisseur (quand il la donne)."""
    if not info:
        return {}
    v = info.get("video") if isinstance(info.get("video"), dict) else {}
    w, h = v.get("width"), v.get("height")
    parts, rank = [], 0
    try:
        w, h = int(w), int(h)
        parts.append(f"{w}×{h}")
        rank = 4 if w >= 3000 or h >= 1800 else 3 if w >= 1700 or h >= 1000 else 2 if w >= 1200 or h >= 700 else 1
    except (TypeError, ValueError):
        pass
    codec = str(v.get("codec_name") or "").lower()
    if codec:
        parts.append({"hevc": "HEVC", "h264": "H.264", "av1": "AV1", "mpeg2video": "MPEG-2"}.get(codec, codec.upper()))
    try:
        br = int(info.get("bitrate") or 0)
        if br > 0:
            parts.append(f"{br / 1000:.1f} Mb/s" if br >= 1000 else f"{br} kb/s")
    except (TypeError, ValueError):
        pass
    return {"label": " · ".join(parts), "rank": rank}

