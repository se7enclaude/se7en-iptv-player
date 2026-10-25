"""Client de l'API Xtream Codes (``player_api.php``) et adresses de lecture.

Les serveurs Xtream sont très hétérogènes : champs absents ou ``null``, nombres envoyés en texte, listes renvoyées sous
forme de dictionnaire, JSON tronqué, titres d'EPG en base64, fuseaux horaires variés. Tout passe par des fonctions de
lecture tolérantes (``as_int``, ``as_str``…) et chaque appel réessaie sur les erreurs réseau passagères.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from ..errors import MediaLabError
from ..logging_setup import get_logger

log = get_logger("stream")
USER_AGENT = "se7en IPTV Player"
# Some providers (often behind a proxy) only answer the players they know and reply 401 / 403 to anyone else, even with
# good codes. With no user agent chosen in the account, these are tried in turn after a refusal.
FALLBACK_AGENTS = ("VLC/3.0.21 LibVLC/3.0.21",
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/141.0.0.0 Safari/537.36")
RETRIES = 3


# ------------------------------------------------------------------ lecture tolérante des champs
def as_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip()
    return str(v).strip()


def as_int(v: Any, default: int | None = None) -> int | None:
    if v is None or isinstance(v, bool):
        return default if not isinstance(v, bool) else int(v)
    if isinstance(v, (int, float)):
        return int(v)
    s = as_str(v)
    if not s:
        return default
    try:
        return int(float(s.replace(",", ".")))
    except ValueError:
        return default


def as_float(v: Any, default: float | None = None) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = as_str(v).replace(",", ".")
    try:
        return float(s) if s else default
    except ValueError:
        return default


def as_list(v: Any) -> list:
    """Une liste, même quand le serveur renvoie un dictionnaire {"0": …, "1": …} ou rien."""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return list(v.values())
    return []


def as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return as_str(v).lower() in ("1", "true", "yes", "on")


_B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def maybe_b64(s: str) -> str:
    """Certains serveurs encodent les titres d'EPG en base64 : on décode seulement si le résultat est du vrai texte."""
    t = as_str(s)
    if len(t) < 4 or len(t) % 4 or not _B64.match(t):
        return t
    try:
        out = base64.b64decode(t, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return t
    return out if out.isprintable() or "\n" in out else t


# ------------------------------------------------------------------ serveur et identifiants
def normalize_server(url: str) -> str:
    """« monserveur.tv:8080/player_api.php?… » → « http://monserveur.tv:8080 » (sans chemin ni paramètres)."""
    u = as_str(url)
    if not u:
        raise MediaLabError("invalid_server", "Adresse du serveur manquante.", "Exemple : http://monserveur.tv:8080")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
        u = "http://" + u
    p = urlsplit(u)
    if not p.netloc:
        raise MediaLabError("invalid_server", "Adresse du serveur invalide.", "Exemple : http://monserveur.tv:8080")
    path = re.sub(r"/(player_api\.php|get\.php|xmltv\.php|panel_api\.php|c/?)$", "", p.path.rstrip("/"), flags=re.I)
    return urlunsplit((p.scheme.lower(), p.netloc, path, "", ""))


_SECRET_PATH = re.compile(r"/(live|movie|series|timeshift)/([^/]+)/([^/]+)/", re.I)
_SECRET_QS = re.compile(r"((?:username|password|user|pass)=)[^&\s]+", re.I)


def redact(url: str) -> str:
    """Adresse sans identifiants, pour les journaux : /live/***/***/123.ts, username=***."""
    s = _SECRET_PATH.sub(lambda m: f"/{m.group(1)}/***/***/", as_str(url))
    return _SECRET_QS.sub(r"\1***", s)


@dataclass
class Creds:
    server: str
    username: str
    password: str

    def path(self, kind: str) -> str:
        return f"{self.server}/{kind}/{quote(self.username, safe='')}/{quote(self.password, safe='')}"


# ------------------------------------------------------------------ problèmes de compte
def account_problem(user_info: dict, now: float | None = None) -> tuple[str, str] | None:
    """(code, message) si le compte ne peut pas lire : désactivé, expiré, banni, connexions pleines."""
    if not user_info:
        return ("auth_failed", "Identifiants refusés par le serveur.")
    if as_int(user_info.get("auth"), 1) == 0:
        return ("auth_failed", "Identifiants refusés par le serveur.")
    status = as_str(user_info.get("status"), "Active").lower()
    if status and status not in ("active", "actif"):
        label = {"expired": "expiré", "banned": "banni", "disabled": "désactivé"}.get(status, status)
        return (f"account_{status}", f"Ton abonnement est {label}.")
    exp = as_int(user_info.get("exp_date"))
    if exp and exp < (now or time.time()):
        return ("account_expired", "Ton abonnement a expiré le " + dt.datetime.fromtimestamp(exp).strftime("%d/%m/%Y") + ".")
    return None


def connections(user_info: dict) -> tuple[int, int | None]:
    """(connexions actives, maximum) ; maximum None = illimité ou inconnu."""
    mx = as_int(user_info.get("max_connections"))
    return as_int(user_info.get("active_cons"), 0) or 0, (mx if mx and mx > 0 else None)


# ------------------------------------------------------------------ client
class XtreamClient:
    def __init__(self, server: str, username: str, password: str, *, timeout: float = 25.0,
                 client: httpx.Client | None = None, user_agent: str = "") -> None:
        self.creds = Creds(normalize_server(server), username.strip(), password.strip())
        self._own = client is None
        self.http = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=True,
                                           headers={"User-Agent": user_agent or USER_AGENT})
        self.http.headers["User-Agent"] = self.user_agent = user_agent or USER_AGENT
        self._fallbacks = [] if user_agent else list(FALLBACK_AGENTS)
        self.server_info: dict = {}
        self.user_info: dict = {}

    def close(self) -> None:
        if self._own:
            self.http.close()

    def __enter__(self) -> "XtreamClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ---------------------------------------------------------------- appels
    def _get(self, action: str | None = None, **params: Any) -> Any:
        q = {"username": self.creds.username, "password": self.creds.password}
        if action:
            q["action"] = action
        q.update({k: v for k, v in params.items() if v is not None})
        url = f"{self.creds.server}/player_api.php"
        last: Exception | None = None
        attempt = 0
        while attempt < RETRIES:
            attempt += 1
            try:
                r = self.http.get(url, params=q)
            except httpx.HTTPError as e:
                last = e
                log.warning("Xtream %s: %s (try %d)", action or "auth", type(e).__name__, attempt)
                time.sleep(0.6 * attempt)
                continue
            if r.status_code in (401, 403) and self._fallbacks:
                ua = self._fallbacks.pop(0)
                log.info("Xtream %s: %d, retrying as %s", action or "auth", r.status_code, ua.split("/")[0])
                self.http.headers["User-Agent"] = self.user_agent = ua
                attempt -= 1  # not a failed try: same request, another name
                continue
            if r.status_code in (401, 403):
                log.warning("Xtream %s: refused (%d)", action or "auth", r.status_code)
                raise MediaLabError("auth_failed", "Identifiants refusés par le serveur.",
                                    "Vérifie le nom d'utilisateur, le mot de passe et l'adresse du serveur.")
            if r.status_code >= 500 or r.status_code == 429:
                last = MediaLabError("server_error", f"Le serveur a répondu {r.status_code}.")
                time.sleep(0.8 * attempt)
                continue
            if r.status_code >= 400:
                raise MediaLabError("server_error", f"Le serveur a répondu {r.status_code}.",
                                    "Vérifie l'adresse du serveur (avec le port, ex. :8080).")
            return _json(r.content)
        if isinstance(last, MediaLabError):
            raise last
        raise MediaLabError("server_unreachable", "Le serveur ne répond pas.",
                            "Vérifie l'adresse et ta connexion, puis réessaie.", True, str(last))

    def auth(self) -> dict:
        data = self._get()
        if not isinstance(data, dict) or "user_info" not in data:
            raise MediaLabError("auth_failed", "Ce serveur ne répond pas comme un serveur Xtream Codes.",
                                "Vérifie l'adresse (ex. http://monserveur.tv:8080) ou utilise une liste M3U.")
        self.user_info = data.get("user_info") or {}
        self.server_info = data.get("server_info") or {}
        problem = account_problem(self.user_info)
        if problem and problem[0] == "auth_failed":
            raise MediaLabError("auth_failed", problem[1], "Vérifie le nom d'utilisateur et le mot de passe.")
        return data

    def live_categories(self) -> list[dict]:
        return [c for c in as_list(self._get("get_live_categories")) if isinstance(c, dict)]

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        return [c for c in as_list(self._get("get_live_streams", category_id=category_id)) if isinstance(c, dict)]

    def vod_categories(self) -> list[dict]:
        return [c for c in as_list(self._get("get_vod_categories")) if isinstance(c, dict)]

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        return [c for c in as_list(self._get("get_vod_streams", category_id=category_id)) if isinstance(c, dict)]

    def vod_info(self, vod_id: int | str) -> dict:
        d = self._get("get_vod_info", vod_id=vod_id)
        return d if isinstance(d, dict) else {}

    def series_categories(self) -> list[dict]:
        return [c for c in as_list(self._get("get_series_categories")) if isinstance(c, dict)]

    def series(self, category_id: str | None = None) -> list[dict]:
        return [c for c in as_list(self._get("get_series", category_id=category_id)) if isinstance(c, dict)]

    def series_info(self, series_id: int | str) -> dict:
        d = self._get("get_series_info", series_id=series_id)
        return d if isinstance(d, dict) else {}

    def short_epg(self, stream_id: int | str, limit: int = 4) -> list[dict]:
        d = self._get("get_short_epg", stream_id=stream_id, limit=limit)
        return [epg_item(e) for e in as_list((d or {}).get("epg_listings") if isinstance(d, dict) else d)]

    def epg_table(self, stream_id: int | str) -> list[dict]:
        d = self._get("get_simple_data_table", stream_id=stream_id)
        return [epg_item(e) for e in as_list((d or {}).get("epg_listings") if isinstance(d, dict) else d)]

    # ---------------------------------------------------------------- adresses
    def xmltv_url(self) -> str:
        return (f"{self.creds.server}/xmltv.php?username={quote(self.creds.username, safe='')}"
                f"&password={quote(self.creds.password, safe='')}")

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        return f"{self.creds.path('live')}/{stream_id}.{'m3u8' if fmt == 'm3u8' else 'ts'}"

    def movie_url(self, stream_id: int | str, ext: str = "mp4") -> str:
        return f"{self.creds.path('movie')}/{stream_id}.{(ext or 'mp4').lstrip('.')}"

    def episode_url(self, episode_id: int | str, ext: str = "mp4") -> str:
        return f"{self.creds.path('series')}/{episode_id}.{(ext or 'mp4').lstrip('.')}"

    def timeshift_url(self, stream_id: int | str, start_utc: float, minutes: int) -> str:
        """Replay : l'heure de début est donnée dans le fuseau du serveur (server_info.timezone)."""
        local = to_server_time(start_utc, self.server_info)
        return f"{self.creds.path('timeshift')}/{max(1, int(minutes))}/{local.strftime('%Y-%m-%d:%H-%M')}/{stream_id}.ts"

    def output_format(self, preferred: str = "ts") -> str:
        allowed = [as_str(x).lower() for x in as_list(self.user_info.get("allowed_output_formats"))]
        if not allowed or preferred in allowed:
            return preferred
        return "m3u8" if "m3u8" in allowed else ("ts" if "ts" in allowed else preferred)


