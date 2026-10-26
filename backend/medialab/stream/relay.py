"""Relais local des films et épisodes : une seule connexion à la fois vers le fournisseur.

Beaucoup de fournisseurs Xtream n'acceptent qu'une connexion par abonnement. Or pour un .mkv / .mp4, le lecteur en
ouvre deux à la fois : à l'ouverture (l'index du fichier est à la fin) et à chaque saut dans la barre de progression
(ffmpeg ouvre la nouvelle position avant de fermer l'ancienne). La deuxième est refusée (403) et le film reste au
début. Le lecteur lit donc ``http://127.0.0.1:<port>/v/<clé>/film.mkv`` : chaque nouvelle demande ferme d'abord la
connexion en cours vers le fournisseur, puis ouvre la nouvelle position (avec quelques essais, le temps que le serveur
compte la connexion comme fermée).

La clé est aléatoire et l'adresse ne répond que sur 127.0.0.1 ; l'URL du fournisseur (avec les identifiants) n'est
jamais écrite dans le journal.
"""
from __future__ import annotations

import http.client
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

from ..logging_setup import get_logger
from .xtream import redact

log = get_logger("relay")

PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag")


def _open(url: str, user_agent: str, rng: str | None, timeout: float = 20.0):
    """GET ``url`` (redirections suivies). Renvoie (connexion, réponse)."""
    for _ in range(6):
        u = urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = cls(u.hostname, u.port, timeout=timeout)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        headers = {"User-Agent": user_agent or "VLC/3.0.21 LibVLC/3.0.21", "Accept": "*/*", "Connection": "close"}
        if rng:
            headers["Range"] = rng
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        loc = resp.getheader("Location")
        if resp.status in (301, 302, 303, 307, 308) and loc:
            conn.close()
            url = urljoin(url, loc)
            continue
        return conn, resp
    raise OSError("trop de redirections")


def _kill(sock) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class Relay:
    def __init__(self) -> None:
        self._srv: ThreadingHTTPServer | None = None
        self._targets: dict[str, tuple[str, str]] = {}
        self._gate = threading.Lock()  # une ouverture à la fois
        self._lock = threading.Lock()
        self._active: tuple | None = None  # (socket fournisseur, socket lecteur) de la lecture en cours
        self.opened = 0  # connexions ouvertes vers le fournisseur (tests)

    @property
    def port(self) -> int:
        return self._srv.server_address[1] if self._srv else 0

    def start(self) -> None:
        if self._srv:
            return
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a) -> None:  # noqa: D401 - pas de journal par requête
                pass

            def do_GET(self) -> None:
                relay._serve(self)

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, name="stream-relay", daemon=True).start()

    def stop(self) -> None:
        self.abort()
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    def url_for(self, url: str, user_agent: str = "") -> str:
        """Adresse locale à donner au lecteur pour ``url``."""
        self.start()
        key = secrets.token_urlsafe(18)
        with self._lock:
            self._targets = {k: v for k, v in list(self._targets.items())[-3:]}  # les dernières lectures seulement
            self._targets[key] = (url, user_agent)
        name = PurePosixPath(urlsplit(url).path).name or "video"
        return f"http://127.0.0.1:{self.port}/v/{key}/{name}"

    def abort(self) -> None:
        """Ferme la lecture en cours (côté fournisseur et côté lecteur)."""
        with self._lock:
            act, self._active = self._active, None
        if act:
            _kill(act[0])
            _kill(act[1])

    def _serve(self, h: BaseHTTPRequestHandler) -> None:
        parts = h.path.split("/")
        target = self._targets.get(parts[2]) if len(parts) > 2 and parts[1] == "v" else None
        if not target:
            h.send_error(404)
            return
        url, ua = target
        rng = h.headers.get("Range")
        conn = resp = None
        with self._gate:
            self.abort()  # d'abord fermer, ensuite ouvrir : le fournisseur ne voit jamais deux connexions
            # un serveur Xtream met souvent 1 à 3 s à compter l'ancienne connexion comme fermée : il refuse la nouvelle
            # (403) en attendant. On réessaie pendant ~7 s avant d'abandonner (au lieu d'une erreur « connexion perdue »).
            for attempt in range(8):
                try:
                    conn, resp = _open(url, ua, rng)
                    self.opened += 1
                except OSError as e:
                    log.debug("relay open: %s", e)
                    conn = resp = None
                if resp is not None and resp.status < 400:
                    break
                status = resp.status if resp is not None else None
                if conn:
                    conn.close()
                conn = resp = None
                if status == 404:
                    break
                time.sleep(min(1.5, 0.3 * (attempt + 1)))
                log.info("Relais : le serveur refuse encore (%s), nouvel essai %d", status, attempt + 2)
            if resp is None:
                log.warning("Relais : le serveur refuse le film %s", redact(url))
                h.send_error(502)
                return
            with self._lock:
                self._active = (conn.sock, h.connection)
        try:
            h.send_response(resp.status)
            for k in PASS_HEADERS:
                v = resp.getheader(k)
                if v:
                    h.send_header(k, v)
            if not resp.getheader("Accept-Ranges"):
                h.send_header("Accept-Ranges", "bytes")
            h.send_header("Connection", "close")
            h.end_headers()
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                h.wfile.write(chunk)
        except (OSError, http.client.HTTPException, ValueError):
            pass  # le lecteur a sauté ailleurs (ou s'est arrêté) : cette lecture-là est finie
        finally:
            conn.close()
            with self._lock:
                if self._active and self._active[1] is h.connection:
                    self._active = None
            h.close_connection = True


relay = Relay()
