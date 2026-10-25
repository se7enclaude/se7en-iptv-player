"""Mise à jour du catalogue (chaînes, films, séries) et du guide (EPG) d'un abonnement, en arrière-plan.

Tout est écrit en une transaction par type : l'interface garde l'ancien catalogue jusqu'à ce que le nouveau soit prêt.
Le guide est lu en flux (XMLTV de l'abonnement ou XMLTV externe) et ne garde que la fenêtre utile (replay + 7 jours).
"""
from __future__ import annotations

import time
from typing import Callable

import httpx

from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from . import m3u, quality, xmltv
from .store import Store, is_adult, norm
from .xtream import XtreamClient, as_float, as_int, as_str, redact

log = get_logger("stream")
Progress = Callable[[float, str], None]
EPG_PAST_DAYS = 7      # replay : jusqu'à 7 jours en arrière
EPG_FUTURE_DAYS = 7


def client_for(store: Store, account: dict) -> XtreamClient:
    full = store.account(account["id"], with_secret=True)
    c = XtreamClient(full["server"], full["username"], full["password"], user_agent=full.get("user_agent") or "")
    if full.get("info"):
        c.user_info = full["info"].get("user_info") or {}
        c.server_info = full["info"].get("server_info") or {}
    return c


def _check(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Canceled()


# ------------------------------------------------------------------ catalogue
def sync_catalog(store: Store, account: dict, progress: Progress, cancel=None) -> dict:
    if account["kind"] == "m3u":
        return _sync_m3u(store, account, progress, cancel)
    with client_for(store, account) as c:
        progress(0.02, "Connexion au serveur")
        data = c.auth()
        store.update_account(account["id"], info={"user_info": data.get("user_info") or {},
                                                  "server_info": data.get("server_info") or {}})
        counts = {}
        steps = [("live", c.live_categories, c.live_streams), ("movie", c.vod_categories, c.vod_streams),
                 ("series", c.series_categories, c.series)]
        for k, (typ, cats_fn, items_fn) in enumerate(steps):
            _check(cancel)
            label = {"live": "chaînes", "movie": "films", "series": "séries"}[typ]
            progress(0.05 + 0.3 * k, f"Téléchargement des {label}")
            try:
                cats = cats_fn()
                items = items_fn()
            except MediaLabError as e:
                log.warning("Catalog %s failed: %s", typ, e.message)
                counts[typ] = None
                continue
            _check(cancel)
            progress(0.2 + 0.3 * k, f"Enregistrement des {label}")
            counts[typ] = _write_xtream(store, account["id"], typ, cats, items)
    store.update_account(account["id"], synced=time.time(), counts=counts)
    progress(1.0, "Catalogue à jour")
    return counts


def _write_xtream(store: Store, aid: str, typ: str, cats: list[dict], items: list[dict]) -> int:
    cat_rows = []
    for i, c in enumerate(cats):
        cid = as_str(c.get("category_id"))
        name = as_str(c.get("category_name"), "Sans catégorie")
        cat_rows.append((aid, typ, cid, name, i, int(is_adult(name))))
    with store.write_lock:
        con = store.conn()
        con.execute("BEGIN")
        con.execute("DELETE FROM categories WHERE account=? AND type=?", (aid, typ))
        con.executemany("INSERT OR REPLACE INTO categories VALUES (?,?,?,?,?,?)", cat_rows)
        if typ == "live":
            con.execute("DELETE FROM channels WHERE account=?", (aid,))
            con.executemany(
                "INSERT OR REPLACE INTO channels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ((aid, as_str(s.get("stream_id")), as_int(s.get("num")), as_str(s.get("name"), "Sans nom"),
                  norm(as_str(s.get("name"))), as_str(s.get("stream_icon")), as_str(s.get("category_id")),
                  as_str(s.get("epg_channel_id")), int(bool(as_int(s.get("tv_archive"), 0))),
                  as_int(s.get("tv_archive_duration"), 0) or 0, None, as_int(s.get("added")), i)
                 for i, s in enumerate(items) if as_str(s.get("stream_id"))))
        elif typ == "movie":
            con.execute("DELETE FROM vod WHERE account=?", (aid,))
            con.executemany(
                "INSERT OR REPLACE INTO vod VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ((aid, as_str(s.get("stream_id")), as_str(s.get("name"), "Sans nom"), norm(as_str(s.get("name"))),
                  as_str(s.get("stream_icon")), as_str(s.get("category_id")), as_float(s.get("rating")),
                  _year(s), as_int(s.get("added")), as_str(s.get("container_extension"), "mp4"), None, i)
                 for i, s in enumerate(items) if as_str(s.get("stream_id"))))
        else:
            con.execute("DELETE FROM series WHERE account=?", (aid,))
            con.executemany(
                "INSERT OR REPLACE INTO series VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ((aid, as_str(s.get("series_id")), as_str(s.get("name"), "Sans nom"), norm(as_str(s.get("name"))),
                  as_str(s.get("cover")), as_str(s.get("category_id")), as_float(s.get("rating")), _year(s),
                  as_str(s.get("plot")), as_int(s.get("last_modified")), i)
                 for i, s in enumerate(items) if as_str(s.get("series_id"))))
        if typ in ("movie", "series"):
            key = "stream_id" if typ == "movie" else "series_id"
            con.execute("DELETE FROM people WHERE account=? AND type=?", (aid, typ))
            con.executemany("INSERT OR REPLACE INTO people VALUES (?,?,?,?,?,?)", (
                (aid, typ, as_str(s.get(key)), norm(as_str(s.get("genre"))), norm(as_str(s.get("cast") or s.get("actors"))),
                 norm(as_str(s.get("director"))))
                for s in items if as_str(s.get(key)) and (s.get("genre") or s.get("cast") or s.get("actors") or s.get("director"))))
        con.commit()
    quality.index(store, aid, typ)
    return len(items)


