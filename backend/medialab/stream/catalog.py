"""Lectures du catalogue local pour l'interface : catégories, listes paginées, recherche, guide, favoris, reprise.

Contrôle parental : les catégories « adultes » (détectées par leur nom) et tout ce qu'elles contiennent sont cachés
tant que le profil n'est pas déverrouillé par son code ; un profil « enfant » ne les voit jamais.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from ..errors import MediaLabError
from . import quality as Q
from .store import Store, norm
from .xtream import XtreamClient, as_float, as_int, as_str

TABLE = {"live": "channels", "movie": "vod", "series": "series"}


def _adult_filter(show_adult: bool) -> str:
    return "" if show_adult else (" AND NOT EXISTS (SELECT 1 FROM categories k WHERE k.account=t.account AND k.type=? "
                                  "AND k.id=t.category AND k.adult=1)")


def categories(store: Store, aid: str, typ: str, profile: str, show_adult: bool) -> list[dict]:
    table = TABLE[typ]
    rows = store.q(
        f"SELECT c.id, c.name, c.adult, (SELECT COUNT(*) FROM {table} t WHERE t.account=c.account AND t.category=c.id) AS n "
        "FROM categories c WHERE c.account=? AND c.type=? " + ("" if show_adult else "AND c.adult=0 ") + "ORDER BY c.position",
        (aid, typ))
    fav = store.one("SELECT COUNT(*) AS n FROM favorites WHERE profile=? AND account=? AND type=?", (profile, aid, typ))["n"]
    total = store.one(f"SELECT COUNT(*) AS n FROM {table} t WHERE t.account=?" + _adult_filter(show_adult),
                      (aid,) if show_adult else (aid, typ))["n"]
    special = [{"id": "__all", "name": "Tout", "n": total, "adult": 0}, {"id": "__fav", "name": "Favoris", "n": fav, "adult": 0}]
    if typ != "live":
        special.append({"id": "__recent", "name": "Ajouts récents", "n": min(total, 200), "adult": 0})
    return special + [r for r in rows if r["n"]]


def items(store: Store, aid: str, typ: str, profile: str, show_adult: bool, category: str = "__all",
          search: str = "", offset: int = 0, limit: int = 200, sort: str = "default", quality: str = "",
          only: list[str] | None = None) -> dict:
    """Une page de la liste. ``quality`` (« best », « fhd », « hd »…) : une seule version par groupe SD / HD / FHD / 4K,
    la plus proche de cette préférence, avec les autres dans ``variants`` ; vide = toutes les versions à la suite."""
    table = TABLE[typ]
    where = ["t.account=?"]
    params: list[Any] = [aid]
    if category == "__fav":
        where.append("t.id IN (SELECT item FROM favorites WHERE profile=? AND account=? AND type=?)")
        params += [profile, aid, typ]
    elif category not in ("__all", "__recent", ""):
        where.append("t.category=?")
        params.append(category)
    if only is not None:  # résultats de la recherche intelligente (search.py)
        where.append(f"t.id IN ({','.join('?' * len(only))})" if only else "0")
        params += only
    if search:
        for word in norm(search).split():
            where.append("t.norm LIKE ?")
            params.append(f"%{word}%")
    if not show_adult:
        where.append("NOT EXISTS (SELECT 1 FROM categories k WHERE k.account=t.account AND k.type=? AND k.id=t.category AND k.adult=1)")
        params.append(typ)
    where.append("t.id NOT IN (SELECT item FROM hidden WHERE profile=? AND account=? AND type=?)")
    params += [profile, aid, typ]
    order = "t.position"
    if category == "__fav":
        order = "(SELECT position FROM favorites f WHERE f.profile=? AND f.account=t.account AND f.type=? AND f.item=t.id)"
    elif category == "__recent" or sort == "recent":
        order = "t.added DESC"
    elif sort == "name":
        order = "t.norm"
    elif sort == "rating" and typ != "live":
        order = "t.rating DESC"
    elif sort == "year" and typ != "live":
        order = "t.year DESC"
    sql_where = " AND ".join(where)
    order_params = [profile, typ] if category == "__fav" else []
    lim = 200 if category == "__recent" else limit
    if quality:
        Q.ensure(store, aid, quality)
        # une version par groupe (calculée d'avance par quality.index) ; les favoris restent ceux que tu as choisis
        flag = None if category == "__fav" else ("rep_all" if category in ("__all", "__recent", "") else "rep_cat")
        src = (f"FROM {table} t LEFT JOIN variants v ON v.account=t.account AND v.type=? AND v.id=t.id WHERE {sql_where}"
               + (f" AND (v.id IS NULL OR v.{flag}=1)" if flag else ""))
        gparams = [typ] + params
        total = store.one(f"SELECT COUNT(*) AS n {src}", gparams)["n"]
        if order == "t.position" and flag:
            order = f"COALESCE(v.pos_{flag[4:]}, t.position), t.position"
        rows = store.q(f"SELECT t.*, v.grp, v.quality {src} ORDER BY {order} LIMIT ? OFFSET ?",
                       gparams + order_params + [lim, offset])
        _attach_variants(store, aid, typ, profile, rows)
    else:
        total = store.one(f"SELECT COUNT(*) AS n FROM {table} t WHERE {sql_where}", params)["n"]
        rows = store.q(f"SELECT t.* FROM {table} t WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
                       params + order_params + [lim, offset])
    favs = {r["item"] for r in store.q("SELECT item FROM favorites WHERE profile=? AND account=? AND type=?", (profile, aid, typ))}
    for r in rows:
        for k in ("norm", "url", "grp", "rank", "rn"):  # les adresses (avec identifiants) ne sortent jamais vers l'interface
            r.pop(k, None)
        r["fav"] = r["id"] in favs
    if typ == "live" and rows:
        _attach_now(store, aid, rows)
    if typ != "live" and rows:
        hist = {h["item"]: h for h in store.q(
            f"SELECT item, position, duration, watched FROM history WHERE profile=? AND account=? AND type=? "
            f"AND item IN ({','.join('?' * len(rows))})", [profile, aid, typ] + [r["id"] for r in rows])}
        for r in rows:
            h = hist.get(r["id"])
            r["progress"] = (h["position"] / h["duration"]) if h and h["duration"] else 0
    return {"total": total, "items": rows}


def _attach_variants(store: Store, aid: str, typ: str, profile: str, rows: list[dict]) -> None:
    """Les autres versions (qualités) de chaque élément de la page, de la meilleure à la moins bonne."""
    groups = {r["grp"] for r in rows if r.get("grp")}
    if not groups:
        return
    table = TABLE[typ]
    found: dict[str, list[dict]] = {}
    for v in store.q(f"SELECT v.grp, v.id, v.quality, v.rank, t.name FROM variants v JOIN {table} t ON t.account=v.account "
                     f"AND t.id=v.id WHERE v.account=? AND v.type=? AND v.grp IN ({','.join('?' * len(groups))}) "
                     "AND v.id NOT IN (SELECT item FROM hidden WHERE profile=? AND account=? AND type=?) "
                     "ORDER BY v.rank DESC, t.position", [aid, typ, *groups, profile, aid, typ]):
        found.setdefault(v["grp"], []).append({"id": v["id"], "quality": v["quality"], "name": v["name"]})
    for r in rows:
        vs = found.get(r.get("grp") or "", [])
        r["variants"] = vs if len(vs) > 1 else []


def group_ids(store: Store, aid: str, typ: str, profile: str, show_adult: bool, category: str, quality: str) -> list[str]:
    """Les éléments d'une catégorie dans l'ordre de la liste (une version par groupe) : pour zapper ↑ ↓."""
    return [r["id"] for r in items(store, aid, typ, profile, show_adult, category, limit=100_000, quality=quality)["items"]]


def group_of(store: Store, aid: str, typ: str, item: str) -> list[str]:
    """Toutes les versions de l'élément (lui compris)."""
    g = store.one("SELECT grp FROM variants WHERE account=? AND type=? AND id=?", (aid, typ, item))
    if not g:
        return [item]
    return [r["id"] for r in store.q("SELECT id FROM variants WHERE account=? AND type=? AND grp=?", (aid, typ, g["grp"]))]


