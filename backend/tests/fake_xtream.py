"""Faux serveur Xtream Codes pour les tests (aucun vrai service n'est contacté).

Reproduit les bizarreries des vrais serveurs : nombres en texte, champs null, listes en dictionnaire, titres d'EPG en
base64, avertissement PHP avant le JSON. Les flux pointent vers des fichiers de test locaux (``media``).
"""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

USER, PASS = "demo", "s3cret"


def make_app(channels: int = 30, vod: int = 20, series: int = 5, *, status: str = "Active", exp_days: int = 30,
             max_cons: int = 2, active: int = 0, media: dict[str, Path] | None = None, php_warning: bool = False) -> FastAPI:
    app = FastAPI()
    media = media or {}
    now = int(time.time())
    state = {"status": status, "active": active, "calls": []}
    live_cats = [{"category_id": "1", "category_name": "France"}, {"category_id": "2", "category_name": "Sport"},
                 {"category_id": "9", "category_name": "XXX Adult"}]
    chans = []
    for i in range(channels):
        cat = "9" if i == channels - 1 else ("1" if i % 2 == 0 else "2")
        chans.append({"num": i + 1, "name": f"Chaîne {i + 1}" + (" Été" if i == 3 else ""), "stream_id": str(1000 + i),
                      "stream_icon": "", "epg_channel_id": f"ch{i + 1}.fr" if i % 5 else None, "category_id": cat,
                      "tv_archive": 1 if i < 3 else "0", "tv_archive_duration": "7" if i < 3 else None, "added": str(now - i)})
    vods = [{"stream_id": 2000 + i, "name": f"Film {i + 1}", "stream_icon": "", "rating": "7.5" if i % 2 else None,
             "category_id": "10", "container_extension": "mkv" if i == 0 else "mp4", "added": now - i * 10,
             "year": "2020"} for i in range(vod)]
    sers = [{"series_id": 3000 + i, "name": f"Série {i + 1}", "cover": "", "category_id": "20", "plot": "Une série.",
             "rating": 8, "last_modified": str(now - i)} for i in range(series)]

    def ok(req: Request) -> bool:
        return req.query_params.get("username") == USER and req.query_params.get("password") == PASS

    def info() -> dict:
        return {"user_info": {"username": USER, "password": PASS, "auth": 1, "status": state["status"],
                              "exp_date": str(now + exp_days * 86400), "is_trial": "0", "active_cons": str(state["active"]),
                              "max_connections": str(max_cons), "allowed_output_formats": ["m3u8", "ts"]},
                "server_info": {"url": "testserver", "port": "80", "server_protocol": "http", "timezone": "Europe/Paris",
                                "timestamp_now": now}}

    def epg_for(stream_id: str) -> list[dict]:
        out = []
        base = now - now % 3600 - 3 * 3600
        for k in range(8):
            start = base + k * 3600
            out.append({"id": str(k), "title": base64.b64encode(f"Émission {k}".encode()).decode(),
                        "description": base64.b64encode(b"Description").decode(), "start_timestamp": str(start),
                        "stop_timestamp": str(start + 3600), "has_archive": 1 if start + 3600 < now else 0,
                        "now_playing": 1 if start <= now < start + 3600 else 0})
        return out

    @app.get("/player_api.php")
    def api(request: Request):
        state["calls"].append(request.query_params.get("action") or "auth")
        if not ok(request):
            return JSONResponse({"user_info": {"auth": 0}})
        a = request.query_params.get("action")
        if a is None:
            body = info()
        elif a == "get_live_categories":
            body = live_cats
        elif a == "get_live_streams":
            c = request.query_params.get("category_id")
            body = {str(i): s for i, s in enumerate(x for x in chans if not c or x["category_id"] == c)}  # dictionnaire !
        elif a == "get_vod_categories":
            body = [{"category_id": "10", "category_name": "Films"}]
        elif a == "get_vod_streams":
            body = vods
        elif a == "get_vod_info":
            body = {"info": {"plot": "Un film.", "duration_secs": "5400", "cast": "A, B", "rating": "7.1"},
                    "movie_data": {"stream_id": request.query_params.get("vod_id")}}
        elif a == "get_series_categories":
            body = [{"category_id": "20", "category_name": "Séries"}]
        elif a == "get_series":
            body = sers
        elif a == "get_series_info":
            sid = int(request.query_params.get("series_id") or 0)
            body = {"info": {"name": "Série", "plot": "Intrigue"},
                    "episodes": {"1": [{"id": str(sid * 10 + e), "episode_num": e, "title": f"Épisode {e}",
                                        "container_extension": "mp4", "info": {"duration": "00:42:00"}} for e in (2, 1)],
                                 "2": [{"id": str(sid * 10 + 9), "episode_num": "1", "title": "Retour", "container_extension": "mkv"}]}}
        elif a == "get_short_epg" or a == "get_simple_data_table":
            body = {"epg_listings": epg_for(request.query_params.get("stream_id") or "")}
        else:
            body = []
        text = __import__("json").dumps(body)
        if php_warning:
            text = "<br />\n<b>Warning</b>: something in /var/www<br />\n" + text
        return PlainTextResponse(text, media_type="application/json")

    @app.get("/xmltv.php")
    def xml(request: Request):
        if not ok(request):
            return Response(status_code=403)
        base = now - now % 3600 - 24 * 3600
        parts = ['<?xml version="1.0" encoding="UTF-8"?><tv>']
        for c in chans:
            if c["epg_channel_id"]:
                parts.append(f'<channel id="{c["epg_channel_id"]}"><display-name>{c["name"]}</display-name></channel>')
        for c in chans:
            if not c["epg_channel_id"]:
                continue
            for k in range(48):
                s = dt.datetime.fromtimestamp(base + k * 3600, dt.timezone(dt.timedelta(hours=1)))
                e = s + dt.timedelta(hours=1)
                parts.append(f'<programme start="{s:%Y%m%d%H%M%S} +0100" stop="{e:%Y%m%d%H%M%S} +0100" '
                             f'channel="{c["epg_channel_id"]}"><title>Prog {k} &amp; co</title><desc>D{k}</desc></programme>')
        parts.append("</tv>")
        data = gzip.compress("".join(parts).encode())
        return Response(data, media_type="application/octet-stream")  # gzip sans le dire

    def serve(kind: str, user: str, pw: str, name: str):
        if user != USER or pw != PASS:
            return Response(status_code=403)
        f = media.get(kind)
        if f is None:
            return Response(status_code=404)
        return FileResponse(f)

    @app.get("/live/{user}/{pw}/{name}")
    def live(user: str, pw: str, name: str):
        return serve("live", user, pw, name)

    @app.get("/movie/{user}/{pw}/{name}")
    def movie(user: str, pw: str, name: str):
        return serve("movie", user, pw, name)

    @app.get("/series/{user}/{pw}/{name}")
    def episode(user: str, pw: str, name: str):
        return serve("movie", user, pw, name)

    @app.get("/timeshift/{user}/{pw}/{minutes}/{start}/{name}")
    def timeshift(user: str, pw: str, minutes: str, start: str, name: str):
        state["calls"].append(f"timeshift:{minutes}:{start}")
        return serve("live", user, pw, name)

    app.state.fake = state
    return app