def _json(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace").strip().lstrip("﻿")
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # JSON tronqué ou précédé d'un avertissement PHP : on garde la partie JSON si elle existe
        for opener, closer in (("{", "}"), ("[", "]")):
            i, j = text.find(opener), text.rfind(closer)
            if i != -1 and j > i:
                try:
                    return json.loads(text[i:j + 1])
                except json.JSONDecodeError:
                    pass
        raise MediaLabError("bad_response", "Le serveur a envoyé une réponse illisible.",
                            "Réessaie plus tard ; si ça continue, le serveur a un problème.", True, text[:300])


# ------------------------------------------------------------------ guide (EPG)
def epg_item(e: Any) -> dict:
    if not isinstance(e, dict):
        return {}
    start = as_int(e.get("start_timestamp")) or _parse_dt(e.get("start"))
    stop = as_int(e.get("stop_timestamp")) or as_int(e.get("end_timestamp")) or _parse_dt(e.get("end") or e.get("stop"))
    return {"start": start, "stop": stop, "title": maybe_b64(as_str(e.get("title"))),
            "desc": maybe_b64(as_str(e.get("description"))), "archive": as_bool(e.get("has_archive")),
            "now": as_bool(e.get("now_playing"))}


def _parse_dt(v: Any) -> int | None:
    s = as_str(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def server_tz(server_info: dict) -> dt.tzinfo:
    name = as_str((server_info or {}).get("timezone"))
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - fuseau inconnu
            pass
    m = re.match(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2}):?(\d{2})?$", name)
    if m:
        delta = dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return dt.timezone(delta if m.group(1) == "+" else -delta)
    return dt.timezone.utc


def to_server_time(ts_utc: float, server_info: dict) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts_utc, tz=dt.timezone.utc).astimezone(server_tz(server_info))