def _attach_now(store: Store, aid: str, rows: list[dict]) -> None:
    now = int(time.time())
    ids = [r["epg_id"] for r in rows if r.get("epg_id")]
    if not ids:
        return
    progs = store.q(f"SELECT channel, start, stop, title FROM epg WHERE account=? AND channel IN ({','.join('?' * len(ids))}) "
                    "AND stop>? AND start<? ORDER BY start", [aid] + ids + [now, now + 6 * 3600])
    nowmap: dict[str, dict] = {}
    nextmap: dict[str, dict] = {}
    for p in progs:
        if p["start"] <= now < p["stop"]:
            nowmap.setdefault(p["channel"], p)
        elif p["start"] >= now and p["channel"] not in nextmap:
            nextmap[p["channel"]] = p
    for r in rows:
        r["now"] = nowmap.get(r.get("epg_id") or "")
        r["next"] = nextmap.get(r.get("epg_id") or "")


def guide(store: Store, aid: str, profile: str, show_adult: bool, category: str, start: int, hours: int = 4,
          offset: int = 0, limit: int = 60, quality: str = "") -> dict:
    page = items(store, aid, "live", profile, show_adult, category, offset=offset, limit=limit, quality=quality)
    ids = [r["epg_id"] for r in page["items"] if r.get("epg_id")]
    end = start + hours * 3600
    progs: dict[str, list] = {}
    if ids:
        for p in store.q(f"SELECT channel, start, stop, title, descr, category FROM epg WHERE account=? AND channel IN "
                         f"({','.join('?' * len(ids))}) AND stop>? AND start<? ORDER BY start", [aid] + ids + [start, end]):
            progs.setdefault(p["channel"], []).append(p)
    for r in page["items"]:
        r["programmes"] = progs.get(r.get("epg_id") or "", [])
    return {"start": start, "end": end, "total": page["total"], "channels": page["items"]}


