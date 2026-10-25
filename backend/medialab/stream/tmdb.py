"""Fiches enrichies par TMDB (The Movie Database), seulement si l'utilisateur a mis sa propre clé dans les réglages.

Résumé en français, genres, durée, note, affiche et fond en grand, distribution avec photos, réalisateur, bande-annonce
YouTube et recommandations. Réponses gardées 7 jours dans la table ``tmdb`` ; la clé est chiffrée par Windows (DPAPI)
et n'apparaît jamais dans les journaux.
"""
from __future__ import annotations

import json
import time

import httpx

from .. import security
from ..errors import MediaLabError
from ..logging_setup import get_logger
from .store import Store
from .xtream import as_float, as_int, as_str

log = get_logger("stream")
API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
TTL = 7 * 86400


def save_key(store: Store, key: str) -> None:
    key = (key or "").strip()
    store.set_meta("tmdb_key", security.protect(key.encode()).hex() if key else None)


def get_key(store: Store) -> str:
    raw = store.get_meta("tmdb_key")
    if not raw:
        return ""
    try:
        return security.unprotect(bytes.fromhex(raw)).decode()
    except Exception:  # noqa: BLE001  (clé d'un autre compte Windows)
        return ""


def _client(key: str) -> tuple[httpx.Client, dict]:
    # clé « API » (v3, 32 caractères) en paramètre, jeton de lecture (v4, long) en en-tête
    if len(key) > 40:
        return httpx.Client(timeout=10, headers={"Authorization": f"Bearer {key}"}), {}
    return httpx.Client(timeout=10), {"api_key": key}


def _get(c: httpx.Client, auth: dict, path: str, **params) -> dict:
    r = c.get(API + path, params={**auth, "language": "fr-FR", **params})
    if r.status_code == 401:
        raise MediaLabError("tmdb_key", "La clé TMDB est refusée.", "Vérifie-la dans Réglages → Fiches enrichies.")
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json()


def check_key(key: str) -> bool:
    c, auth = _client(key)
    with c:
        try:
            return bool(_get(c, auth, "/configuration"))
        except MediaLabError:
            return False


def details(store: Store, kind: str, title: str, year: int | None, tmdb_id: str | int | None = None) -> dict | None:
    """Fiche TMDB d'un film (kind « movie ») ou d'une série (« tv ») ; None sans clé ou si rien ne correspond."""
    key = get_key(store)
    if not key:
        return None
    cache_key = f"{kind}:{tmdb_id or ''}:{title.lower()}:{year or ''}"
    row = store.one("SELECT data, fetched FROM tmdb WHERE key=?", (cache_key,))
    if row and time.time() - row["fetched"] < TTL:
        return json.loads(row["data"]) or None
    c, auth = _client(key)
    data: dict = {}
    try:
        with c:
            tid = as_int(tmdb_id)
            if not tid and title:
                found = _get(c, auth, f"/search/{kind}", query=title,
                             **({"year" if kind == "movie" else "first_air_date_year": year} if year else {}))
                res = found.get("results") or []
                if not res and year:  # année du fournisseur parfois fausse d'un an
                    res = _get(c, auth, f"/search/{kind}", query=title).get("results") or []
                tid = res[0]["id"] if res else None
            if tid:
                d = _get(c, auth, f"/{kind}/{tid}", append_to_response="credits,videos,recommendations",
                         include_video_language="fr,en,null")
                data = _shape(kind, d) if d else {}
    except MediaLabError:
        raise
    except Exception as e:  # noqa: BLE001  (réseau : la fiche du fournisseur suffit)
        log.warning("TMDB %s: %s", kind, type(e).__name__)
        return json.loads(row["data"]) if row else None
    store.run("INSERT OR REPLACE INTO tmdb VALUES (?,?,?)", (cache_key, json.dumps(data), time.time()))
    return data or None


def _img(path: str | None, size: str) -> str:
    return f"{IMG}{size}{path}" if path else ""


def _shape(kind: str, d: dict) -> dict:
    credits = d.get("credits") or {}
    cast = [{"name": as_str(p.get("name")), "role": as_str(p.get("character")), "photo": _img(p.get("profile_path"), "w185")}
            for p in (credits.get("cast") or [])[:14]]
    crew = credits.get("crew") or []
    director = ", ".join(as_str(p.get("name")) for p in crew if p.get("job") == "Director")[:120]
    if kind == "tv" and not director:
        director = ", ".join(as_str(p.get("name")) for p in (d.get("created_by") or []))[:120]
    vids = [v for v in ((d.get("videos") or {}).get("results") or []) if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")]
    vids.sort(key=lambda v: (v.get("iso_639_1") != "fr", v.get("type") != "Trailer"))
    runtime = as_int(d.get("runtime")) or as_int((d.get("episode_run_time") or [None])[0])
    date = as_str(d.get("release_date") or d.get("first_air_date"))
    return {
        "tmdb_id": d.get("id"), "title": as_str(d.get("title") or d.get("name")), "plot": as_str(d.get("overview")),
        "tagline": as_str(d.get("tagline")), "genres": [as_str(g.get("name")) for g in d.get("genres") or []],
        "runtime": runtime, "year": as_int(date[:4]) if len(date) >= 4 else None, "rating": as_float(d.get("vote_average")),
        "votes": as_int(d.get("vote_count")), "poster": _img(d.get("poster_path"), "w500"),
        "backdrop": _img(d.get("backdrop_path"), "w1280"), "cast": cast, "director": director,
        "trailer": as_str(vids[0].get("key")) if vids else "",
        "recommended": [{"title": as_str(r.get("title") or r.get("name")),
                         "year": as_int(as_str(r.get("release_date") or r.get("first_air_date"))[:4])}
                        for r in ((d.get("recommendations") or {}).get("results") or [])[:30]],
    }
