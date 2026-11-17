"""Routes HTTP de se7en IPTV Player et tâches de fond (mise à jour du catalogue et du guide)."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import APP_NAME
from ..errors import Canceled, MediaLabError
from ..events import bus
from ..logging_setup import get_logger
from . import catalog, images, recorder, subs_online, sync, versions
from . import search as search_index
from .player import Player
from .relay import relay
from .store import Store, check_pin, hash_pin
from .xtream import USER_AGENT, XtreamClient, account_problem, as_int, as_str, connections, normalize_server, redact

log = get_logger("stream")

DEFAULTS = {"output": "ts", "hwdec": "auto-safe", "buffer_s": 10, "buffer_mb": 150, "audio_lang": "fr,fre,fra,en,eng",
            "sub_lang": "fr,fre,fra", "record_folder": "", "margin_before": 1, "margin_after": 3, "refresh_hours": 12,
            "refresh_on_start": True, "hide_adult": True, "parental_pin": None, "volume": 100, "user_agent": "",
            "language": "fr", "group_quality": True, "quality_pref": "best", "instant_zap": True,
            "remote": False, "auto_next": True, "smooth": True, "hdr": True, "sub_scale": 1.0, "auto_sync": True, "interpolation": False}


class Session:
    """État de l'appli ouverte : abonnement et profil actifs, contenus adultes déverrouillés jusqu'à…"""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.account: str | None = store.get_meta("active_account")
        self.profile: str = store.get_meta("active_profile", "main")
        self.adult_until = 0.0
        self.last_channel: dict[str, str] = {}
        self.prev_channel: dict[str, str] = {}
        self.zap_dir: dict[str, int] = {}  # sens du dernier zapping (+1 / -1) : la chaîne ouverte d'avance
        self.digits = ""
        self.digit_timer: threading.Timer | None = None
        self.fullscreen = False

    def settings(self) -> dict:
        return {**DEFAULTS, **(self.store.get_meta("settings", {}) or {})}

    def quality(self) -> str:
        """Préférence de qualité pour regrouper les versions SD / HD / FHD / 4K ("" : ne pas regrouper)."""
        s = self.settings()
        return (s.get("quality_pref") or "best") if s.get("group_quality", True) else ""

    def show_adult(self) -> bool:
        prof = next((p for p in self.store.profiles() if p["id"] == self.profile), None)
        if prof and prof["kids"]:
            return False
        s = self.settings()
        return (not s["hide_adult"]) or time.time() < self.adult_until

    def require_account(self, aid: str | None = None) -> dict:
        aid = aid or self.account
        if not aid:
            raise MediaLabError("no_account", "Ajoute d'abord ton abonnement.")
        return self.store.account(aid)


# ------------------------------------------------------------------ corps des requêtes
class AccountBody(BaseModel):
    kind: str = "xtream"
    name: str = ""
    server: str = ""
    username: str = ""
    password: str | None = None
    m3u_url: str = ""
    epg_url: str = ""
    output: str = "ts"
    user_agent: str = ""


def next_episode_of(info: dict, iid: str) -> dict | None:
    """Dans la fiche d'une série, l'épisode qui suit ``iid`` (saison suivante comprise)."""
    eps = [{**e, "season": se.get("season")} for se in info.get("seasons") or [] for e in se.get("episodes") or []]
    k = next((n for n, e in enumerate(eps) if str(e.get("id")) == str(iid)), None)
    if k is None or k + 1 >= len(eps):
        return None
    e = eps[k + 1]
    label = f"S{e.get('season')} E{e.get('episode') if e.get('episode') is not None else '?'} · {e.get('title') or ''}".strip(" ·")
    return {"id": str(e["id"]), "title": e.get("title") or "", "label": label}


class PlayBody(BaseModel):
    type: str
    id: str
    account: str | None = None
    start: float | None = None      # replay : début du programme (UTC)
    stop: float | None = None
    title: str | None = None
    series: str | None = None       # épisode : la série
    resume: bool = True


class RectBody(BaseModel):
    x: float
    y: float
    w: float
    h: float
    dpr: float = 1.0
    visible: bool = True
    radius: int = 16


class CmdBody(BaseModel):
    cmd: str
    value: Any = None


class FavBody(BaseModel):
    type: str
    item: str
    on: bool = True


class OrderBody(BaseModel):
    type: str
    items: list[str]


class RecordBody(BaseModel):
    channel: str
    title: str = ""
    start: float | None = None
    stop: float | None = None
    minutes: int = 240
    source: str = "live"


class ReminderBody(BaseModel):
    channel: str
    title: str
    start: float


class ProfileBody(BaseModel):
    name: str | None = None
    kids: bool | None = None
    color: str | None = None
    pin: str | None = None


class PinBody(BaseModel):
    pin: str = ""
    new_pin: str | None = None


class BackupBody(BaseModel):
    path: str
    password: str


# ------------------------------------------------------------------ sauvegarde chiffrée
def _key(password: str, salt: bytes) -> bytes:
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000, 32)


def encrypt_backup(data: dict, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(password, salt)).encrypt(nonce, json.dumps(data).encode(), b"se7en-iptv-player")
    return b"S7SB1" + salt + nonce + ct


def decrypt_backup(blob: bytes, password: str) -> dict:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(b"S7SB1"):
        raise MediaLabError("bad_backup", "Ce fichier n'est pas une sauvegarde de se7en IPTV Player.")
    salt, nonce, ct = blob[5:21], blob[21:33], blob[33:]
    try:
        return json.loads(AESGCM(_key(password, salt)).decrypt(nonce, ct, b"se7en-iptv-player"))
    except InvalidTag as e:
        raise MediaLabError("bad_password", "Mot de passe de la sauvegarde incorrect.") from e


# ------------------------------------------------------------------ enregistrement des routes
def register(app: FastAPI, S) -> None:
    store = Store()
    sess = Session(store)
    conns = recorder.Connections()
    player = Player(APP_NAME, sess.settings)
    S.stream = sess  # type: ignore[attr-defined]

    def max_cons(account: dict) -> int | None:
        return connections((account.get("info") or {}).get("user_info") or {})[1] if account["kind"] == "xtream" else None

    def client(account: dict) -> XtreamClient:
        return sync.client_for(store, account)

    # ---------------------------------------------------------------- adresses de lecture
    def resolve(account: dict, typ: str, item_id: str, start: float | None = None, minutes: int | None = None) -> str:
        s = sess.settings()
        if account["kind"] == "m3u":
            table = {"live": "channels", "replay": "channels", "movie": "vod"}.get(typ)
            row = store.one(f"SELECT url FROM {table} WHERE account=? AND id=?", (account["id"], item_id)) if table else None
            if not row or not row["url"]:
                raise MediaLabError("not_found", "Introuvable dans la liste.")
            url = row["url"]
            if typ == "replay" and start:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}utc={int(start)}&lutc={int(time.time())}"
            return url
        c = client(account)
        try:
            if typ == "live":
                return c.live_url(item_id, c.output_format(account.get("output") or s["output"]))
            if typ == "movie":
                row = store.one("SELECT ext FROM vod WHERE account=? AND id=?", (account["id"], item_id))
                return c.movie_url(item_id, (row or {}).get("ext") or "mp4")
            if typ == "episode":
                ext = "mp4"
                for r in store.q("SELECT data FROM series_info WHERE account=?", (account["id"],)):
                    for lst in (json.loads(r["data"]).get("episodes") or {}).values():
                        for e in (lst or []):
                            if isinstance(e, dict) and as_str(e.get("id")) == item_id:
                                ext = as_str(e.get("container_extension"), "mp4")
                return c.episode_url(item_id, ext)
            if typ == "replay":
                return c.timeshift_url(item_id, start or time.time() - 3600, minutes or 60)
        finally:
            c.close()
        raise MediaLabError("invalid", "Type de contenu inconnu.")

    def rec_resolve(r: dict) -> tuple[str, str, int | None]:
        account = store.account(r["account"])
        if r["source"] == "replay":
            url = resolve(account, "replay", r["channel"], r["start"], int((r["stop"] - r["start"]) / 60) + 1)
        else:
            url = resolve(account, "live", r["channel"])
        return url, account.get("user_agent") or sess.settings()["user_agent"], max_cons(account)

    def rec_folder() -> Path:
        f = sess.settings().get("record_folder")
        return Path(f) if f else recorder.default_folder()

    rec = recorder.Recorder(store, conns, rec_resolve, rec_folder)

    def progress_cb(item: dict, pos: float, dur: float) -> None:
        catalog.save_progress(store, sess.profile, item["account"], item["kind"], item["id"], pos, dur,
                              item.get("title") or "", item.get("logo") or "", {"series": item.get("series")})
    player.on_progress(progress_cb)

    # ---------------------------------------------------------------- lecture
    def play(body: PlayBody) -> dict:
        account = sess.require_account(body.account)
        aid = account["id"]
        problem = account_problem((account.get("info") or {}).get("user_info") or {}) if account["kind"] == "xtream" else None
        if problem and problem[0] != "auth_failed":
            raise MediaLabError(problem[0], problem[1], "Renouvelle ton abonnement auprès de ton fournisseur.")
        conns.take(aid, "player", "la lecture en cours", max_cons(account))
        item: dict = {"account": aid, "kind": body.type, "id": body.id}
        start_at = 0.0
        if body.type in ("live", "replay"):
            ch = store.one("SELECT * FROM channels WHERE account=? AND id=?", (aid, body.id))
            if not ch:
                raise MediaLabError("not_found", "Cette chaîne n'existe plus dans le catalogue.")
            item.update(title=ch["name"], logo=ch["logo"], number=ch["num"], epg_id=ch["epg_id"])
            if body.type == "live":
                if sess.last_channel.get(aid) and sess.last_channel[aid] != body.id:
                    sess.prev_channel[aid] = sess.last_channel[aid]
                sess.last_channel[aid] = body.id
                now = catalog._attach_now  # noqa: SLF001 - même calcul que les listes
                rows = [dict(ch)]
                now(store, aid, rows)
                item.update(now=rows[0].get("now"), next=rows[0].get("next"))
                store.run("INSERT INTO history VALUES (?,?,?,?,?,?,0,0,0,?,?) ON CONFLICT (profile, account, type, item) "
                          "DO UPDATE SET updated=excluded.updated", (sess.profile, aid, "live", body.id, ch["name"], ch["logo"],
                                                                      time.time(), "{}"))
            else:
                minutes = int(((body.stop or 0) - (body.start or 0)) / 60) + 1
                item.update(kind="replay", subtitle=f"Replay · {body.title or ''}", start=body.start, stop=body.stop)
                url = resolve(account, "replay", body.id, body.start, max(1, minutes))
                ua = account.get("user_agent") or sess.settings()["user_agent"]
                if url.startswith("http"):
                    url = relay.url_for(url, ua)
                player.play(url, item, 0, ua)
                bus.publish("player", {"event": "started", "item": item})
                return {"item": item}
        elif body.type == "movie":
            row = store.one("SELECT name, logo FROM vod WHERE account=? AND id=?", (aid, body.id)) or {}
            item.update(title=row.get("name") or body.title or "Film", logo=row.get("logo"))
            if body.resume:
                start_at = catalog.resume_point(store, sess.profile, aid, "movie", body.id)
        elif body.type == "episode":
            item.update(title=body.title or "Épisode", series=body.series)
            ser = store.one("SELECT name, logo FROM series WHERE account=? AND id=?", (aid, body.series or "")) or {}
            item.update(subtitle=ser.get("name") or "", logo=ser.get("logo"))
            if body.resume:
                start_at = catalog.resume_point(store, sess.profile, aid, "episode", body.id)
        url = resolve(account, body.type, body.id)
        ua = account.get("user_agent") or sess.settings()["user_agent"]
        # Tout passe par le relais local (voir relay.py) : une seule connexion à la fois vers le fournisseur, et quand
        # son serveur n'a pas encore libéré la précédente (zapping, saut dans un film), il patiente au lieu d'échouer
        # (« connexion perdue · nouvelle tentative »). Sauf la chaîne déjà ouverte d'avance (zapping instantané).
        if url.startswith("http") and not (body.type == "live" and player.preloaded() == body.id):
            log.info("Play %s %s (relais local)", body.type, redact(url))
            url = relay.url_for(url, ua)
        player.play(url, item, start_at, ua, track_langs() if body.type in ("movie", "episode") else None)
        if body.type in ("movie", "episode"):
            threading.Thread(target=online_subtitles, args=(account, dict(item)), name="subs-online", daemon=True).start()
        if body.type == "episode":
            threading.Thread(target=find_next_episode, args=(account, dict(item)), name="next-episode", daemon=True).start()
        if player.preloaded() is None:  # la chaîne ouverte d'avance vient d'être affichée (ou il n'y en avait pas)
            conns.release(aid, "preload")
        if body.type == "live":
            schedule_preload(account, body.id)
        else:
            player.drop_preload()
            conns.release(aid, "preload")
        bus.publish("player", {"event": "started", "item": item})
        return {"item": item, "resumed_at": start_at}

    def find_next_episode(account: dict, item: dict) -> None:
        """L'épisode qui suit celui qui commence (pour la carte « Épisode suivant » pendant le générique)."""
        sid, iid = item.get("series") or "", item.get("id")
        if not sid:
            return
        c = client(account) if account["kind"] == "xtream" else None
        try:
            info = catalog.series_info(store, c, account["id"], sid, sess.profile)
        except Exception as e:  # noqa: BLE001 — sans la liste, pas de carte (la fin de l'épisode enchaîne quand même)
            log.info("Next episode unknown: %s", e)
            return
        finally:
            if c:
                c.close()
        nxt = next_episode_of(info, iid)
        if nxt and player.item and player.item.get("id") == iid:
            player.update_item(next_ep=nxt)

    def online_subtitles(account: dict, item: dict) -> None:
        """Sous-titres d'OpenSubtitles pour un film / un épisode qui n'en a pas dans tes langues (subs_online.py)."""
        aid, iid = account["id"], item["id"]
        try:
            tracks = None
            for _ in range(60):  # la vidéo s'ouvre : on attend ses pistes (30 s au plus)
                if not player.item or player.item.get("id") != iid:
                    return
                st = player.snapshot()
                if any(t["type"] == "audio" for t in st.get("tracks") or []):
                    tracks = st["tracks"]
                    break
                time.sleep(0.5)
            if tracks is None:
                return
            s = sess.settings()
            missing = [l for l in subs_online.wanted_langs(s.get("sub_lang") or "fr") if not subs_online.has_track(tracks, l)]
            if not missing:
                return
            imdb, season, episode, title, year = None, None, None, "", None

            def details(typ: str, i: str) -> dict:
                c = client(account) if account["kind"] == "xtream" else None
                try:
                    return catalog.vod_info(store, c, aid, i) if typ == "movie" else catalog.series_info(store, c, aid, i, sess.profile)
                finally:
                    if c:
                        c.close()

            if item["kind"] == "movie":
                w = store.one("SELECT tmdb FROM works WHERE account=? AND type='movie' AND id=?", (aid, iid)) or {}
                d = {} if w.get("tmdb") else details("movie", iid)
                tmdb = w.get("tmdb") or d.get("tmdb_id")
                row = store.one("SELECT name, year FROM vod WHERE account=? AND id=?", (aid, iid)) or {}
                title, y = catalog.search_title(row.get("name") or item.get("title") or "")
                year = row.get("year") or y
            else:
                sid = item.get("series") or ""
                d = details("series", sid)
                w = store.one("SELECT tmdb FROM works WHERE account=? AND type='series' AND id=?", (aid, sid)) or {}
                tmdb = w.get("tmdb") or d.get("tmdb_id")
                for se in d.get("seasons") or []:
                    for e in se["episodes"]:
                        if e["id"] == iid:
                            season, episode = int(se["season"] or 0) or None, e["episode"]
                title, year = catalog.search_title(d.get("name") or "")
                if season is None or episode is None:
                    return  # sans numéro d'épisode, on risquerait les sous-titres d'un autre épisode
            if tmdb:
                imdb = versions.work_info(store, "movie" if item["kind"] == "movie" else "series", str(tmdb)).get("imdb")
            audio = next((t for t in tracks if t["type"] == "audio" and t.get("selected")), None)
            audio_lang = ((audio or {}).get("lang") or "").lower()
            prefs = {x.strip().lower() for x in (s.get("audio_lang") or "fr").split(",")}
            sub_on = any(t["type"] == "sub" and t.get("selected") for t in tracks)
            # la vidéo est dans une autre langue que la tienne et rien n'est affiché : le premier sous-titre est activé
            select_first = not sub_on and bool(audio_lang) and audio_lang not in prefs
            added: list[str] = []
            for lang in missing:
                found = subs_online.search(imdb, lang, title=title, year=year, season=season, episode=episode)
                if not imdb:  # trouvé par le titre : son identifiant IMDb sert pour les autres langues
                    ids = [s_["imdb"] for s_ in found if s_.get("imdb")]
                    imdb = max(set(ids), key=ids.count) if ids else None
                n = 0
                for sub in found[:subs_online.PER_LANG * 2]:
                    if n >= subs_online.PER_LANG:
                        break
                    path = subs_online.download(sub)
                    if not path:
                        continue
                    n += 1
                    name = f"OpenSubtitles {n}"  # le lecteur ajoute la langue devant : « Français · OpenSubtitles 1 »
                    if not player.add_external_sub(iid, str(path), name, lang, select_first):
                        return
                    select_first = False
                if n:
                    added.append(f"{subs_online.LABEL.get(lang, lang)} ({n})")
            if added:
                log.info("Online subtitles added for %s: %s", iid, ", ".join(added))
                player.flash("Sous-titres ajoutés : " + ", ".join(added))
                bus.publish("player", {"event": "tracks"})
        except Exception:  # noqa: BLE001 — jamais au détriment de la lecture
            log.exception("online subtitles")

    def schedule_preload(account: dict, cur: str) -> None:
        """Zapping instantané : 1,5 s après le début d'une chaîne, ouvre d'avance la suivante (dans le sens du dernier
        zapping), si l'abonnement a une connexion de libre. Elle cède sa place à tout enregistrement."""
        aid = account["id"]
        if not sess.settings().get("instant_zap", True):
            return
        mx = max_cons(account)
        if mx is not None and mx < 2:
            return

        def run() -> None:
            time.sleep(1.5)
            if sess.last_channel.get(aid) != cur or not player.item or player.item.get("id") != cur:
                return
            row = store.one("SELECT category FROM channels WHERE account=? AND id=?", (aid, cur))
            if not row:
                return
            ids = catalog.group_ids(store, aid, "live", sess.profile, sess.show_adult(), row["category"], sess.quality())
            here = cur if cur in ids else next((i for i in catalog.group_of(store, aid, "live", cur) if i in ids), None)
            if not here or len(ids) < 2:
                return
            nxt = ids[(ids.index(here) + sess.zap_dir.get(aid, 1)) % len(ids)]
            if player.preloaded() == nxt:
                return
            try:
                conns.take(aid, "preload", "le zapping instantané", mx)
                player.preload(resolve(account, "live", nxt), {"account": aid, "kind": "live", "id": nxt},
                               account.get("user_agent") or sess.settings()["user_agent"])
            except MediaLabError:
                conns.release(aid, "preload")
            except Exception:  # noqa: BLE001  (le zapping instantané n'est qu'un plus)
                log.exception("preload")
                conns.release(aid, "preload")
        threading.Thread(target=run, name="preload", daemon=True).start()

    # ---------------------------------------------------------------- télécommande (téléphone)
    def remote_state() -> dict:
        a = store.account(sess.account) if sess.account else None
        item = player.item or {}
        st = player.snapshot()
        out: dict = {"playing": bool(player.item), "title": item.get("display_title") or item.get("title") or "",
                     "subtitle": item.get("display_subtitle") or item.get("subtitle") or "",
                     "kind": item.get("kind") or "", "id": item.get("id") or "", "number": item.get("number"),
                     "logo": item.get("logo") or "", "paused": bool(st.get("paused")), "volume": int(st.get("volume") or 0),
                     "muted": bool(st.get("muted")), "fullscreen": sess.fullscreen, "channels": [], "favorites": [],
                     "position": st.get("position") or 0, "duration": st.get("duration") or 0,
                     "tracks": [{k: t.get(k) for k in ("id", "type", "label", "selected")} for t in st.get("tracks") or []],
                     "skip_intro": bool(player.item) and player._intro_visible(st),
                     "next_episode": (item.get("next_ep") or {}).get("label") if item.get("kind") == "episode" else None}
        if not a:
            return out
        if item.get("kind") == "live":
            ch = [{"epg_id": item.get("epg_id")}]
            catalog._attach_now(store, a["id"], ch)
            out["now"], out["next"] = ch[0].get("now"), ch[0].get("next")
        keep = ("id", "name", "num", "logo", "now")

        def slim(rows: list[dict]) -> list[dict]:
            return [{k: (r.get(k)["title"] if k == "now" and r.get(k) else r.get(k)) for k in keep} for r in rows]
        out["favorites"] = slim(catalog.items(store, a["id"], "live", sess.profile, sess.show_adult(), "__fav", limit=60,
                                              quality=sess.quality())["items"])
        cur = sess.last_channel.get(a["id"])
        row = store.one("SELECT category FROM channels WHERE account=? AND id=?", (a["id"], cur)) if cur else None
        out["channels"] = slim(catalog.items(store, a["id"], "live", sess.profile, sess.show_adult(),
                                             row["category"] if row else "__all", limit=300, quality=sess.quality())["items"])
        return out

    def remote_command(cmd: str, value) -> object:
        if cmd == "zap":
            zap(1 if int(value or 1) > 0 else -1)
        elif cmd == "channel":
            play(PlayBody(type="live", id=str(value)))
        elif cmd == "previous":
            on_action("previous_channel", {})
        elif cmd == "digit" and str(value) in "0123456789" and len(str(value)) == 1:
            on_action("digit", {"digit": str(value)})
        elif cmd == "pause":
            player.toggle_pause()
        elif cmd == "mute":
            player.toggle_mute()
        elif cmd == "volume":
            player.volume(delta=max(-20, min(20, float(value or 0))))
        elif cmd == "seek":
            player.seek(max(-600, min(600, float(value or 0))))
        elif cmd == "fullscreen":
            return set_fullscreen(None)
        elif cmd == "stop":
            player.stop()
            conns.release(sess.account or "", "player")
            conns.release(sess.account or "", "preload")
        elif cmd == "osd":
            player.show_osd(6)
        elif cmd == "seek_to":
            player.seek(max(0.0, float(value or 0)), absolute=True)
        elif cmd in ("aid", "sid"):
            v = "no" if str(value) == "no" else int(value)
            player.set(cmd, v)
            remember_track(cmd, v)
        elif cmd == "skip_intro":
            player.skip_intro()
        elif cmd == "next_episode":
            ne = dict((player.item or {}).get("next_ep") or {})
            if ne:
                on_action("next_episode", {**ne, "series": (player.item or {}).get("series")})
        elif cmd == "play_movie":
            play(PlayBody(type="movie", id=str(value)))
            bus.publish("player", {"event": "remote_play"})
        elif cmd == "play_episode":
            v = value if isinstance(value, dict) else {}
            play(PlayBody(type="episode", id=str(v.get("id")), series=str(v.get("series") or ""), title=v.get("title")))
            bus.publish("player", {"event": "remote_play"})
        elif cmd == "search":
            return remote_search(str(value or ""))
        elif cmd == "series":
            return remote_series(str(value or ""))
        elif cmd == "home":
            a = sess.require_account()
            return [{"type": r["type"], "item": r["item"], "title": r["title"], "logo": r["logo"], "progress": r["progress"],
                     "series": (r.get("extra") or {}).get("series")} for r in catalog.continue_watching(store, sess.profile, a["id"], 12)]
        else:
            raise MediaLabError("invalid", "Action inconnue.")
        return None

    def remote_search(q: str) -> dict:
        a = sess.require_account()
        if len(q.strip()) < 2:
            return {"live": [], "movie": [], "series": []}
        from . import search as smart
        res = smart.search(store, a["id"], sess.profile, sess.show_adult(), q, sess.quality(), limit=15)
        return {typ: [{"id": r["id"], "name": r.get("name"), "logo": r.get("logo"), "year": r.get("year")} for r in res.get(typ) or []]
                for typ in ("live", "movie", "series")}

    def remote_series(sid: str) -> dict:
        a = sess.require_account()
        c = client(a) if a["kind"] == "xtream" else None
        try:
            info = catalog.series_info(store, c, a["id"], sid, sess.profile)
        finally:
            if c:
                c.close()
        return {"id": sid, "name": info.get("name"), "logo": info.get("logo"), "seasons": [
            {"season": se.get("season"), "episodes": [{"id": e.get("id"), "episode": e.get("episode"), "title": e.get("title"),
                                                       "watched": e.get("watched"), "progress": e.get("progress")}
                                                      for e in se.get("episodes") or []]} for se in info.get("seasons") or []]}

    from .remote import DEFAULT_PORT, Remote
    remote = Remote(store, remote_state, remote_command)
    S.remote = remote  # type: ignore[attr-defined]
    if sess.settings().get("remote"):
        threading.Thread(target=remote.start, name="remote-start", daemon=True).start()

    @app.get("/api/stream/remote")
    async def remote_get():
        return remote.info()

    @app.post("/api/stream/remote")
    async def remote_set(body: dict):
        def run():
            on = bool(body.get("enabled"))
            if body.get("reset"):
                remote.new_token()
            info = remote.start(DEFAULT_PORT) if on else remote.stop()
            cur = store.get_meta("settings", {}) or {}
            cur["remote"] = info["enabled"]
            store.set_meta("settings", cur)
            return info
        return await run_in_threadpool(run)

    def evict_preload(aid: str) -> None:
        player.drop_preload()
    conns.on_evict = evict_preload

    def zap(delta: int) -> None:
        aid = sess.account
        cur = sess.last_channel.get(aid or "")
        if not aid or not cur:
            return
        sess.zap_dir[aid] = 1 if delta > 0 else -1
        row = store.one("SELECT category FROM channels WHERE account=? AND id=?", (aid, cur))
        if not row:
            return
        # the list as shown (one version per SD / HD / FHD / 4K group): a channel watched in another quality zaps from its group
        ids = catalog.group_ids(store, aid, "live", sess.profile, sess.show_adult(), row["category"], sess.quality())
        here = cur if cur in ids else next((i for i in catalog.group_of(store, aid, "live", cur) if i in ids), None)
        if here:
            nxt = ids[(ids.index(here) + delta) % len(ids)]
            play(PlayBody(type="live", id=nxt))
            bus.publish("player", {"event": "zapped", "id": nxt})

    def set_fullscreen(on: bool | None = None) -> bool:
        """Plein écran de la vidéo seule, dans sa propre fenêtre sur tout l'écran (player.Host) : elle prend toujours
        toute la place, garde le clavier (Échap / F / flèches) et montre un bouton pour en sortir."""
        on = (not sess.fullscreen) if on is None else on
        if on and not player.item:
            on = False
        if on != sess.fullscreen:
            sess.fullscreen = on
            try:
                player.set_fullscreen(on)
            except Exception:  # noqa: BLE001
                log.exception("fullscreen")
            bus.publish("player", {"event": "fullscreen", "on": on})
        return on

    def remember_track(kind: str, value) -> None:
        """Langue audio / de sous-titres choisie : retenue pour ce profil et reprise au film suivant."""
        if kind == "sid" and value in ("no", None, False):
            lang = "no"
        else:
            t = next((t for t in player.snapshot().get("tracks", [])
                      if t["id"] == value and t["type"] == ("audio" if kind == "aid" else "sub")), None)
            lang = t.get("lang") if t else None
        if lang and lang not in ("und", "qaa", "mul"):
            store.run("INSERT OR REPLACE INTO tracks VALUES (?,?,?)", (sess.profile, kind, lang))

    def track_langs() -> dict:
        return {r["kind"]: r["lang"] for r in store.q("SELECT kind, lang FROM tracks WHERE profile=?", (sess.profile,))}

    def on_action(action: str, data: dict) -> None:
        try:
            if action == "fullscreen":
                set_fullscreen()
            elif action == "exit_fullscreen":
                set_fullscreen(False)
            elif action == "zap":
                zap(int(data.get("delta", 1)))
            elif action == "previous_channel":
                prev = sess.prev_channel.get(sess.account or "")
                if prev:
                    play(PlayBody(type="live", id=prev))
                    bus.publish("player", {"event": "zapped", "id": prev})
            elif action == "digit":
                sess.digits = (sess.digits + data["digit"])[-4:]
                player.flash(f"Chaîne {sess.digits}…", 1.6)
                if sess.digit_timer:
                    sess.digit_timer.cancel()

                def go():
                    num = int(sess.digits or 0)
                    sess.digits = ""
                    row = store.one("SELECT id FROM channels WHERE account=? AND num=?", (sess.account, num))
                    if row:
                        play(PlayBody(type="live", id=row["id"]))
                        bus.publish("player", {"event": "zapped", "id": row["id"]})
                sess.digit_timer = threading.Timer(1.4, go)
                sess.digit_timer.start()
            elif action == "track":
                remember_track(data["kind"], data["id"])
            elif action == "next_episode":
                if data.get("id") and data.get("series"):
                    play(PlayBody(type="episode", id=str(data["id"]), series=str(data["series"]), title=data.get("label"),
                                  resume=False))
            elif action == "sub_scale":
                cur = store.get_meta("settings", {}) or {}
                cur["sub_scale"] = float(data.get("value") or 1)
                store.set_meta("settings", cur)
            else:
                bus.publish("player", {"event": "action", "action": action})
        except MediaLabError as e:
            player.flash(e.message, 4)
    player.on_action = on_action
    from .audio import IntroMemory
    player.intro_mem = IntroMemory(store)
    player.account_id = lambda: sess.account or ""

    @app.post("/api/stream/play")
    async def play_route(body: PlayBody):
        return await run_in_threadpool(play, body)

    @app.put("/api/stream/player/rect")
    async def player_rect(body: RectBody):
        player.place(body.x, body.y, body.w, body.h, body.dpr, body.visible, body.radius)
        return {"ok": True}

    @app.get("/api/stream/player/still")
    async def player_still():
        data = await run_in_threadpool(player.still)
        if not data:
            return Response(status_code=204)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/stream/player")
    async def player_state():
        st = await run_in_threadpool(player.snapshot)
        st["connections"] = conns.count(sess.account or "")
        return st

    @app.post("/api/stream/player")
    async def player_cmd(body: CmdBody):
        def run():
            c, v = body.cmd, body.value
            if c == "stop":
                player.stop()
                conns.release(sess.account or "", "player")
                conns.release(sess.account or "", "preload")
            elif c == "pause":
                player.toggle_pause()
            elif c == "seek":
                player.seek(float(v or 0))
            elif c == "seek_to":
                player.seek(float(v or 0), absolute=True)
            elif c == "volume":
                player.volume(float(v))
            elif c == "mute":
                player.toggle_mute()
            elif c == "zap":
                zap(int(v or 1))
            elif c == "fullscreen":
                return {"on": set_fullscreen(None if v is None else bool(v))}
            elif c == "digit":
                on_action("digit", {"digit": str(v)[-1:]})
            elif c == "previous":
                on_action("previous_channel", {})
            elif c == "osd":
                player.show_osd(float(v or 4))
            elif c == "screenshot":
                return {"folder": player.screenshot()}
            elif c == "subtitle_file":
                player.add_subtitle(str(v))
            elif c in ("aid", "sid", "speed", "video-aspect-override", "panscan", "sub-delay", "sub-scale", "audio-delay", "af", "ontop"):
                player.set(c, v)
                if c in ("aid", "sid"):  # langue choisie mémorisée pour ce profil
                    remember_track(c, v)
            else:
                raise MediaLabError("invalid", "Commande inconnue.")
            return {"ok": True}
        return await run_in_threadpool(run)

    # ---------------------------------------------------------------- comptes
    def test_account(body: AccountBody) -> dict:
        if body.kind == "m3u":
            src = body.m3u_url.strip()
            if not src:
                raise MediaLabError("invalid", "Lien ou fichier de la liste manquant.")
            n = {"live": 0, "movie": 0}
            for k, e in enumerate(sync.read_m3u_entries(src, body.user_agent)):
                n["live" if e["kind"] == "live" else "movie"] += 1
                if k > 300_000:
                    break
            if not sum(n.values()):
                raise MediaLabError("m3u_empty", "Cette liste est vide ou illisible.")
            return {"kind": "m3u", "counts": n}
        with XtreamClient(body.server, body.username, body.password or "", user_agent=body.user_agent) as c:
            data = c.auth()
            ui = data.get("user_info") or {}
            active, mx = connections(ui)
            counts = {}
            for typ, fn in (("live", c.live_streams), ("movie", c.vod_streams), ("series", c.series)):
                try:
                    counts[typ] = len(fn())
                except MediaLabError:
                    counts[typ] = None
            problem = account_problem(ui)
            return {"kind": "xtream", "server": c.creds.server, "status": as_str(ui.get("status")),
                    "expires": as_int(ui.get("exp_date")), "trial": as_str(ui.get("is_trial")) == "1",
                    "active": active, "max": mx, "formats": ui.get("allowed_output_formats") or [],
                    "timezone": as_str((data.get("server_info") or {}).get("timezone")), "counts": counts,
                    "problem": problem[1] if problem else None, "info": data,
                    # the user agent the server accepted, when it refused ours (kept with the account)
                    "user_agent": c.user_agent if not body.user_agent and c.user_agent != USER_AGENT else ""}

    def start_sync(aid: str, what: str = "all") -> dict:
        account = store.account(aid)
        return S.jobs.add("stream_sync", account["name"], {"account": aid, "what": what},
                          title=f"Mise à jour · {account['name']}", platform="stream")

    @app.post("/api/stream/accounts/test")
    async def account_test(body: AccountBody):
        res = await run_in_threadpool(test_account, body)
        res.pop("info", None)
        return res

    @app.post("/api/stream/accounts")
    async def account_add(body: AccountBody):
        def run():
            res = test_account(body)
            if body.kind == "m3u":
                a = store.add_account("m3u", body.name.strip() or "Ma liste M3U", m3u_url=body.m3u_url.strip(),
                                      epg_url=body.epg_url.strip(), user_agent=body.user_agent)
            else:
                info = res.pop("info")
                a = store.add_account("xtream", body.name.strip() or normalize_server(body.server).split("//")[-1],
                                      server=normalize_server(body.server), username=body.username.strip(),
                                      password=body.password or "", epg_url=body.epg_url.strip(), output=body.output,
                                      user_agent=body.user_agent or res.pop("user_agent", ""), info={"user_info": info.get("user_info") or {},
                                                                        "server_info": info.get("server_info") or {}})
            sess.account = a["id"]
            store.set_meta("active_account", a["id"])
            start_sync(a["id"])
            return {"account": a, "summary": res}
        return await run_in_threadpool(run)

    @app.patch("/api/stream/accounts/{aid}")
    async def account_edit(aid: str, body: AccountBody):
        def run():
            vals = {k: v for k, v in body.model_dump().items() if k in ("name", "m3u_url", "epg_url", "output", "user_agent")}
            if body.kind == "xtream" and body.server:
                vals.update(server=normalize_server(body.server), username=body.username.strip())
            if body.password:
                vals["password"] = body.password
            a = store.update_account(aid, **vals)
            start_sync(aid)
            return a
        return await run_in_threadpool(run)

    @app.delete("/api/stream/accounts/{aid}")
    async def account_delete(aid: str):
        if sess.account == aid:
            player.stop()
            sess.account = None
        await run_in_threadpool(store.delete_account, aid)
        rest = store.accounts()
        sess.account = rest[0]["id"] if rest else None
        store.set_meta("active_account", sess.account)
        return {"ok": True}

    @app.post("/api/stream/accounts/{aid}/select")
    async def account_select(aid: str):
        store.account(aid)
        if sess.account != aid:
            player.stop()
            conns.release(sess.account or "", "player")
            conns.release(sess.account or "", "preload")
        sess.account = aid
        store.set_meta("active_account", aid)
        return {"ok": True}

    @app.post("/api/stream/accounts/{aid}/sync")
    async def account_sync(aid: str, what: str = "all"):
        return {"job": start_sync(aid, what)}

    # ---------------------------------------------------------------- état général
    @app.get("/api/stream/state")
    async def state():
        def run():
            accounts = store.accounts()
            for a in accounts:
                ui = (a.get("info") or {}).get("user_info") or {}
                active, mx = connections(ui)
                p = account_problem(ui) if a["kind"] == "xtream" else None
                a["status"] = {"expires": as_int(ui.get("exp_date")), "max": mx, "active": active, "problem": p[1] if p else None}
                a.pop("info", None)
            s = sess.settings()
            return {"accounts": accounts, "account": sess.account, "profiles": store.profiles(), "profile": sess.profile,
                    "adult": sess.show_adult(), "settings": {**s, "parental_pin": bool(s.get("parental_pin"))},
                    "mpv": player.mpv is not None or bool(__import__("medialab.stream.player", fromlist=["mpv_dir"]).mpv_dir())}
        return await run_in_threadpool(run)

    # ---------------------------------------------------------------- images (affiches, logos) réduites et gardées
    @app.get("/api/stream/img")
    async def image(u: str, w: int = 256):
        hit = images.cached(u, w)
        if hit:
            return FileResponse(hit, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})

        def run():
            ua = ""
            try:
                if sess.account:
                    ua = store.account(sess.account).get("user_agent") or ""
            except Exception:  # noqa: BLE001
                pass
            return images.get(u, w, ua)
        try:
            path = await asyncio.get_running_loop().run_in_executor(images.pool(), run)
        except images.ImageError:
            return Response(status_code=404, headers={"Cache-Control": "max-age=600"})
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    # ---------------------------------------------------------------- catalogue
    @app.get("/api/stream/home")
    async def home():
        def run():
            a = sess.require_account()
            return {"continue": catalog.continue_watching(store, sess.profile, a["id"]),
                    "recent": catalog.recent_channels(store, sess.profile, a["id"]),
                    "favorites": catalog.items(store, a["id"], "live", sess.profile, sess.show_adult(), "__fav", limit=24,
                                               quality=sess.quality())["items"],
                    "movies": catalog.items(store, a["id"], "movie", sess.profile, sess.show_adult(), "__recent", limit=24,
                                            quality=sess.quality())["items"],
                    "series": catalog.items(store, a["id"], "series", sess.profile, sess.show_adult(), "__recent", limit=24,
                                            quality=sess.quality())["items"]}
        return await run_in_threadpool(run)

    @app.get("/api/stream/categories/{typ}")
    async def categories(typ: str):
        if typ not in catalog.TABLE:
            raise MediaLabError("invalid", "Type inconnu.")
        a = sess.require_account()
        return {"categories": await run_in_threadpool(catalog.categories, store, a["id"], typ, sess.profile, sess.show_adult())}

    @app.get("/api/stream/items/{typ}")
    async def items(typ: str, category: str = "__all", q: str = "", offset: int = 0, limit: int = 200, sort: str = "default"):
        if typ not in catalog.TABLE:
            raise MediaLabError("invalid", "Type inconnu.")
        a = sess.require_account()
        return await run_in_threadpool(catalog.items, store, a["id"], typ, sess.profile, sess.show_adult(), category, q,
                                       offset, min(500, limit), sort, sess.quality())

    @app.get("/api/stream/guide")
    async def guide(category: str = "__all", start: int = 0, hours: int = 4, offset: int = 0, limit: int = 60):
        a = sess.require_account()
        start = start or int(time.time() // 1800 * 1800 - 1800)
        return await run_in_threadpool(catalog.guide, store, a["id"], sess.profile, sess.show_adult(), category, start,
                                       max(1, min(24, hours)), offset, min(200, limit), sess.quality())

    @app.get("/api/stream/channel/{cid}/epg")
    async def channel_epg(cid: str):
        a = sess.require_account()
        return {"programmes": await run_in_threadpool(catalog.channel_epg, store, a["id"], cid)}

    @app.get("/api/stream/search")
    async def search(q: str):
        a = sess.require_account()
        from . import search as smart
        return await run_in_threadpool(smart.search, store, a["id"], sess.profile, sess.show_adult(), q, sess.quality())

    @app.get("/api/stream/vod/{vid}")
    async def vod(vid: str):
        def run():
            a = sess.require_account()
            c = client(a) if a["kind"] == "xtream" else None
            try:
                d = catalog.vod_info(store, c, a["id"], vid)
            finally:
                if c:
                    c.close()
            versions.remember_tmdb(store, a["id"], "movie", vid, d.get("tmdb_id"))
            catalog.enrich(store, a["id"], "movie", d, sess.profile, sess.show_adult(), sess.quality())
            d["resume"] = catalog.resume_point(store, sess.profile, a["id"], "movie", vid)
            d["fav"] = bool(store.one("SELECT 1 FROM favorites WHERE profile=? AND account=? AND type='movie' AND item=?",
                                      (sess.profile, a["id"], vid)))
            return d
        return await run_in_threadpool(run)

    @app.get("/api/stream/series/{sid}")
    async def series(sid: str, refresh: bool = False):
        def run():
            a = sess.require_account()
            c = client(a) if a["kind"] == "xtream" else None
            try:
                d = catalog.series_info(store, c, a["id"], sid, sess.profile, refresh)
            finally:
                if c:
                    c.close()
            versions.remember_tmdb(store, a["id"], "series", sid, d.get("tmdb_id"))
            catalog.enrich(store, a["id"], "series", d, sess.profile, sess.show_adult(), sess.quality())
            d["fav"] = bool(store.one("SELECT 1 FROM favorites WHERE profile=? AND account=? AND type='series' AND item=?",
                                      (sess.profile, a["id"], sid)))
            return d
        return await run_in_threadpool(run)

    @app.get("/api/stream/versions/{typ}/{item}")
    async def all_versions(typ: str, item: str):
        """Le même film / la même série dans toutes les langues et toutes les qualités du catalogue."""
        if typ not in versions.TABLE:
            raise MediaLabError("invalid", "Type inconnu.")

        def run():
            a = sess.require_account()

            def tmdb_of(i: str) -> str | None:
                c = client(a) if a["kind"] == "xtream" else None
                if c is None:
                    return None
                try:
                    d = catalog.vod_info(store, c, a["id"], i) if typ == "movie" else                         catalog.series_info(store, c, a["id"], i, sess.profile)
                    return d.get("tmdb_id") or None
                finally:
                    c.close()

            def details_of(i: str) -> dict:
                if typ != "movie":
                    return {}
                row = store.one("SELECT data FROM details WHERE account=? AND type='movie' AND id=?", (a["id"], i))
                return json.loads(row["data"]) if row else {}

            langs = [{"fre": "fr", "fra": "fr", "eng": "en", "spa": "es", "ita": "it", "ger": "de", "deu": "de", "ara": "ar",
                      "tur": "tr", "por": "pt"}.get(x.strip().lower(), x.strip().lower())
                     for x in (sess.settings().get("audio_lang") or "fr").split(",")]
            res = versions.versions(store, a["id"], typ, item, tmdb_of=tmdb_of, details_of=details_of,
                                    pref_langs=list(dict.fromkeys(l for l in langs if len(l) == 2)),
                                    show_adult=sess.show_adult(), verify_limit=30 if typ == "movie" else 10)
            fav = {r["item"] for r in store.q("SELECT item FROM favorites WHERE profile=? AND account=? AND type=?",
                                              (sess.profile, a["id"], typ))}
            for v in res["versions"]:
                v["fav"] = v["id"] in fav
            return res
        return await run_in_threadpool(run)

    @app.get("/api/stream/interp")
    async def interp_status():
        from . import interp
        return interp.status()

    @app.get("/api/stream/trailer-page")
    async def trailer_page(v: str = ""):
        """La bande-annonce YouTube dans une page à part, ouverte par l'appli à l'adresse « localhost » : YouTube refuse
        de lire une vidéo intégrée dans une page dont l'adresse est un numéro (127.0.0.1), comme celle de l'appli."""
        import re
        from fastapi.responses import HTMLResponse
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", v):
            raise MediaLabError("invalid", "Bande-annonce introuvable.")
        # certaines bandes-annonces de studios refusent d'être lues hors de YouTube : un bouton pour l'y ouvrir
        html = ("<!doctype html><html><head><meta charset='utf-8'><title>Bande-annonce</title><style>"
                "html,body{margin:0;height:100%;background:#000;overflow:hidden;font-family:Segoe UI,sans-serif}"
                "iframe{border:0;width:100%;height:100%;display:block}"
                "button{position:fixed;right:16px;bottom:16px;padding:9px 16px;border:0;border-radius:99px;"
                "background:rgba(28,28,32,.82);color:#fff;font:600 13px Segoe UI,sans-serif;cursor:pointer;opacity:.55;transition:opacity .2s}"
                "button:hover{opacity:1}</style></head><body>"
                f"<iframe src='https://www.youtube-nocookie.com/embed/{v}?autoplay=1&rel=0&modestbranding=1&hl=fr' "
                "allow='autoplay; encrypted-media; picture-in-picture; fullscreen' allowfullscreen "
                "referrerpolicy='strict-origin-when-cross-origin'></iframe>"
                "<button onclick=\"fetch('/api/stream/trailer?token='+encodeURIComponent(new URLSearchParams(location.search).get('token')||''),"
                f"{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id:'{v}',browser:true}})}})"
                ".then(()=>window.close())\">Ouvrir sur YouTube</button></body></html>")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.post("/api/stream/trailer")
    async def trailer(body: dict, request: Request):
        """La bande-annonce dans une fenêtre de l'appli (page à « localhost », voir trailer_page) ; dans le navigateur
        si l'appli tourne sans sa fenêtre, ou si on le demande (« Ouvrir sur YouTube »)."""
        import re
        import webbrowser
        from urllib.parse import quote
        from .. import system
        yt = str(body.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", yt):
            raise MediaLabError("invalid", "Bande-annonce introuvable.")
        if getattr(system, "window", None) is not None and not body.get("browser"):
            try:
                import webview
                port = request.url.port or 80
                url = f"http://localhost:{port}/api/stream/trailer-page?v={yt}&token={quote(S.token)}"
                title = str(body.get("title") or "")[:80]
                webview.create_window(f"Bande-annonce · {title}" if title else "Bande-annonce", url=url, width=1280,
                                      height=760, min_size=(640, 400), background_color="#000000")
                return {"ok": True, "window": True}
            except Exception as e:  # noqa: BLE001
                log.info("Trailer window unavailable: %s", e)
        webbrowser.open(f"https://www.youtube.com/watch?v={yt}")
        return {"ok": True, "window": False}

    @app.post("/api/stream/favorite")
    async def favorite(body: FavBody):
        a = sess.require_account()
        await run_in_threadpool(catalog.toggle_favorite, store, sess.profile, a["id"], body.type, body.item, body.on)
        return {"ok": True}

    @app.post("/api/stream/favorite/order")
    async def favorite_order(body: OrderBody):
        a = sess.require_account()
        await run_in_threadpool(catalog.reorder_favorites, store, sess.profile, a["id"], body.type, body.items)
        return {"ok": True}

    @app.post("/api/stream/hidden")
    async def hidden(body: FavBody):
        a = sess.require_account()
        await run_in_threadpool(catalog.set_hidden, store, sess.profile, a["id"], body.type, body.item, body.on)
        return {"ok": True}

    @app.get("/api/stream/hidden")
    async def hidden_list():
        a = sess.require_account()
        return {"items": store.q("SELECT h.type, h.item, COALESCE(c.name, v.name, s.name) AS name FROM hidden h "
                                 "LEFT JOIN channels c ON h.type='live' AND c.account=h.account AND c.id=h.item "
                                 "LEFT JOIN vod v ON h.type='movie' AND v.account=h.account AND v.id=h.item "
                                 "LEFT JOIN series s ON h.type='series' AND s.account=h.account AND s.id=h.item "
                                 "WHERE h.profile=? AND h.account=?", (sess.profile, a["id"]))}

    @app.post("/api/stream/watched")
    async def watched(body: FavBody):
        a = sess.require_account()
        if body.on:
            catalog.save_progress(store, sess.profile, a["id"], body.type, body.item, 1, 1)
        else:
            store.run("DELETE FROM history WHERE profile=? AND account=? AND type=? AND item=?", (sess.profile, a["id"], body.type, body.item))
        return {"ok": True}

    # ---------------------------------------------------------------- enregistrements et rappels
    @app.get("/api/stream/recordings")
    async def recordings():
        return {"recordings": await run_in_threadpool(rec.list), "folder": str(rec_folder())}

    @app.post("/api/stream/recordings")
    async def record(body: RecordBody):
        def run():
            a = sess.require_account()
            ch = store.one("SELECT name FROM channels WHERE account=? AND id=?", (a["id"], body.channel))
            if not ch:
                raise MediaLabError("not_found", "Chaîne introuvable.")
            s = sess.settings()
            if body.start and body.stop:
                return rec.add(a["id"], body.channel, ch["name"], body.title or ch["name"], body.start, body.stop, body.source,
                               int(s["margin_before"]) * 60, int(s["margin_after"]) * 60)
            return rec.record_now(a["id"], body.channel, ch["name"], body.title, body.minutes)
        return await run_in_threadpool(run)

    @app.post("/api/stream/recordings/{rid}/stop")
    async def record_stop(rid: str):
        await run_in_threadpool(rec.stop, rid)
        return {"ok": True}

    @app.delete("/api/stream/recordings/{rid}")
    async def record_delete(rid: str, file: bool = False):
        await run_in_threadpool(rec.delete, rid, file)
        return {"ok": True}

    @app.post("/api/stream/recordings/{rid}/play")
    async def record_play(rid: str):
        r = rec.get(rid)
        if not r["file"] or not Path(r["file"]).exists():
            raise MediaLabError("file_missing", "Le fichier de cet enregistrement n'existe plus.")
        player.play(r["file"], {"account": r["account"], "kind": "recording", "id": rid, "title": r["title"],
                                "subtitle": f"Enregistré · {r['channel_name']}"}, 0)
        return {"ok": True}

    @app.get("/api/stream/reminders")
    async def reminders():
        return {"reminders": store.q("SELECT * FROM reminders WHERE profile=? AND start>? ORDER BY start",
                                     (sess.profile, time.time() - 600))}

    @app.post("/api/stream/reminders")
    async def reminder_add(body: ReminderBody):
        a = sess.require_account()
        ch = store.one("SELECT name FROM channels WHERE account=? AND id=?", (a["id"], body.channel)) or {}
        rid = os.urandom(5).hex()
        store.run("INSERT INTO reminders VALUES (?,?,?,?,?,?,?,0)", (rid, sess.profile, a["id"], body.channel,
                                                                     ch.get("name") or "", body.title, body.start))
        return {"id": rid}

    @app.delete("/api/stream/reminders/{rid}")
    async def reminder_delete(rid: str):
        store.run("DELETE FROM reminders WHERE id=?", (rid,))
        return {"ok": True}

    def reminder_loop() -> None:
        while True:
            time.sleep(15)
            try:
                for r in store.q("SELECT * FROM reminders WHERE fired=0 AND start<=?", (time.time() + 120,)):
                    store.run("UPDATE reminders SET fired=1 WHERE id=?", (r["id"],))
                    bus.publish("stream_reminder", r)
                    bus.publish("notify", {"kind": "success", "job_kind": "reminder", "title": f"{r['title']} · {r['channel_name']}"})
            except Exception:  # noqa: BLE001
                log.exception("reminders")
    threading.Thread(target=reminder_loop, name="reminders", daemon=True).start()

    # ---------------------------------------------------------------- profils et contrôle parental
    @app.get("/api/stream/profiles")
    async def profiles():
        return {"profiles": store.profiles(), "profile": sess.profile}

    @app.post("/api/stream/profiles")
    async def profile_add(body: ProfileBody):
        p = store.add_profile(body.name or "Profil", bool(body.kids), body.color or "#7FA7AC")
        if body.pin:
            store.set_pin(p["id"], body.pin)
        return p

    @app.patch("/api/stream/profiles/{pid}")
    async def profile_edit(pid: str, body: ProfileBody):
        store.update_profile(pid, body.name, body.kids, body.color)
        return {"ok": True}

    @app.delete("/api/stream/profiles/{pid}")
    async def profile_delete(pid: str):
        store.delete_profile(pid)
        if sess.profile == pid:
            sess.profile = "main"
            store.set_meta("active_profile", "main")
        return {"ok": True}

    @app.post("/api/stream/profiles/{pid}/select")
    async def profile_select(pid: str, body: PinBody):
        if not check_pin(body.pin, store.profile_pin(pid)):
            raise MediaLabError("bad_pin", "Code incorrect.")
        if sess.profile != pid:
            player.stop()
        sess.profile = pid
        sess.adult_until = 0
        store.set_meta("active_profile", pid)
        return {"ok": True}

    @app.post("/api/stream/profiles/{pid}/pin")
    async def profile_pin(pid: str, body: PinBody):
        if not check_pin(body.pin, store.profile_pin(pid)):
            raise MediaLabError("bad_pin", "Code actuel incorrect.")
        if body.new_pin and not (body.new_pin.isdigit() and 4 <= len(body.new_pin) <= 8):
            raise MediaLabError("invalid", "Le code doit faire 4 à 8 chiffres.")
        store.set_pin(pid, body.new_pin or None)
        return {"ok": True}

    @app.post("/api/stream/parental/unlock")
    async def parental_unlock(body: PinBody):
        s = sess.settings()
        prof = next((p for p in store.profiles() if p["id"] == sess.profile), None)
        if prof and prof["kids"]:
            raise MediaLabError("kids_profile", "Les contenus adultes ne sont jamais montrés dans un profil enfant.")
        if s.get("parental_pin") and not check_pin(body.pin, s["parental_pin"]):
            raise MediaLabError("bad_pin", "Code parental incorrect.")
        sess.adult_until = time.time() + 2 * 3600
        return {"ok": True, "until": sess.adult_until}

    @app.post("/api/stream/parental/lock")
    async def parental_lock():
        sess.adult_until = 0
        return {"ok": True}

    @app.post("/api/stream/parental/pin")
    async def parental_pin(body: PinBody):
        s = sess.settings()
        if s.get("parental_pin") and not check_pin(body.pin, s["parental_pin"]):
            raise MediaLabError("bad_pin", "Code parental actuel incorrect.")
        if body.new_pin and not (body.new_pin.isdigit() and 4 <= len(body.new_pin) <= 8):
            raise MediaLabError("invalid", "Le code doit faire 4 à 8 chiffres.")
        store.set_meta("settings", {**(store.get_meta("settings", {}) or {}),
                                    "parental_pin": hash_pin(body.new_pin) if body.new_pin else None})
        return {"ok": True}

    # ---------------------------------------------------------------- réglages
    @app.get("/api/stream/settings")
    async def settings_get():
        from . import tmdb
        s = sess.settings()
        return {**s, "parental_pin": bool(s.get("parental_pin")), "record_default": str(recorder.default_folder()),
                "tmdb": bool(tmdb.get_key(store))}

    @app.post("/api/stream/tmdb")
    async def tmdb_key(body: dict):
        """Ta clé TMDB (gratuite, sur themoviedb.org) : vérifiée, puis gardée chiffrée. Vide = l'enlever."""
        from . import tmdb

        def run():
            key = str(body.get("key") or "").strip()
            if key and not tmdb.check_key(key):
                raise MediaLabError("tmdb_key", "TMDB refuse cette clé.",
                                    "Copie la « clé d'API » ou le « jeton d'accès en lecture » de ton compte TMDB.")
            tmdb.save_key(store, key)
            store.run("DELETE FROM tmdb")  # fiches refaites avec (ou sans) la clé
            return {"tmdb": bool(key)}
        return await run_in_threadpool(run)

    @app.patch("/api/stream/settings")
    async def settings_patch(body: dict):
        cur = store.get_meta("settings", {}) or {}
        for k, v in body.items():
            if k in DEFAULTS and k != "parental_pin":
                cur[k] = v
        store.set_meta("settings", cur)
        if body.get("interpolation"):
            from . import interp
            if not interp.installed():  # première fois : les deux modules sont téléchargés, puis l'option s'applique
                def install_then_apply():
                    interp.install()
                    if interp.installed():
                        player.apply_video_settings(sess.settings())
                        player.flash("Interpolation d'images activée", 3)
                threading.Thread(target=install_then_apply, name="interp-install", daemon=True).start()
        if {"smooth", "hdr", "sub_scale", "interpolation", "hwdec"} & set(body):
            await run_in_threadpool(player.apply_video_settings, sess.settings())
        return {**sess.settings(), "parental_pin": bool(sess.settings().get("parental_pin"))}

    # ---------------------------------------------------------------- sauvegarde
    @app.post("/api/stream/backup/export")
    async def backup_export(body: BackupBody):
        def run():
            if len(body.password) < 6:
                raise MediaLabError("invalid", "Choisis un mot de passe d'au moins 6 caractères pour la sauvegarde.")
            data = {"v": 1, "made": time.time(), "settings": store.get_meta("settings", {}),
                    "accounts": [store.account(a["id"], with_secret=True) for a in store.accounts()],
                    "profiles": store.q("SELECT * FROM profiles"), "favorites": store.q("SELECT * FROM favorites"),
                    "hidden": store.q("SELECT * FROM hidden"), "history": store.q("SELECT * FROM history")}
            p = Path(body.path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(encrypt_backup(data, body.password))
            return {"path": str(p), "accounts": len(data["accounts"])}
        return await run_in_threadpool(run)

    @app.post("/api/stream/backup/import")
    async def backup_import(body: BackupBody):
        def run():
            data = decrypt_backup(Path(body.path).read_bytes(), body.password)
            existing = {(a["server"], a["username"], a["m3u_url"]) for a in store.accounts()}
            added = 0
            for a in data.get("accounts", []):
                if (a.get("server"), a.get("username"), a.get("m3u_url")) in existing:
                    continue
                new = store.add_account(a["kind"], a["name"], server=a.get("server") or "", username=a.get("username") or "",
                                        password=a.get("password") or "", m3u_url=a.get("m3u_url") or "",
                                        epg_url=a.get("epg_url") or "", output=a.get("output") or "ts",
                                        user_agent=a.get("user_agent") or "", info=a.get("info") or {})
                for t in ("favorites", "hidden", "history"):
                    for r in data.get(t, []):
                        if r.get("account") == a["id"]:
                            r = {**r, "account": new["id"]}
                            store.run(f"INSERT OR IGNORE INTO {t} ({','.join(r)}) VALUES ({','.join('?' * len(r))})", tuple(r.values()))
                start_sync(new["id"])
                added += 1
            for p in data.get("profiles", []):
                store.run("INSERT OR IGNORE INTO profiles (id, name, pin, kids, color, created) VALUES (?,?,?,?,?,?)",
                          (p["id"], p["name"], p.get("pin"), p.get("kids", 0), p.get("color"), p.get("created")))
            if data.get("settings"):
                store.set_meta("settings", {**data["settings"], **(store.get_meta("settings", {}) or {})})
            if added and not sess.account:
                sess.account = store.accounts()[0]["id"]
                store.set_meta("active_account", sess.account)
            return {"accounts": added}
        return await run_in_threadpool(run)

    # ---------------------------------------------------------------- tâche de mise à jour
    def worker(job: dict, jctx) -> dict:
        opts = job.get("options") or {}
        aid = opts["account"]
        account = store.account(aid)

        def progress(frac: float, label: str) -> None:
            jctx.progress({"stage": "processing", "label": label, "percent": frac * 100, "eta": None, "speed": 0})
            bus.publish("stream_sync", {"account": aid, "percent": frac * 100, "label": label})
        what = opts.get("what", "all")
        counts = {}
        epg = 0
        ok = False
        # quoi qu'il arrive (erreur inattendue, annulation…), « done » est publié : sinon l'indicateur de mise à jour
        # de l'interface resterait affiché pour toujours
        try:
            if what in ("all", "catalog"):
                counts = sync.sync_catalog(store, account, lambda f, l: progress(f * (0.6 if what == "all" else 1), l), jctx.cancel)
            if what in ("all", "epg"):
                try:
                    epg = sync.sync_epg(store, store.account(aid), lambda f, l: progress((0.6 if what == "all" else 0) + f * (0.4 if what == "all" else 1), l), jctx.cancel)
                except Canceled:
                    raise
                except MediaLabError as e:
                    log.warning("EPG failed for %s: %s", aid, e.message)
                    bus.publish("stream_sync", {"account": aid, "warning": e.message})
                except Exception as e:  # noqa: BLE001 - réseau, guide illisible… le catalogue reste bon
                    log.warning("EPG failed for %s: %r", aid, e)
                    bus.publish("stream_sync", {"account": aid, "warning": "Le guide des programmes n'a pas pu être mis à jour."})
            ok = True
        finally:
            bus.publish("stream_sync", {"account": aid, "done": True, "counts": counts, "epg": epg} if ok
                        else {"account": aid, "done": True, "failed": True})
        search_index.warm_later(store, aid)
        return {"file_path": None, "title": account["name"], "platform": "stream", "format": f"{sum(v or 0 for v in counts.values())} entrées",
                "meta": {"account": aid, "epg": epg}}

    S.jobs.register_worker("stream_sync", worker, lambda: 2)
    # mises à jour interrompues par la fermeture de l'appli (gardées « en pause ») : on les oublie, la mise à jour
    # automatique les relance si besoin (sinon l'indicateur de synchro attendrait une tâche qui ne repart jamais)
    try:
        for j in S.jobs.db.list_jobs(["paused"]):
            if j.get("kind") == "stream_sync":
                S.jobs.db.delete_job(j["id"])
    except Exception:  # noqa: BLE001
        log.exception("stream_sync cleanup")

    def auto_refresh() -> None:
        time.sleep(4)
        while True:
            try:
                s = sess.settings()
                for a in store.accounts():
                    search_index.warm(store, a["id"])  # index de recherche prêt avant la première recherche
                    # le catalogue est gardé sur le PC : rechargé seulement quand il a plus de 12 h (réglable), au
                    # lancement comme pendant que l'appli reste ouverte ; le guide seul quand il arrive à sa fin
                    age = time.time() - (a.get("synced") or 0)
                    if age > s["refresh_hours"] * 3600:
                        start_sync(a["id"])
                        a["synced"] = time.time()
                    else:
                        last = store.one("SELECT MAX(stop) AS t FROM epg WHERE account=?", (a["id"],))["t"]
                        epg_age = time.time() - (a.get("epg_synced") or 0)
                        if last is not None and last < time.time() + 6 * 3600 and epg_age > 3600:
                            start_sync(a["id"], "epg")
            except Exception:  # noqa: BLE001
                log.exception("auto refresh")
            time.sleep(1800)
    threading.Thread(target=auto_refresh, name="stream-refresh", daemon=True).start()

    from .. import system
    for stop in (rec.shutdown, player.shutdown, remote.stop, relay.stop):  # chacun le sien : une erreur ne saute pas les autres
        system.on_quit(stop)
    app.state.stream = {"store": store, "session": sess, "player": player, "recorder": rec, "connections": conns}