def _year(s: dict) -> int | None:
    y = as_int(s.get("year"))
    if y:
        return y
    rd = as_str(s.get("releaseDate") or s.get("release_date"))
    return as_int(rd[:4]) if len(rd) >= 4 else None


def _sync_m3u(store: Store, account: dict, progress: Progress, cancel=None) -> dict:
    src = account.get("m3u_url") or ""
    progress(0.05, "Téléchargement de la liste")
    entries = list(m3u.parse(_read_m3u(src, account.get("user_agent") or "", cancel)))
    _check(cancel)
    progress(0.6, "Enregistrement de la liste")
    aid = account["id"]
    cats: dict[tuple[str, str], int] = {}
    live, vod = [], []
    for e in entries:
        typ = "live" if e["kind"] == "live" else "movie"
        group = e["group"] or "Sans catégorie"
        key = (typ, group)
        if key not in cats:
            cats[key] = len(cats)
        cid = f"m{cats[key]}"
        if typ == "live":
            days = as_int(e.get("catchup_days"), 0) or 0
            live.append((aid, e["id"], as_int(e.get("chno")), e["name"], norm(e["name"]), e["logo"], cid,
                         e["tvg_id"] or e["tvg_name"] or e["name"], int(bool(e.get("catchup")) or days > 0), days, e["url"],
                         None, len(live)))
        else:
            vod.append((aid, e["id"], e["name"], norm(e["name"]), e["logo"], cid, None, None, None,
                        e["url"].rsplit(".", 1)[-1][:5] if "." in e["url"].rsplit("/", 1)[-1] else "mp4", e["url"], len(vod)))
    with store.write_lock:
        con = store.conn()
        con.execute("BEGIN")
        for t in ("categories", "channels", "vod"):
            con.execute(f"DELETE FROM {t} WHERE account=?", (aid,))
        con.executemany("INSERT OR REPLACE INTO categories VALUES (?,?,?,?,?,?)",
                        ((aid, typ, f"m{i}", g, i, int(is_adult(g))) for (typ, g), i in cats.items()))
        con.executemany("INSERT OR REPLACE INTO channels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", live)
        con.executemany("INSERT OR REPLACE INTO vod VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", vod)
        con.commit()
    quality.index(store, aid)
    counts = {"live": len(live), "movie": len(vod), "series": 0}
    store.update_account(aid, synced=time.time(), counts=counts)
    progress(1.0, "Liste à jour")
    return counts


def _read_m3u(src: str, ua: str, cancel=None):
    if src.lower().startswith(("http://", "https://")):
        with httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=True,
                          headers={"User-Agent": ua or "se7en IPTV Player"}) as h, h.stream("GET", src) as r:
            if r.status_code >= 400:
                raise MediaLabError("m3u_failed", f"La liste n'a pas pu être téléchargée ({r.status_code}).",
                                    "Vérifie le lien de la liste.")
            for line in r.iter_lines():
                _check(cancel)
                yield line
    else:
        try:
            with open(src, "rb") as f:
                yield from (line.decode("utf-8", "replace") for line in f)
        except OSError as e:
            raise MediaLabError("m3u_missing", "Fichier de liste introuvable.", str(e)) from e


