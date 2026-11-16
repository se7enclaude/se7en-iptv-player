"""Note, distribution et bande-annonce sur chaque fiche, même sans clé TMDB et quand le fournisseur ne les donne pas.

- Distribution (avec photos), réalisation / création et bande-annonce YouTube : Wikidata, à partir du numéro TMDB de
  l'œuvre (donné par le fournisseur ou appris par « Toutes les versions »). Gardé 30 jours.
- Note IMDb et nombre de votes : le fichier public des notes IMDb (≈ 8 Mo, un million et demi de titres), téléchargé
  en arrière-plan et rafraîchi toutes les deux semaines dans ``cache/imdb_ratings.db``.
Hors ligne, la fiche garde ce qu'elle avait.
"""
from __future__ import annotations

import gzip
import io
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..config import paths
from ..logging_setup import get_logger
from .store import Store

log = get_logger("stream.extras")

RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
RATINGS_MAX_AGE = 14 * 86400
_PROP = {"movie": "P4947", "series": "P4983"}
_lock = threading.Lock()
_loading = False


# ------------------------------------------------------------------ Wikidata
def facts(store: Store, typ: str, tmdb: str | int, fetch: Callable[[str], dict] | None = None) -> dict:
    """{"imdb", "cast": [{name, role, photo}], "director", "trailer"} d'une œuvre, d'après son numéro TMDB."""
    try:
        num = int(str(tmdb).strip())
    except (TypeError, ValueError):
        return {}
    key = f"facts:{typ}:{num}"
    row = store.one("SELECT data, fetched FROM wd_titles WHERE key=?", (key,))
    if row:
        data = json.loads(row["data"])
        if time.time() - row["fetched"] < (30 if data.get("found") else 1) * 86400:
            return data
    query = f"""SELECT ?imdb ?yt ?dirLabel ?castLabel ?img ?ord ?links WHERE {{
  ?m wdt:{_PROP.get(typ, 'P4947')} "{num}" .
  OPTIONAL {{ ?m wdt:P345 ?imdb }}
  OPTIONAL {{ ?m wdt:P1651 ?yt }}
  OPTIONAL {{ ?m wdt:{'P57' if typ == 'movie' else 'P170'} ?dir }}
  OPTIONAL {{ ?m p:P161 ?st . ?st ps:P161 ?cast . OPTIONAL {{ ?st pq:P1545 ?ord }} OPTIONAL {{ ?cast wdt:P18 ?img }} OPTIONAL {{ ?cast wikibase:sitelinks ?links }} }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "fr,en". }}
}} LIMIT 400"""
    try:
        from .versions import _sparql
        res = (fetch or _sparql)(query)
    except Exception as e:  # noqa: BLE001 — hors ligne : la dernière réponse, sinon rien
        log.info("Wikidata unavailable for %s: %s", key, e)
        return json.loads(row["data"]) if row else {}
    data = parse_facts(res)
    store.run("INSERT OR REPLACE INTO wd_titles VALUES (?,?,?)", (key, json.dumps(data, ensure_ascii=False), time.time()))
    return data


def parse_facts(res: dict) -> dict:
    imdb = trailer = None
    directors: list[str] = []
    cast: dict[str, dict] = {}
    order: list[str] = []
    for b in res.get("results", {}).get("bindings", []):
        v = {k: x.get("value") for k, x in b.items()}
        imdb = imdb or v.get("imdb")
        trailer = trailer or v.get("yt")
        d = v.get("dirLabel")
        if d and not re.fullmatch(r"Q\d+", d) and d not in directors:
            directors.append(d)
        name = v.get("castLabel")
        if name and not re.fullmatch(r"Q\d+", name):
            c = cast.setdefault(name, {"name": name, "role": "", "photo": "", "ord": 999, "links": 0})
            if name not in order:
                order.append(name)
            if v.get("img") and not c["photo"]:
                c["photo"] = commons_thumb(v["img"])
            if str(v.get("links") or "").isdigit():
                c["links"] = max(c["links"], int(v["links"]))
            if v.get("ord") and str(v["ord"]).isdigit():
                c["ord"] = min(c["ord"], int(v["ord"]))
    # ordre du générique quand Wikidata le donne, sinon les plus connus d'abord (nombre de pages Wikipédia)
    people = sorted(cast.values(), key=lambda c: (c["ord"], -c["links"], order.index(c["name"])))[:14]
    for c in people:
        c.pop("ord", None)
        c.pop("links", None)
    return {"found": bool(imdb or people or trailer), "imdb": imdb, "trailer": trailer,
            "director": ", ".join(directors[:3]), "cast": people}


