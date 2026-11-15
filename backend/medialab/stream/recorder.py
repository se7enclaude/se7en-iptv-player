"""Enregistrements : FFmpeg en copie de flux (aucun réencodage), programmés depuis le guide ou lancés à la main.

- un enregistrement programmé démarre un peu avant le début du programme et finit un peu après ;
- si le flux coupe, FFmpeg est relancé et la suite est écrite dans un nouveau morceau ; à la fin, les morceaux sont
  réunis en un seul fichier ;
- certains fournisseurs répondent par une courte vidéo toujours identique (connexion refusée, « trop de connexions ») :
  elle n'est pas gardée en double, les essais s'espacent, et l'enregistrement s'arrête en le disant si ça dure ;
- le fichier est un .ts (lisible même si l'enregistrement s'arrête brutalement) dans Vidéos\\se7en IPTV Player ;
- chaque enregistrement compte comme une connexion : on refuse s'il dépasse la limite de l'abonnement.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable

from ..components import CREATE_NO_WINDOW, require
from ..errors import MediaLabError
from ..events import bus
from ..logging_setup import get_logger
from ..system import kill_with_app
from .store import Store
from .xtream import redact

log = get_logger("recorder")

RETRY_DELAYS = (10, 20, 40, 60)   # secondes entre deux essais quand le direct « finit » tout de suite
FAST_END = 30                     # un direct qui « finit » en moins de 30 s n'était pas le direct
MAX_SAME = 8                      # tant d'essais d'affilée sur la même vidéo : on abandonne en expliquant pourquoi
SAME_VIDEO = ("Le fournisseur renvoie toujours la même courte vidéo au lieu de la chaîne "
              "(souvent : trop de connexions en même temps, ou chaîne indisponible).")


def file_digest(path: Path) -> str | None:
    """Empreinte d'un morceau (taille + début, milieu et fin : rapide même sur un gros fichier). None s'il est vide."""
    try:
        size = path.stat().st_size
        if not size:
            return None
        h = hashlib.sha1(str(size).encode())
        with open(path, "rb") as f:
            for pos in (0, max(0, size // 2 - 512 * 1024), max(0, size - 1024 * 1024)):
                f.seek(pos)
                h.update(f.read(1024 * 1024))
        return h.hexdigest()
    except OSError:
        return None


def parts_of(main: Path) -> list[Path]:
    """Le fichier principal puis ses morceaux « … partie 2.ts », « … partie 3.ts »… dans l'ordre."""
    found = []
    prefix = f"{main.stem} partie "
    for p in main.parent.iterdir() if main.parent.exists() else []:
        if p.suffix == main.suffix and p.stem.startswith(prefix) and p.stem[len(prefix):].isdigit():
            found.append((int(p.stem[len(prefix):]), p))
    return ([main] if main.exists() else []) + [p for _k, p in sorted(found)]


def default_folder() -> Path:
    return Path.home() / "Videos" / "se7en IPTV Player"


def _drain(pipe, tail: deque) -> None:
    """Lit la sortie d'erreur de FFmpeg jusqu'au bout (n'en garde que la fin)."""
    try:
        for line in iter(pipe.readline, b""):
            tail.append(line)
    except Exception:  # noqa: BLE001 - tuyau fermé
        pass
    finally:
        try:
            pipe.close()
        except Exception:  # noqa: BLE001
            pass


def safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", name or "").strip(" .")[:90] or "enregistrement"