def channel_epg(store: Store, aid: str, channel_id: str, days_back: int = 7, days_fwd: int = 2) -> list[dict]:
    ch = store.one("SELECT epg_id FROM channels WHERE account=? AND id=?", (aid, channel_id))
    if not ch or not ch["epg_id"]:
        return []
    now = int(time.time())
    return store.q("SELECT start, stop, title, descr, category FROM epg WHERE account=? AND channel=? AND stop>? AND start<? "
                   "ORDER BY start", (aid, ch["epg_id"], now - days_back * 86400, now + days_fwd * 86400))


def search_all(store: Store, aid: str, profile: str, show_adult: bool, query: str, limit: int = 30,
               programmes_only: bool = False) -> dict:
    out = {} if programmes_only else {typ: items(store, aid, typ, profile, show_adult, "__all", query, 0, limit)["items"]
                                      for typ in TABLE}
    words = norm(query).split()
    if words:
        now = int(time.time())
        where = " AND ".join("e.norm LIKE ?" for _ in words)
        out["programmes"] = store.q(
            f"SELECT e.channel, e.start, e.stop, e.title, c.id AS channel_id, c.name AS channel_name, c.logo FROM epg e "
            f"JOIN channels c ON c.account=e.account AND c.epg_id=e.channel WHERE e.account=? AND {where} AND e.stop>? "
            "GROUP BY e.channel, e.start ORDER BY e.start LIMIT ?", [aid] + [f"%{w}%" for w in words] + [now - 7 * 86400, limit])
    else:
        out["programmes"] = []
    return out


# ------------------------------------------------------------------ fiches (films, séries)
def vod_info(store: Store, client: XtreamClient | None, aid: str, vid: str) -> dict:
    row = store.one("SELECT * FROM vod WHERE account=? AND id=?", (aid, vid))
    if not row:
        raise MediaLabError("not_found", "Ce film n'existe plus dans le catalogue.")
    row.pop("url", None)
    row.pop("norm", None)
    # la fiche du fournisseur, gardée 3 jours (ouverture instantanée la fois suivante, recherche par acteur / genre)
    cached = store.one("SELECT data, fetched FROM details WHERE account=? AND type='movie' AND id=?", (aid, vid))
    i: dict = json.loads(cached["data"]) if cached else {}
    if client is not None and (not cached or time.time() - cached["fetched"] > 3 * 86400):
        try:
            i = client.vod_info(vid).get("info") or {}
            store.run("INSERT OR REPLACE INTO details VALUES (?,?,?,?,?)", (aid, "movie", vid, json.dumps(i), time.time()))
        except MediaLabError:
            pass
    info = {"plot": as_str(i.get("plot") or i.get("description")), "cast": as_str(i.get("cast") or i.get("actors")),
            "director": as_str(i.get("director")), "genre": as_str(i.get("genre")),
            "duration": as_str(i.get("duration")), "duration_s": as_int(i.get("duration_secs")),
            "rating": as_float(i.get("rating")), "release": as_str(i.get("releasedate") or i.get("release_date")),
            "backdrop": (i.get("backdrop_path") or [None])[0] if isinstance(i.get("backdrop_path"), list) else as_str(i.get("backdrop_path")),
            "trailer": as_str(i.get("youtube_trailer")), "poster": as_str(i.get("movie_image") or i.get("cover_big")),
            "tmdb_id": as_str(i.get("tmdb_id"))}
    if i:
        _remember_people(store, aid, "movie", vid, info["genre"], info["cast"], info["director"])
    return {**row, **{k: v for k, v in info.items() if v not in (None, "")}}