def commons_thumb(url: str, width: int = 240) -> str:
    """Lien d'une image Wikimedia Commons en petite taille (photo d'acteur)."""
    url = url.replace("http://", "https://")
    return f"{url}?width={width}" if "Special:FilePath" in url else url


# ------------------------------------------------------------------ notes IMDb
def ratings_db() -> Path:
    return paths().cache / "imdb_ratings.db"


def imdb_rating(imdb: str | None, *, db: Path | None = None, refresh: bool = True) -> dict | None:
    """{"rating": 9.2, "votes": 2100000} pour « tt0068646 », ou None (fichier pas encore là, titre inconnu)."""
    m = re.fullmatch(r"tt(\d+)", (imdb or "").strip())
    if not m:
        return None
    path = db or ratings_db()
    if refresh and (not path.exists() or time.time() - path.stat().st_mtime > RATINGS_MAX_AGE):
        update_ratings_later()
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("SELECT rating, votes FROM r WHERE id=?", (int(m.group(1)),)).fetchone()
        finally:
            con.close()
    except sqlite3.Error as e:
        log.info("IMDb ratings unreadable: %s", e)
        return None
    return {"rating": row[0], "votes": row[1]} if row else None


def update_ratings_later() -> None:
    """Télécharge les notes IMDb en arrière-plan (une seule fois à la fois)."""
    global _loading
    with _lock:
        if _loading:
            return
        _loading = True

    def run():
        global _loading
        try:
            build_ratings(_download(RATINGS_URL), ratings_db())
        except Exception as e:  # noqa: BLE001
            log.info("IMDb ratings not updated: %s", e)
        finally:
            _loading = False
    threading.Thread(target=run, name="imdb-ratings", daemon=True).start()


def _download(url: str) -> bytes:
    import httpx
    r = httpx.get(url, timeout=120, follow_redirects=True, headers={"User-Agent": "se7en-iptv-player"})
    r.raise_for_status()
    return r.content


def build_ratings(gz: bytes, path: Path) -> int:
    """Fichier IMDb (tconst, averageRating, numVotes) → petite base SQLite ; remplace l'ancienne d'un coup."""
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    n = 0
    try:
        con.execute("CREATE TABLE r (id INTEGER PRIMARY KEY, rating REAL, votes INTEGER) WITHOUT ROWID")
        rows = []
        with gzip.open(io.BytesIO(gz), "rt", encoding="utf-8") as f:
            next(f, None)  # en-tête
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 3 or not p[0].startswith("tt"):
                    continue
                try:
                    rows.append((int(p[0][2:]), float(p[1]), int(p[2])))
                except ValueError:
                    continue
                if len(rows) >= 50000:
                    con.executemany("INSERT OR REPLACE INTO r VALUES (?,?,?)", rows)
                    n += len(rows)
                    rows = []
        con.executemany("INSERT OR REPLACE INTO r VALUES (?,?,?)", rows)
        n += len(rows)
        con.commit()
    finally:
        con.close()
    if n:
        tmp.replace(path)
    else:
        tmp.unlink(missing_ok=True)
    return n


# ------------------------------------------------------------------ fiche
def fill(store: Store, typ: str, d: dict, tmdb: Any, fetch: Callable[[str], dict] | None = None) -> dict:
    """Complète une fiche (après TMDB s'il y a une clé) : distribution, réalisation, bande-annonce, note IMDb."""
    f = facts(store, typ, tmdb, fetch) if tmdb else {}
    if f.get("cast") and not any(c.get("photo") for c in d.get("cast_list") or []):
        # la liste du fournisseur n'est qu'une suite de noms : celle de Wikidata a les photos
        d["cast_list"] = f["cast"] if len(f["cast"]) >= min(4, len(d.get("cast_list") or [])) else d.get("cast_list")
    if f.get("director") and not d.get("director"):
        d["director"] = f["director"]
    if f.get("trailer") and not d.get("trailer"):
        d["trailer"] = f["trailer"]
    imdb = f.get("imdb") or d.get("imdb")
    if imdb:
        d["imdb"] = imdb
        r = imdb_rating(imdb)
        if r:
            d["imdb_rating"], d["imdb_votes"] = r["rating"], r["votes"]
    return d
