"""Enregistrements : FFmpeg en copie de flux (aucun réencodage), programmés depuis le guide ou lancés à la main.

- un enregistrement programmé démarre un peu avant le début du programme et finit un peu après ;
- si le flux coupe, FFmpeg est relancé et la suite est écrite dans un nouveau morceau ;
- le fichier est un .ts (lisible même si l'enregistrement s'arrête brutalement) dans Vidéos\\se7en IPTV Player ;
- chaque enregistrement compte comme une connexion : on refuse s'il dépasse la limite de l'abonnement.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from ..components import CREATE_NO_WINDOW, require
from ..errors import MediaLabError
from ..events import bus
from ..logging_setup import get_logger
from .store import Store
from .xtream import redact

log = get_logger("recorder")


def default_folder() -> Path:
    return Path.home() / "Videos" / "se7en IPTV Player"


def safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", name or "").strip(" .")[:90] or "enregistrement"


class Connections:
    """Connexions ouvertes par l'appli, par abonnement (lecteur, aperçus, enregistrements, multi-écran)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.used: dict[str, dict[str, str]] = {}  # compte -> {clé: description}

    def count(self, aid: str) -> int:
        with self.lock:
            return len(self.used.get(aid, {}))

    def take(self, aid: str, key: str, what: str, maximum: int | None) -> None:
        with self.lock:
            cur = self.used.setdefault(aid, {})
            if key in cur:
                return
            if maximum is not None and len(cur) >= maximum:
                busy = ", ".join(cur.values())
                raise MediaLabError("max_connections", f"Ton abonnement permet {maximum} connexion(s) à la fois : déjà utilisée(s) par {busy}.",
                                    "Arrête une lecture ou un enregistrement, ou attends qu'il se termine.")
            cur[key] = what

    def release(self, aid: str, key: str) -> None:
        with self.lock:
            self.used.get(aid, {}).pop(key, None)

    def release_prefix(self, aid: str, prefix: str) -> None:
        with self.lock:
            for k in [k for k in self.used.get(aid, {}) if k.startswith(prefix)]:
                self.used[aid].pop(k, None)