def _remember_people(store: Store, aid: str, typ: str, item: str, genre: str, cast: str, director: str) -> None:
    if genre or cast or director:
        store.run("INSERT OR REPLACE INTO people VALUES (?,?,?,?,?,?)", (aid, typ, item, norm(genre), norm(cast), norm(director)))


_TAGS = r"\b(4K|UHD|FHD|HD|SD|HEVC|H265|1080p|720p|MULTI|VOSTFR|VFF|VFQ|VF|VO|TRUEFRENCH)\b"


def search_title(name: str) -> tuple[str, int | None]:
    """Titre et année à chercher sur TMDB : « FR | Dune: Part Two (2024) 4K » → (« Dune: Part Two », 2024)."""
    s = re.sub(r"^\s*(?:\[[^\]]{1,6}\]|\|?[A-Za-z]{2,3}\s*[|:\-]+)\s*", "", name or "")  # « FR | », « [VF] », « EN - »
    s = re.sub(_TAGS, "", s, flags=re.I).strip()
    year = None
    m = re.search(r"[\(\[]?((?:19|20)\d{2})[\)\]]?\s*$", s)
    if m:
        year = int(m.group(1))
        s = s[:m.start()]
    return " ".join(s.replace("|", " ").split()).strip(" -:"), year


def enrich(store: Store, aid: str, typ: str, d: dict, profile: str, show_adult: bool, quality: str) -> dict:
    """Ajoute à une fiche : genres, distribution, versions SD / HD / 4K, titres similaires du catalogue, et TMDB si
    une clé est réglée (résumé en français, photos des acteurs, fond, bande-annonce, recommandations)."""
    from . import tmdb
    title, year = search_title(d.get("name") or "")
    tm = None
    try:
        tm = tmdb.details(store, "movie" if typ == "movie" else "tv", title, d.get("year") or year, d.get("tmdb_id"))
    except MediaLabError as e:
        d["tmdb_error"] = e.message
    genres = [g.strip() for g in re.split(r"[,/|]", d.get("genre") or "") if g.strip()]
    cast = [{"name": c.strip(), "role": "", "photo": ""} for c in re.split(r"[,/]", d.get("cast") or "") if c.strip()][:14]
    if tm:
        d["tmdb"] = True
        d["plot"] = tm["plot"] or d.get("plot")
        d["tagline"] = tm["tagline"]
        genres = tm["genres"] or genres
        cast = tm["cast"] or cast
        d["director"] = tm["director"] or d.get("director")
        d["backdrop"] = tm["backdrop"] or d.get("backdrop")
        d["poster"] = d.get("poster") or tm["poster"]
        d["rating"] = d.get("rating") or tm["rating"]
        d["year"] = d.get("year") or tm["year"]
        d["trailer"] = d.get("trailer") or tm["trailer"]
        if tm["runtime"] and not d.get("duration"):
            d["duration"] = f"{tm['runtime'] // 60} h {tm['runtime'] % 60:02d}" if tm["runtime"] >= 60 else f"{tm['runtime']} min"
    d["genres"] = genres
    d["cast_list"] = cast
    d["variants"] = variants_of(store, aid, typ, d["id"])
    d["similar"] = similar(store, aid, typ, d, profile, show_adult, quality, (tm or {}).get("recommended") or [], genres)
    return d


def variants_of(store: Store, aid: str, typ: str, item: str) -> list[dict]:
    g = store.one("SELECT grp FROM variants WHERE account=? AND type=? AND id=?", (aid, typ, item))
    if not g:
        return []
    rows = store.q(f"SELECT v.id, v.quality, t.name FROM variants v JOIN {TABLE[typ]} t ON t.account=v.account AND t.id=v.id "
                   "WHERE v.account=? AND v.type=? AND v.grp=? ORDER BY v.rank DESC, t.position", (aid, typ, g["grp"]))
    return rows if len(rows) > 1 else []