class Connections:
    """Connexions ouvertes par l'appli, par abonnement (lecteur, aperçus, enregistrements, multi-écran)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.used: dict[str, dict[str, str]] = {}  # compte -> {clé: description}
        # « preload » (zapping instantané) cède sa place à tout le reste : appelé pour fermer cette chaîne d'avance
        self.on_evict: Callable[[str], None] = lambda aid: None

    def count(self, aid: str) -> int:
        with self.lock:
            return len(self.used.get(aid, {}))

    def take(self, aid: str, key: str, what: str, maximum: int | None) -> None:
        evicted = False
        with self.lock:
            cur = self.used.setdefault(aid, {})
            if key in cur:
                return
            if maximum is not None and len(cur) >= maximum and key != "preload" and "preload" in cur:
                cur.pop("preload")
                evicted = True
            if maximum is not None and len(cur) >= maximum:
                busy = ", ".join(cur.values())
                raise MediaLabError("max_connections", f"Ton abonnement permet {maximum} connexion(s) à la fois : déjà utilisée(s) par {busy}.",
                                    "Arrête une lecture ou un enregistrement, ou attends qu'il se termine.")
            cur[key] = what
        if evicted:
            self.on_evict(aid)

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
        # par enregistrement : empreintes des morceaux gardés, essais ratés d'affilée, heure du prochain essai
        self.retry: dict[str, dict] = {}
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
                        st = self.retry.setdefault(r["id"], {"seen": set(), "fails": 0, "next": 0.0, "checked": None})
                        if st["checked"] is not p:  # ce morceau vient de finir : jugé une seule fois
                            st["checked"] = p
                            self._judge_part(r, p, st, now)
                            if r["id"] not in self.procs:  # abandon (toujours la même vidéo)
                                continue
                        if now < st["next"]:
                            continue
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

    def _judge_part(self, r: dict, p, st: dict, now: float) -> None:
        """Un morceau vient de finir. Vide, ou copie d'un morceau déjà gardé : effacé. S'il a « fini » très vite, ce
        n'était pas le direct (vidéo d'attente du fournisseur) : le prochain essai attend de plus en plus longtemps."""
        out = getattr(p, "out", None)
        fast = now - getattr(p, "started", now) < FAST_END
        digest = file_digest(out) if out else None
        dup = digest is None or digest in st["seen"]
        if dup and out is not None and out != Path(r["file"]):
            try:
                out.unlink()
            except OSError:
                pass
        elif digest:
            st["seen"].add(digest)
        if fast and digest:
            st.setdefault("junk", set()).add(digest)  # vidéo d'attente : retirée au moment d'assembler
        if fast or dup:
            st["fails"] += 1
            st["next"] = now + RETRY_DELAYS[min(st["fails"] - 1, len(RETRY_DELAYS) - 1)]
            if st["fails"] >= MAX_SAME and dup:
                log.warning("Recording %s: provider keeps sending the same short video, giving up", r["id"])
                self._end(r["id"], "failed", reason=SAME_VIDEO)
        else:
            st["fails"], st["next"] = 0, 0.0

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
        kill_with_app(p)  # FFmpeg ne survit pas à l'appli (fermeture brutale, plantage)
        p.out = out  # type: ignore[attr-defined]
        p.started = time.time()  # type: ignore[attr-defined]
        # les erreurs de FFmpeg sont lues au fil de l'eau : lues seulement à la fin, elles rempliraient le tuyau
        # (4 Ko sous Windows) pendant un long enregistrement qui se reconnecte souvent, et FFmpeg resterait bloqué
        p.err_tail = deque(maxlen=64)  # type: ignore[attr-defined]
        p.err_reader = threading.Thread(target=_drain, args=(p.stderr, p.err_tail), name=f"rec-stderr-{r['id']}",  # type: ignore[attr-defined]
                                        daemon=True)
        p.err_reader.start()  # type: ignore[attr-defined]
        self.procs[r["id"]] = p

    def _end(self, rid: str, status: str, reason: str | None = None) -> None:
        p = self.procs.pop(rid, None)
        junk = (self.retry.pop(rid, None) or {}).get("junk") or set()
        if p is not None and p.poll() is None:
            try:
                p.stdin.write(b"q")  # FFmpeg finit proprement le fichier
                p.stdin.flush()
                p.wait(timeout=8)
            except Exception:  # noqa: BLE001
                p.kill()
        r = self.get(rid)
        err = None
        if p is not None and getattr(p, "err_tail", None) is not None:
            p.err_reader.join(timeout=2)
            err = b"".join(p.err_tail).decode("utf-8", "replace")[-400:] or None
        if r["file"]:
            try:
                join_parts(Path(r["file"]), junk)
            except Exception as e:  # noqa: BLE001 — les morceaux restent, lisibles un par un
                log.warning("Recording %s: parts not joined: %s", rid, e)
        size = Path(r["file"]).stat().st_size if r["file"] and Path(r["file"]).exists() else 0
        if size == 0:
            status = "failed"
        msg = reason or (redact(err) if err and status == "failed" else None)
        self.store.run("UPDATE recordings SET status=?, size=?, error=? WHERE id=?", (status, size, msg, rid))
        self.conns.release(r["account"], f"rec:{rid}")
        bus.publish("stream_recordings", {"done": rid})

    def shutdown(self) -> None:
        self._stop.set()
        for rid in list(self.procs):
            self._end(rid, "interrupted")


def join_parts(main: Path, junk: set[str] | frozenset = frozenset()) -> None:
    """Réunit « film.ts », « film partie 2.ts »… en un seul « film.ts » (copie de flux, sans réencodage). Les morceaux
    vides ou identiques à un autre (même empreinte) ne sont gardés qu'une fois ; ceux de ``junk`` (vidéo d'attente du
    fournisseur, reconnue pendant l'enregistrement) sont retirés, sauf s'il ne reste qu'eux."""
    every = parts_of(main)
    if all(file_digest(p) in junk for p in every):
        junk = frozenset()
    parts, seen = [], set()
    for p in every:
        d = file_digest(p)
        if d is None or d in seen or d in junk:
            if p != main:
                p.unlink(missing_ok=True)
            continue
        seen.add(d)
        parts.append(p)
    if len(parts) < 2:
        if parts and parts[0] != main:
            os.replace(parts[0], main)
        return
    tmp = main.with_name(main.stem + " (assemblage).ts")
    lst = main.with_name(main.stem + " (assemblage).txt")
    quote = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731 — guillemets du format concat de FFmpeg
    lst.write_text("".join(f"file {quote(str(p))}\n" for p in parts), encoding="utf-8")
    try:
        subprocess.run([require("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-map", "0", "-c", "copy", "-ignore_unknown", "-f", "mpegts", str(tmp)],
                       check=True, timeout=1800, creationflags=CREATE_NO_WINDOW, capture_output=True)
        if not tmp.exists() or tmp.stat().st_size < sum(p.stat().st_size for p in parts) * 0.9:
            raise RuntimeError("assemblage incomplet")
        os.replace(tmp, main)
        for p in parts:
            if p != main:
                p.unlink(missing_ok=True)
    finally:
        lst.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