class Recorder:
    def __init__(self, store: Store, conns: Connections, resolve: Callable[[dict], tuple[str, str, int | None]],
                 folder: Callable[[], Path]) -> None:
        """``resolve(recording)`` → (adresse du flux, user-agent, connexions max de l'abonnement)."""
        self.store = store
        self.conns = conns
        self.resolve = resolve
        self.folder = folder
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.RLock()
        self._stop = threading.Event()
        # au démarrage, ce qui était « en cours » a été interrompu par la fermeture de l'appli
        store.run("UPDATE recordings SET status='interrupted' WHERE status='recording'")
        threading.Thread(target=self._loop, name="recorder", daemon=True).start()

    # ---------------------------------------------------------------- création
    def add(self, aid: str, channel: str, channel_name: str, title: str, start: float, stop: float, source: str = "live",
            margin_before: int = 60, margin_after: int = 120) -> dict:
        now = time.time()
        if stop <= start:
            raise MediaLabError("invalid", "La fin doit être après le début.")
        if source == "live":
            start, stop = max(now, start - margin_before), stop + margin_after
        elif stop > now:
            raise MediaLabError("invalid", "Ce programme n'est pas encore terminé : enregistre-le en direct.")
        rid = uuid.uuid4().hex[:10]
        self.store.run("INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (rid, aid, channel, channel_name, title, start, stop, source, "scheduled", None, 0, None, now))
        bus.publish("stream_recordings", {})
        self._tick()
        return self.get(rid)

    def record_now(self, aid: str, channel: str, channel_name: str, title: str, minutes: int = 240) -> dict:
        now = time.time()
        return self.add(aid, channel, channel_name, title or channel_name, now, now + minutes * 60, "live", 0, 0)

    def get(self, rid: str) -> dict:
        r = self.store.one("SELECT * FROM recordings WHERE id=?", (rid,))
        if not r:
            raise MediaLabError("not_found", "Cet enregistrement n'existe plus.")
        return r

    def list(self) -> list[dict]:
        rows = self.store.q("SELECT * FROM recordings ORDER BY start DESC")
        for r in rows:
            if r["file"] and Path(r["file"]).exists():
                r["size"] = Path(r["file"]).stat().st_size
        return rows

    def stop(self, rid: str) -> None:
        r = self.get(rid)
        if r["status"] == "recording":
            self.store.run("UPDATE recordings SET stop=? WHERE id=?", (time.time(), rid))
            self._end(rid, "done")
        elif r["status"] == "scheduled":
            self.store.run("UPDATE recordings SET status='canceled' WHERE id=?", (rid,))
        bus.publish("stream_recordings", {})

    def delete(self, rid: str, delete_file: bool = False) -> None:
        r = self.get(rid)
        if r["status"] == "recording":
            self.stop(rid)
        if delete_file and r["file"]:
            for p in Path(r["file"]).parent.glob(Path(r["file"]).stem + "*" + Path(r["file"]).suffix):
                p.unlink(missing_ok=True)
        self.store.run("DELETE FROM recordings WHERE id=?", (rid,))
        bus.publish("stream_recordings", {})

    # ---------------------------------------------------------------- boucle
    def _loop(self) -> None:
        while not self._stop.wait(5):
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("recorder tick")

    def _tick(self) -> None:
        now = time.time()
        with self.lock:
            for r in self.store.q("SELECT * FROM recordings WHERE status='scheduled' AND start<=?", (now,)):
                if r["stop"] <= now and r["source"] == "live":
                    self.store.run("UPDATE recordings SET status='missed' WHERE id=?", (r["id"],))
                    continue
                self._start(r)
            for r in self.store.q("SELECT * FROM recordings WHERE status='recording'"):
                p = self.procs.get(r["id"])
                if r["source"] == "live" and r["stop"] <= now:
                    self._end(r["id"], "done")
                elif p is not None and p.poll() is not None:
                    if r["source"] == "live" and r["stop"] - now > 20:
                        log.warning("Recording %s: stream dropped (code %s), restarting", r["id"], p.returncode)
                        self._spawn(self.get(r["id"]), part=True)
                    else:
                        self._end(r["id"], "done" if p.returncode == 0 or r["source"] != "live" else "done")

    def _start(self, r: dict) -> None:
        try:
            _url, _ua, mx = self.resolve(r)
            self.conns.take(r["account"], f"rec:{r['id']}", f"l'enregistrement « {r['title']} »", mx)
        except MediaLabError as e:
            self.store.run("UPDATE recordings SET status='failed', error=? WHERE id=?", (e.message, r["id"]))
            bus.publish("stream_recordings", {"error": e.message})
            return
        folder = self.folder()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %Hh%M", time.localtime(r["start"]))
        f = folder / f"{safe(r['title'])} - {safe(r['channel_name'])} - {stamp}.ts"
        n = 2
        while f.exists():
            f = f.with_name(f"{f.stem.rsplit(' (', 1)[0]} ({n}).ts")
            n += 1
        self.store.run("UPDATE recordings SET status='recording', file=?, error=NULL WHERE id=?", (str(f), r["id"]))
        self._spawn(self.get(r["id"]))
        bus.publish("stream_recordings", {})

    def _spawn(self, r: dict, part: bool = False) -> None:
        url, ua, _mx = self.resolve(r)
        out = Path(r["file"])
        if part:
            k = 2
            while out.with_name(f"{out.stem} partie {k}{out.suffix}").exists():
                k += 1
            out = out.with_name(f"{out.stem} partie {k}{out.suffix}")
        args = [require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
        if ua:
            args += ["-user_agent", ua]
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                 "-reconnect_delay_max", "10", "-rw_timeout", "20000000", "-i", url, "-map", "0", "-c", "copy",
                 "-ignore_unknown", "-f", "mpegts"]
        if r["source"] != "live":
            args += ["-t", str(int(r["stop"] - r["start"]) + 5)]
        args.append(str(out))
        log.info("Recording %s → %s (%s)", r["id"], out.name, redact(url))
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             creationflags=CREATE_NO_WINDOW)
        self.procs[r["id"]] = p

    def _end(self, rid: str, status: str) -> None:
        p = self.procs.pop(rid, None)
        if p is not None and p.poll() is None:
            try:
                p.stdin.write(b"q")  # FFmpeg finit proprement le fichier
                p.stdin.flush()
                p.wait(timeout=8)
            except Exception:  # noqa: BLE001
                p.kill()
        r = self.get(rid)
        err = None
        if p is not None and p.stderr is not None:
            try:
                err = p.stderr.read().decode("utf-8", "replace")[-400:] or None
            except Exception:  # noqa: BLE001
                err = None
        size = Path(r["file"]).stat().st_size if r["file"] and Path(r["file"]).exists() else 0
        if size == 0:
            status = "failed"
        self.store.run("UPDATE recordings SET status=?, size=?, error=? WHERE id=?",
                       (status, size, (redact(err) if err and status == "failed" else None), rid))
        self.conns.release(r["account"], f"rec:{rid}")
        bus.publish("stream_recordings", {"done": rid})

    def shutdown(self) -> None:
        self._stop.set()
        for rid in list(self.procs):
            self._end(rid, "interrupted")