def similar(store: Store, aid: str, typ: str, d: dict, profile: str, show_adult: bool, quality: str,
            recommended: list[dict], genres: list[str], limit: int = 14) -> list[dict]:
    """Titres du catalogue proches : d'abord les recommandations TMDB qu'on a, puis même genre, puis même catégorie."""
    from . import quality as Q
    out: list[dict] = []
    seen = {Q.parse(d.get("name") or "")[0]}
    hidden = {r["item"] for r in store.q("SELECT item FROM hidden WHERE profile=? AND account=? AND type=?", (profile, aid, typ))}

    def add(rows: list[dict]) -> None:
        for r in rows:
            k = Q.parse(r["name"])[0]
            if k in seen or r["id"] in hidden or len(out) >= limit:
                continue
            seen.add(k)
            out.append({x: r.get(x) for x in ("id", "name", "logo", "year", "rating", "progress")})

    if recommended:
        keys = list(dict.fromkeys(Q.parse(r["title"])[0] for r in recommended if r.get("title")))
        if keys:
            hits = {h["grp"] for h in store.q(
                f"SELECT DISTINCT grp FROM variants WHERE account=? AND type=? AND grp IN ({','.join('?' * len(keys))})",
                [aid, typ, *keys])}
            for k in keys:
                if k in hits:
                    add(items(store, aid, typ, profile, show_adult, "__all", k, 0, 3, quality=quality)["items"])
    for g in genres[:2]:
        ids = [r["id"] for r in store.q("SELECT id FROM people WHERE account=? AND type=? AND genre LIKE ? LIMIT 80",
                                        (aid, typ, f"%{norm(g)}%"))]
        if ids:
            add(store.q(f"SELECT id, name, logo, year, rating FROM {TABLE[typ]} t WHERE account=? AND id IN "
                        f"({','.join('?' * len(ids))})" + ("" if show_adult else
                        " AND NOT EXISTS (SELECT 1 FROM categories k WHERE k.account=t.account AND k.type=? AND k.id=t.category AND k.adult=1)")
                        + " ORDER BY rating DESC", [aid, *ids] + ([] if show_adult else [typ])))
    if len(out) < limit and d.get("category"):
        add(items(store, aid, typ, profile, show_adult, d["category"], "", 0, 60, sort="rating", quality=quality)["items"])
    return out


def series_info(store: Store, client: XtreamClient | None, aid: str, sid: str, profile: str, refresh: bool = False) -> dict:
    row = store.one("SELECT * FROM series WHERE account=? AND id=?", (aid, sid))
    if not row:
        raise MediaLabError("not_found", "Cette série n'existe plus dans le catalogue.")
    row.pop("norm", None)
    cached = store.one("SELECT data, fetched FROM series_info WHERE account=? AND id=?", (aid, sid))
    data = json.loads(cached["data"]) if cached else None
    if client is not None and (refresh or not cached or time.time() - cached["fetched"] > 6 * 3600):
        try:
            data = client.series_info(sid)
            store.run("INSERT OR REPLACE INTO series_info VALUES (?,?,?,?)", (aid, sid, json.dumps(data), time.time()))
        except MediaLabError:
            if data is None:
                raise
    data = data or {}
    seasons: dict[str, list] = {}
    eps = data.get("episodes") or {}
    if isinstance(eps, list):  # certains serveurs : liste de listes
        eps = {str(i + 1): v for i, v in enumerate(eps)}
    watched = {h["item"]: h for h in store.q("SELECT item, position, duration, watched FROM history WHERE profile=? AND account=? "
                                              "AND type='episode'", (profile, aid))}
    for season, lst in eps.items():
        for e in (lst if isinstance(lst, list) else []):
            if not isinstance(e, dict):
                continue
            eid = as_str(e.get("id"))
            info = e.get("info") if isinstance(e.get("info"), dict) else {}
            h = watched.get(eid)
            seasons.setdefault(as_str(season), []).append({
                "id": eid, "episode": as_int(e.get("episode_num")), "title": as_str(e.get("title")),
                "ext": as_str(e.get("container_extension"), "mp4"), "plot": as_str(info.get("plot")),
                "duration": as_str(info.get("duration")), "image": as_str(info.get("movie_image")),
                "progress": (h["position"] / h["duration"]) if h and h["duration"] else 0, "watched": bool(h and h["watched"])})
    for lst in seasons.values():
        lst.sort(key=lambda e: e["episode"] or 0)
    i = data.get("info") if isinstance(data.get("info"), dict) else {}
    if i:
        _remember_people(store, aid, "series", sid, as_str(i.get("genre")), as_str(i.get("cast")), as_str(i.get("director")))
    return {**row, "plot": as_str(i.get("plot")) or row.get("plot"), "cast": as_str(i.get("cast")),
            "director": as_str(i.get("director")), "tmdb_id": as_str(i.get("tmdb") or i.get("tmdb_id")),
            "trailer": as_str(i.get("youtube_trailer")), "genre": as_str(i.get("genre")), "backdrop": (i.get("backdrop_path") or [None])[0] if isinstance(i.get("backdrop_path"), list) else "",
            "seasons": [{"season": k, "episodes": v} for k, v in sorted(seasons.items(), key=lambda kv: as_int(kv[0], 0) or 0)]}