def read_m3u_entries(src: str, ua: str = "", cancel=None):
    return m3u.parse(_read_m3u(src, ua, cancel))


# ------------------------------------------------------------------ guide
def epg_source(store: Store, account: dict) -> str | None:
    if account.get("epg_url"):
        return account["epg_url"]
    if account["kind"] == "xtream":
        with client_for(store, account) as c:
            return c.xmltv_url()
    return None


def sync_epg(store: Store, account: dict, progress: Progress, cancel=None) -> int:
    url = epg_source(store, account)
    if not url:
        return 0
    now = int(time.time())
    since, until = now - EPG_PAST_DAYS * 86400, now + EPG_FUTURE_DAYS * 86400
    aid = account["id"]
    progress(0.02, "Téléchargement du guide des programmes")
    # correspondance chaîne du guide → epg_id de nos chaînes (id XMLTV, ou nom affiché pour les listes M3U)
    known = {r["epg_id"] for r in store.q("SELECT DISTINCT epg_id FROM channels WHERE account=? AND epg_id<>''", (aid,))}
    by_name = {norm(r["epg_id"]): r["epg_id"] for r in store.q(
        "SELECT DISTINCT epg_id FROM channels WHERE account=? AND epg_id<>''", (aid,))}
    alias: dict[str, str] = {}
    rows: list[tuple] = []
    count = 0
    t0 = time.monotonic()

    def flush(final: bool = False) -> None:
        nonlocal rows
        if rows and (final or len(rows) >= 5000):
            store.many("INSERT OR REPLACE INTO epg VALUES (?,?,?,?,?,?,?,?)", rows)
            rows = []

    with store.write_lock:
        store.conn().execute("DELETE FROM epg WHERE account=?", (aid,))
        store.conn().commit()
    try:
        raw = _open_url(url, account.get("user_agent") or "")
    except MediaLabError:
        raise
    try:
        for kind, item in xmltv.parse(raw, since, until):
            _check(cancel)
            if kind == "channel":
                cid = item["id"]
                if cid in known:
                    alias[cid] = cid
                else:
                    for n in item["names"]:
                        if norm(n) in by_name:
                            alias[cid] = by_name[norm(n)]
                            break
                continue
            ch = alias.get(item["channel"], item["channel"] if item["channel"] in known else None)
            if ch is None:
                continue
            rows.append((aid, ch, item["start"], item["stop"], item["title"], norm(item["title"]), item["desc"],
                         item["category"]))
            count += 1
            if count % 5000 == 0:
                flush()
                progress(min(0.95, 0.1 + (time.monotonic() - t0) / 120), f"Guide : {count} programmes")
        flush(final=True)
    except xmltv.Broken as e:
        flush(final=True)
        if not count:
            raise
        log.warning("EPG for %s cut short after %d programmes: %s", aid, count, e.detail)
    finally:
        raw.close()
    store.update_account(aid, epg_synced=time.time())
    progress(1.0, f"Guide à jour : {count} programmes")
    log.info("EPG for %s: %d programmes from %s", aid, count, redact(url))
    return count


class _HttpStream:
    """Réponse HTTP lue comme un fichier (pour l'analyse XMLTV en flux)."""

    def __init__(self, client: httpx.Client, resp: httpx.Response) -> None:
        self.client, self.resp = client, resp
        self.it = resp.iter_raw()  # brut : on gère gzip nous-mêmes (même s'il n'est pas annoncé)
        self.buf = b""

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self.buf) < n:
            try:
                self.buf += next(self.it)
            except StopIteration:
                break
        if n < 0:
            out, self.buf = self.buf, b""
        else:
            out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def close(self) -> None:
        self.resp.close()
        self.client.close()


def _open_url(url: str, ua: str):
    if not url.lower().startswith(("http://", "https://")):
        return open(url, "rb")
    client = httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=True,
                          headers={"User-Agent": ua or "se7en IPTV Player", "Accept-Encoding": "identity"})
    resp = client.send(client.build_request("GET", url), stream=True)
    if resp.status_code >= 400:
        resp.close()
        client.close()
        raise MediaLabError("epg_failed", f"Le guide des programmes n'a pas pu être téléchargé ({resp.status_code}).",
                            "Le serveur ne fournit peut-être pas de guide ; tu peux ajouter un lien XMLTV.")
    return _HttpStream(client, resp)
