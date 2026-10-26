"""Routes de se7en IPTV Player de bout en bout contre un faux serveur Xtream lancé pour de vrai (HTTP local)."""
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from fake_xtream import PASS, USER, make_app
from medialab.app import Services, create_app
from medialab.stream import player as P

TOKEN = "test-token"
H = {"X-MediaLab-Token": TOKEN}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(make_app(channels=40, vod=25, series=4, max_cons=1), host="127.0.0.1", port=port,
                                        log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


@pytest.fixture(scope="module")
def api(server):
    played = []
    orig = P.Player.play, P.Player.stop
    P.Player.play = lambda self, url, item, start=0.0, user_agent="": played.append((url, item, start))
    P.Player.stop = lambda self: None
    services = Services(token=TOKEN)
    with TestClient(create_app(services)) as c:
        c.played = played
        yield c
    P.Player.play, P.Player.stop = orig


def wait_sync(api, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        jobs = [j for j in api.get("/api/jobs", headers=H).json()["jobs"] if j["kind"] == "stream_sync"]
        if jobs and all(j["status"] in ("done", "completed", "failed", "error") for j in jobs):
            return jobs
        time.sleep(0.2)
    raise AssertionError("sync trop long")


def test_full_flow(api, server, tmp_path):
    st = api.get("/api/stream/state", headers=H).json()
    for a in st["accounts"]:  # données d'une exécution précédente
        api.delete(f"/api/stream/accounts/{a['id']}", headers=H)
    assert api.get("/api/stream/home", headers=H).json()["code"] == "no_account"

    bad = api.post("/api/stream/accounts/test", headers=H, json={"server": server, "username": USER, "password": "nope"})
    assert bad.status_code >= 400

    t = api.post("/api/stream/accounts/test", headers=H, json={"server": server, "username": USER, "password": PASS}).json()
    assert t["counts"] == {"live": 40, "movie": 25, "series": 4} and t["max"] == 1 and "info" not in t

    r = api.post("/api/stream/accounts", headers=H, json={"server": server, "username": USER, "password": PASS, "name": "Test"})
    assert r.status_code == 200, r.text
    assert PASS not in r.text
    jobs = wait_sync(api)
    assert jobs[0]["status"] in ("done", "completed"), jobs[0]

    st = api.get("/api/stream/state", headers=H).json()
    assert st["account"] and st["accounts"][0]["status"]["max"] == 1 and PASS not in str(st)

    cats = api.get("/api/stream/categories/live", headers=H).json()["categories"]
    assert any(c["id"] == "2" for c in cats)
    items = api.get("/api/stream/items/live?category=2", headers=H).json()
    assert items["total"] == next(c["n"] for c in cats if c["id"] == "2") > 0
    ch = items["items"][0]

    g = api.get("/api/stream/guide", headers=H).json()
    assert g["channels"] and any(c.get("programmes") for c in g["channels"])

    # lecture live : adresse au bon format, connexion comptée
    r = api.post("/api/stream/play", headers=H, json={"type": "live", "id": ch["id"]})
    assert r.status_code == 200, r.text
    url, item, _ = api.played[-1]
    assert f"/live/{USER}/{PASS}/{ch['id']}." in url and item["title"] == ch["name"]

    # enregistrement : refusé car l'abonnement n'a qu'une connexion et la lecture l'utilise
    rec = api.post("/api/stream/recordings", headers=H, json={"channel": ch["id"], "minutes": 1}).json()
    for _ in range(50):  # the refusal comes from the recorder thread: wait for it (a busy PC can take a while)
        recs = api.get("/api/stream/recordings", headers=H).json()["recordings"]
        mine = next(x for x in recs if x["id"] == rec["id"])
        if mine["status"] not in ("scheduled", "recording"):
            break
        time.sleep(0.1)
    assert mine["status"] == "failed" and "connexion" in (mine["error"] or "")
    api.post("/api/stream/player", headers=H, json={"cmd": "stop"})

    # film avec reprise
    mv = api.get("/api/stream/items/movie", headers=H).json()["items"][0]
    api.post("/api/stream/watched", headers=H, json={"type": "movie", "item": mv["id"], "on": False})
    from medialab.stream import catalog
    s = api.app.state.stream
    catalog.save_progress(s["store"], s["session"].profile, s["session"].account, "movie", mv["id"], 600, 5400)
    api.post("/api/stream/play", headers=H, json={"type": "movie", "id": mv["id"]})
    url, item, start = api.played[-1]
    # le lecteur passe par le relais local (une seule connexion vers le fournisseur), qui pointe vers le bon film
    from medialab.stream.relay import relay
    assert url.startswith("http://127.0.0.1:") and USER not in url and PASS not in url and start == 600
    target = relay._targets[url.split("/")[4]][0]
    assert f"/movie/{USER}/{PASS}/{mv['id']}." in target
    api.post("/api/stream/player", headers=H, json={"cmd": "stop"})

    # série
    se = api.get("/api/stream/items/series", headers=H).json()["items"][0]
    info = api.get(f"/api/stream/series/{se['id']}", headers=H).json()
    assert info["seasons"]

    # favoris, recherche
    api.post("/api/stream/favorite", headers=H, json={"type": "live", "item": ch["id"]})
    fav = api.get("/api/stream/items/live?category=__fav", headers=H).json()["items"]
    assert [f["id"] for f in fav] == [ch["id"]]
    assert api.get("/api/stream/search?q=" + ch["name"][:5], headers=H).json()["live"]

    # contrôle parental
    api.post("/api/stream/parental/pin", headers=H, json={"new_pin": "1234"})
    assert api.post("/api/stream/parental/unlock", headers=H, json={"pin": "0000"}).status_code >= 400
    assert api.post("/api/stream/parental/unlock", headers=H, json={"pin": "1234"}).json()["ok"]
    api.post("/api/stream/parental/lock", headers=H)
    assert api.get("/api/stream/settings", headers=H).json()["parental_pin"] is True

    # sauvegarde chiffrée : rien en clair, mot de passe vérifié
    f = tmp_path / "save.s7s"
    api.post("/api/stream/backup/export", headers=H, json={"path": str(f), "password": "motdepasse"})
    assert PASS.encode() not in f.read_bytes()
    assert api.post("/api/stream/backup/import", headers=H, json={"path": str(f), "password": "faux!!"}).status_code >= 400
    assert api.post("/api/stream/backup/import", headers=H, json={"path": str(f), "password": "motdepasse"}).json()["accounts"] == 0

    api.post("/api/stream/parental/pin", headers=H, json={"pin": "1234", "new_pin": ""})



def test_phone_remote(tmp_path):
    """La télécommande : rien sans la clé ; avec elle, l'état et les commandes passent par les fonctions de l'appli."""
    from medialab.stream.remote import Remote
    from medialab.stream.store import Store
    calls = []
    r = Remote(Store(tmp_path / "s.db"), lambda: {"playing": False, "channels": []}, lambda c, v: calls.append((c, v)))
    tc = TestClient(r._app())
    tok = r.token()
    assert tc.get("/r/wrong").status_code == 404 and tc.get("/r/wrong/api/state").status_code == 404
    page = tc.get(f"/r/{tok}")
    assert page.status_code == 200 and "télécommande" in page.text and page.headers["cache-control"] == "no-store"
    assert tc.get(f"/r/{tok}/api/state").json() == {"playing": False, "channels": []}
    assert tc.post(f"/r/{tok}/api/cmd", json={"cmd": "zap", "value": 1}).json()["ok"] is True
    assert calls == [("zap", 1)]
    old = tok
    assert r.new_token() != old and tc.get(f"/r/{old}").status_code == 404
