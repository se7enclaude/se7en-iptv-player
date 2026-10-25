"""Base SQLite locale de se7en IPTV Player (<données>/stream.db, mode WAL, une connexion par fil).

Le catalogue (catégories, chaînes, films, séries, épisodes) et le guide sont des copies locales du serveur : l'appli
s'ouvre et cherche instantanément, puis se met à jour en arrière-plan. Les mots de passe sont chiffrés par Windows
(DPAPI). Favoris, historique, reprise et éléments masqués sont rangés par profil.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterable

from ..config import paths
from ..errors import MediaLabError
from .. import security

SCHEMA = 4
TYPES = ("live", "movie", "series")
ADULT_WORDS = ("adult", "adulte", "xxx", "18+", "+18", "porn", "porno", "sex", "erotic", "érotique", "hot ", "for adults")

DDL = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, server TEXT, username TEXT, secret BLOB,
  m3u_url TEXT, epg_url TEXT, output TEXT DEFAULT 'ts', user_agent TEXT DEFAULT '', info TEXT, position INTEGER DEFAULT 0,
  created REAL, synced REAL, epg_synced REAL, counts TEXT);
CREATE TABLE IF NOT EXISTS categories (
  account TEXT, type TEXT, id TEXT, name TEXT, position INTEGER, adult INTEGER DEFAULT 0, PRIMARY KEY (account, type, id));
CREATE TABLE IF NOT EXISTS channels (
  account TEXT, id TEXT, num INTEGER, name TEXT, norm TEXT, logo TEXT, category TEXT, epg_id TEXT,
  archive INTEGER DEFAULT 0, archive_days INTEGER DEFAULT 0, url TEXT, added INTEGER, position INTEGER,
  PRIMARY KEY (account, id));
CREATE INDEX IF NOT EXISTS ch_cat ON channels (account, category, position);
CREATE INDEX IF NOT EXISTS ch_epg ON channels (account, epg_id);
CREATE TABLE IF NOT EXISTS vod (
  account TEXT, id TEXT, name TEXT, norm TEXT, logo TEXT, category TEXT, rating REAL, year INTEGER, added INTEGER,
  ext TEXT, url TEXT, position INTEGER, PRIMARY KEY (account, id));
CREATE INDEX IF NOT EXISTS vod_cat ON vod (account, category, position);
CREATE TABLE IF NOT EXISTS series (
  account TEXT, id TEXT, name TEXT, norm TEXT, logo TEXT, category TEXT, rating REAL, year INTEGER, plot TEXT,
  added INTEGER, position INTEGER, PRIMARY KEY (account, id));
CREATE INDEX IF NOT EXISTS series_cat ON series (account, category, position);
CREATE TABLE IF NOT EXISTS series_info (account TEXT, id TEXT, data TEXT, fetched REAL, PRIMARY KEY (account, id));
CREATE TABLE IF NOT EXISTS epg (
  account TEXT, channel TEXT, start INTEGER, stop INTEGER, title TEXT, norm TEXT, descr TEXT, category TEXT,
  PRIMARY KEY (account, channel, start));
CREATE INDEX IF NOT EXISTS epg_time ON epg (account, start);
CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, name TEXT, pin TEXT, kids INTEGER DEFAULT 0, color TEXT, created REAL);
CREATE TABLE IF NOT EXISTS favorites (
  profile TEXT, account TEXT, type TEXT, item TEXT, position INTEGER, added REAL, PRIMARY KEY (profile, account, type, item));
CREATE TABLE IF NOT EXISTS hidden (profile TEXT, account TEXT, type TEXT, item TEXT, PRIMARY KEY (profile, account, type, item));
CREATE TABLE IF NOT EXISTS history (
  profile TEXT, account TEXT, type TEXT, item TEXT, title TEXT, logo TEXT, position REAL DEFAULT 0, duration REAL DEFAULT 0,
  watched INTEGER DEFAULT 0, updated REAL, extra TEXT, PRIMARY KEY (profile, account, type, item));
CREATE INDEX IF NOT EXISTS hist_recent ON history (profile, updated);
CREATE TABLE IF NOT EXISTS tracks (profile TEXT, kind TEXT, lang TEXT, PRIMARY KEY (profile, kind));
CREATE TABLE IF NOT EXISTS recordings (
  id TEXT PRIMARY KEY, account TEXT, channel TEXT, channel_name TEXT, title TEXT, start REAL, stop REAL, source TEXT,
  status TEXT, file TEXT, size INTEGER, error TEXT, created REAL);
CREATE TABLE IF NOT EXISTS details (account TEXT, type TEXT, id TEXT, data TEXT, fetched REAL, PRIMARY KEY (account, type, id));
CREATE TABLE IF NOT EXISTS people (
  account TEXT, type TEXT, id TEXT, genre TEXT, actors TEXT, director TEXT, PRIMARY KEY (account, type, id));
CREATE TABLE IF NOT EXISTS tmdb (key TEXT PRIMARY KEY, data TEXT, fetched REAL);
CREATE TABLE IF NOT EXISTS variants (
  account TEXT, type TEXT, id TEXT, grp TEXT, quality TEXT, rank INTEGER, rep_all INTEGER, rep_cat INTEGER,
  pos_all INTEGER, pos_cat INTEGER, PRIMARY KEY (account, type, id));
CREATE INDEX IF NOT EXISTS variants_grp ON variants (account, type, grp);
CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY, profile TEXT, account TEXT, channel TEXT, channel_name TEXT, title TEXT, start REAL, fired INTEGER DEFAULT 0);
"""