# ------------------------------------------------------------------ favoris, masqués, historique
def toggle_favorite(store: Store, profile: str, aid: str, typ: str, item: str, on: bool) -> None:
    if on:
        pos = (store.one("SELECT MAX(position) AS p FROM favorites WHERE profile=? AND account=? AND type=?",
                         (profile, aid, typ)) or {}).get("p") or 0
        store.run("INSERT OR IGNORE INTO favorites VALUES (?,?,?,?,?,?)", (profile, aid, typ, item, pos + 1, time.time()))
    else:
        store.run("DELETE FROM favorites WHERE profile=? AND account=? AND type=? AND item=?", (profile, aid, typ, item))


def reorder_favorites(store: Store, profile: str, aid: str, typ: str, ordered: list[str]) -> None:
    store.many("UPDATE favorites SET position=? WHERE profile=? AND account=? AND type=? AND item=?",
               [(i, profile, aid, typ, it) for i, it in enumerate(ordered)])


def set_hidden(store: Store, profile: str, aid: str, typ: str, item: str, on: bool) -> None:
    if on:
        store.run("INSERT OR IGNORE INTO hidden VALUES (?,?,?,?)", (profile, aid, typ, item))
    else:
        store.run("DELETE FROM hidden WHERE profile=? AND account=? AND type=? AND item=?", (profile, aid, typ, item))


def save_progress(store: Store, profile: str, aid: str, typ: str, item: str, position: float, duration: float,
                  title: str = "", logo: str = "", extra: dict | None = None) -> None:
    watched = int(duration > 0 and position >= duration * 0.93)
    store.run("INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (profile, account, type, item) DO UPDATE SET "
              "position=excluded.position, duration=excluded.duration, watched=MAX(history.watched, excluded.watched), "
              "updated=excluded.updated, title=excluded.title, logo=excluded.logo, extra=excluded.extra",
              (profile, aid, typ, item, title, logo, position, duration, watched, time.time(), json.dumps(extra or {})))


def resume_point(store: Store, profile: str, aid: str, typ: str, item: str) -> float:
    h = store.one("SELECT position, duration, watched FROM history WHERE profile=? AND account=? AND type=? AND item=?",
                  (profile, aid, typ, item))
    if not h or h["watched"] or h["position"] < 30:
        return 0.0
    return float(h["position"])


def continue_watching(store: Store, profile: str, aid: str, limit: int = 20) -> list[dict]:
    rows = store.q("SELECT type, item, title, logo, position, duration, updated, extra FROM history WHERE profile=? AND account=? "
                   "AND type IN ('movie','episode') AND watched=0 AND position>=30 ORDER BY updated DESC LIMIT ?",
                   (profile, aid, limit))
    for r in rows:
        r["extra"] = json.loads(r["extra"] or "{}")
        r["progress"] = r["position"] / r["duration"] if r["duration"] else 0
    return rows


def recent_channels(store: Store, profile: str, aid: str, limit: int = 12) -> list[dict]:
    return store.q("SELECT c.id, c.name, c.logo, h.updated FROM history h JOIN channels c ON c.account=h.account AND c.id=h.item "
                   "WHERE h.profile=? AND h.account=? AND h.type='live' ORDER BY h.updated DESC LIMIT ?", (profile, aid, limit))
