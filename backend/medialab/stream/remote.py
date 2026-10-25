"""Télécommande sur le téléphone : une petite page web servie sur le réseau de la maison (Wi-Fi).

Désactivée par défaut. Une fois activée dans les réglages, l'appli écoute sur le réseau local (port 8787) et montre un
QR code ; le téléphone l'ouvre dans son navigateur, sans rien installer. Le lien contient une clé secrète (recréable à
tout moment) : sans elle, rien ne répond. La page ne reçoit jamais les adresses des flux ni les identifiants.
"""
from __future__ import annotations

import secrets
import socket
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..logging_setup import get_logger
from ..security import tokens_equal
from .store import Store

log = get_logger("remote")
PAGE = Path(__file__).with_name("remote.html")
DEFAULT_PORT = 8787


def lan_ip() -> str:
    """L'adresse du PC sur le réseau local (celle de la carte qui sort vers Internet ; aucun paquet n'est envoyé)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 9))
        ip = s.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        s.close()
    if not ip or ip.startswith("127."):
        try:
            ip = next((a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                       if not a[4][0].startswith("127.")), "127.0.0.1")
        except OSError:
            ip = "127.0.0.1"
    return ip


class Remote:
    """``state()`` → ce que le téléphone affiche ; ``command(cmd, value)`` → ce qu'il demande (zapper, volume…)."""

    def __init__(self, store: Store, state: Callable[[], dict], command: Callable[[str, Any], Any]) -> None:
        self.store = store
        self.state = state
        self.command = command
        self.server = None
        self.thread: threading.Thread | None = None
        self.port = DEFAULT_PORT
        self.error = ""

    # ---------------------------------------------------------------- clé et adresse
    def token(self) -> str:
        t = self.store.get_meta("remote_token")
        if not t:
            t = self.new_token()
        return t

    def new_token(self) -> str:
        t = secrets.token_urlsafe(16)
        self.store.set_meta("remote_token", t)
        return t

    def url(self) -> str:
        return f"http://{lan_ip()}:{self.port}/r/{self.token()}"

    def info(self) -> dict:
        on = self.running()
        return {"enabled": on, "url": self.url() if on else "", "port": self.port, "error": self.error}

    # ---------------------------------------------------------------- serveur
    def running(self) -> bool:
        return bool(self.server is not None and self.thread and self.thread.is_alive())

    def start(self, port: int = DEFAULT_PORT) -> dict:
        if self.running():
            return self.info()
        import uvicorn
        self.port, self.error = port, ""
        config = uvicorn.Config(self._app(), host="0.0.0.0", port=port, log_level="warning", access_log=False, log_config=None)
        self.server = uvicorn.Server(config)
        self.server.install_signal_handlers = lambda: None  # type: ignore[method-assign]  (pas le fil principal)

        def run() -> None:
            try:
                self.server.run()
            except (OSError, SystemExit) as e:
                self.error = f"Le port {port} est déjà pris : {e}"
            if not self.server.started:
                self.error = self.error or f"Impossible d'écouter sur le port {port} (déjà utilisé ?)."
        self.thread = threading.Thread(target=run, name="remote", daemon=True)
        self.thread.start()
        for _ in range(40):
            if self.server.started or not self.thread.is_alive():
                break
            threading.Event().wait(0.05)
        if not self.running() or not self.server.started:
            self.server = None
            log.warning("Remote control could not start: %s", self.error)
        else:
            log.info("Remote control listening on port %d", port)
        return self.info()

    def stop(self) -> dict:
        if self.server is not None:
            self.server.should_exit = True
            if self.thread:
                self.thread.join(3)
        self.server = None
        return self.info()

    # ---------------------------------------------------------------- page et API du téléphone
    def _app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        def check(token: str) -> None:
            if not tokens_equal(token, self.token()):
                raise HTTPException(404)

        @app.get("/r/{token}", response_class=HTMLResponse)
        def page(token: str):
            check(token)
            return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store",
                                                                           "Referrer-Policy": "no-referrer"})

        @app.get("/r/{token}/api/state")
        def state(token: str):
            check(token)
            return JSONResponse(self.state(), headers={"Cache-Control": "no-store"})

        @app.post("/r/{token}/api/cmd")
        async def cmd(token: str, request: Request):
            check(token)
            body = await request.json()
            from starlette.concurrency import run_in_threadpool
            try:
                res = await run_in_threadpool(self.command, str(body.get("cmd") or ""), body.get("value"))
            except Exception as e:  # noqa: BLE001  (message lisible sur le téléphone)
                return JSONResponse({"ok": False, "error": getattr(e, "message", None) or "Action impossible."}, status_code=400)
            return {"ok": True, "result": res}

        return app