def norm(s: str | None) -> str:
    """Texte de recherche : minuscules, sans accents ni ponctuation superflue."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.replace("|", " ").split())


def is_adult(name: str) -> bool:
    n = f" {norm(name)} "
    return any(w in n for w in ADULT_WORDS)


def hash_pin(pin: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 120_000)
    return salt.hex() + ":" + h.hex()


def check_pin(pin: str, stored: str | None) -> bool:
    if not stored:
        return True
    salt, _, _h = stored.partition(":")
    return hash_pin(pin, bytes.fromhex(salt)) == stored


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (paths().data / "stream.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()
        c = self.conn()
        with self.write_lock:
            c.executescript(DDL)
            c.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
            if not c.execute("SELECT 1 FROM profiles LIMIT 1").fetchone():
                c.execute("INSERT INTO profiles VALUES (?, ?, NULL, 0, ?, ?)", ("main", "Moi", "#F1B9B2", time.time()))
            c.commit()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA temp_store=MEMORY")
            c.execute("PRAGMA cache_size=-32000")
            self._local.c = c
        return c

    def q(self, sql: str, params: Iterable | dict = ()) -> list[dict]:
        return [dict(r) for r in self.conn().execute(sql, params).fetchall()]

    def one(self, sql: str, params: Iterable | dict = ()) -> dict | None:
        r = self.conn().execute(sql, params).fetchone()
        return dict(r) if r else None

    def run(self, sql: str, params: Iterable | dict = ()) -> int:
        with self.write_lock:
            c = self.conn()
            cur = c.execute(sql, params)
            c.commit()
            return cur.rowcount

    def many(self, sql: str, rows: Iterable[tuple]) -> None:
        with self.write_lock:
            c = self.conn()
            c.executemany(sql, rows)
            c.commit()

    # ---------------------------------------------------------------- comptes
    @staticmethod
    def _clean_info(info: dict | None) -> str:
        """Le serveur renvoie le nom d'utilisateur et le mot de passe dans user_info : on ne les garde jamais en clair."""
        info = dict(info or {})
        if isinstance(info.get("user_info"), dict):
            info["user_info"] = {k: v for k, v in info["user_info"].items() if k not in ("username", "password")}
        return json.dumps(info)

    def add_account(self, kind: str, name: str, *, server: str = "", username: str = "", password: str = "",
                    m3u_url: str = "", epg_url: str = "", output: str = "ts", user_agent: str = "",
                    info: dict | None = None) -> dict:
        aid = uuid.uuid4().hex[:10]
        pos = (self.one("SELECT MAX(position) AS p FROM accounts") or {}).get("p") or 0
        self.run("INSERT INTO accounts (id, kind, name, server, username, secret, m3u_url, epg_url, output, user_agent, info,"
                 " position, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (aid, kind, name, server, username, security.protect(password.encode()) if password else None,
                  m3u_url, epg_url, output, user_agent, self._clean_info(info), pos + 1, time.time()))
        return self.account(aid)

    def update_account(self, aid: str, **values: Any) -> dict:
        if "password" in values:
            pw = values.pop("password")
            if pw is not None:
                values["secret"] = security.protect(pw.encode()) if pw else None
        if "info" in values and not isinstance(values["info"], str):
            values["info"] = self._clean_info(values["info"])
        if "counts" in values and not isinstance(values["counts"], str):
            values["counts"] = json.dumps(values["counts"])
        allowed = {"name", "server", "username", "secret", "m3u_url", "epg_url", "output", "user_agent", "info", "synced",
                   "epg_synced", "counts", "position"}
        sets = {k: v for k, v in values.items() if k in allowed}
        if sets:
            self.run(f"UPDATE accounts SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), aid))
        return self.account(aid)

    def account(self, aid: str, with_secret: bool = False) -> dict:
        r = self.one("SELECT * FROM accounts WHERE id=?", (aid,))
        if not r:
            raise MediaLabError("account_not_found", "Cet abonnement n'existe plus.")
        return self._account_out(r, with_secret)

    def accounts(self) -> list[dict]:
        return [self._account_out(r, False) for r in self.q("SELECT * FROM accounts ORDER BY position, created")]

    def _account_out(self, r: dict, with_secret: bool) -> dict:
        secret = r.pop("secret", None)
        r["info"] = json.loads(r.get("info") or "{}")
        r["counts"] = json.loads(r.get("counts") or "{}")
        r["has_password"] = bool(secret)
        if with_secret:
            r["password"] = security.unprotect(secret).decode() if secret else ""
        return r

    def delete_account(self, aid: str) -> None:
        with self.write_lock:
            c = self.conn()
            for t in ("categories", "channels", "vod", "series", "series_info", "epg"):
                c.execute(f"DELETE FROM {t} WHERE account=?", (aid,))
            for t in ("favorites", "hidden", "history", "reminders"):
                c.execute(f"DELETE FROM {t} WHERE account=?", (aid,))
            c.execute("DELETE FROM accounts WHERE id=?", (aid,))
            c.commit()

    # ---------------------------------------------------------------- profils
    def profiles(self) -> list[dict]:
        out = self.q("SELECT id, name, kids, color, pin IS NOT NULL AS locked, created FROM profiles ORDER BY created")
        for p in out:
            p["locked"] = bool(p["locked"])
            p["kids"] = bool(p["kids"])
        return out

    def add_profile(self, name: str, kids: bool = False, color: str = "#7FA7AC") -> dict:
        pid = uuid.uuid4().hex[:8]
        self.run("INSERT INTO profiles VALUES (?, ?, NULL, ?, ?, ?)", (pid, name.strip()[:40] or "Profil", int(kids), color, time.time()))
        return next(p for p in self.profiles() if p["id"] == pid)

    def update_profile(self, pid: str, name: str | None = None, kids: bool | None = None, color: str | None = None) -> None:
        if name is not None:
            self.run("UPDATE profiles SET name=? WHERE id=?", (name.strip()[:40] or "Profil", pid))
        if kids is not None:
            self.run("UPDATE profiles SET kids=? WHERE id=?", (int(kids), pid))
        if color is not None:
            self.run("UPDATE profiles SET color=? WHERE id=?", (color, pid))

    def set_pin(self, pid: str, pin: str | None) -> None:
        self.run("UPDATE profiles SET pin=? WHERE id=?", (hash_pin(pin) if pin else None, pid))

    def profile_pin(self, pid: str) -> str | None:
        r = self.one("SELECT pin FROM profiles WHERE id=?", (pid,))
        return r["pin"] if r else None

    def delete_profile(self, pid: str) -> None:
        if pid == "main":
            raise MediaLabError("profile_main", "Le profil principal ne peut pas être supprimé.")
        with self.write_lock:
            c = self.conn()
            for t in ("favorites", "hidden", "history", "tracks", "reminders"):
                c.execute(f"DELETE FROM {t} WHERE profile=?", (pid,))
            c.execute("DELETE FROM profiles WHERE id=?", (pid,))
            c.commit()

    # ---------------------------------------------------------------- réglages (clé / valeur)
    def get_meta(self, k: str, default: Any = None) -> Any:
        r = self.one("SELECT v FROM meta WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def set_meta(self, k: str, v: Any) -> None:
        self.run("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, json.dumps(v)))
