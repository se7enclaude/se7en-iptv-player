"""se7en IPTV Player : client Xtream, M3U, XMLTV, base locale et catalogue (contre un faux serveur, sans réseau)."""
import gzip
import io
import time

import pytest
from fastapi.testclient import TestClient

from medialab.errors import MediaLabError
from medialab.stream import catalog, m3u, sync, xmltv
from medialab.stream import xtream as X
from medialab.stream.store import Store, check_pin, hash_pin, is_adult, norm

from fake_xtream import PASS, USER, make_app


def client(app, user=USER, pw=PASS):
    return X.XtreamClient("http://testserver/player_api.php?x=1", user, pw, client=TestClient(app))


# ------------------------------------------------------------------ champs et adresses
def test_tolerant_fields():
    assert X.as_int("12") == 12 and X.as_int("7.9") == 7 and X.as_int(None, 3) == 3 and X.as_int("abc") is None
    assert X.as_float("7,5") == 7.5 and X.as_list({"0": 1, "1": 2}) == [1, 2] and X.as_list(None) == []
    assert X.as_bool("1") and not X.as_bool("0") and X.as_str(None) == ""
    assert X.maybe_b64("w4ltaXNzaW9u") == "Émission" and X.maybe_b64("Journal") == "Journal"


def test_server_normalization():
    assert X.normalize_server("monserveur.tv:8080") == "http://monserveur.tv:8080"
    assert X.normalize_server("https://a.b:8443/player_api.php?username=x") == "https://a.b:8443"
    assert X.normalize_server("http://a.b/get.php") == "http://a.b"
    with pytest.raises(MediaLabError):
        X.normalize_server("")


def test_urls_and_redaction():
    c = X.XtreamClient("http://s.tv:8080", "us er", "p@ss/w")
    assert c.live_url(5) == "http://s.tv:8080/live/us%20er/p%40ss%2Fw/5.ts"
    assert c.live_url(5, "m3u8").endswith("/5.m3u8")
    assert c.movie_url(7, "mkv").endswith("/movie/us%20er/p%40ss%2Fw/7.mkv")
    assert c.episode_url(9, ".mp4").endswith("/series/us%20er/p%40ss%2Fw/9.mp4")
    c.server_info = {"timezone": "Europe/Paris"}
    start = 1_792_000_000  # UTC
    url = c.timeshift_url(5, start, 45)
    local = X.to_server_time(start, c.server_info)
    assert f"/timeshift/us%20er/p%40ss%2Fw/45/{local:%Y-%m-%d:%H-%M}/5.ts" in url
    assert X.redact(url) == url.split("/timeshift/")[0] + f"/timeshift/***/***/45/{local:%Y-%m-%d:%H-%M}/5.ts"
    assert "s3cret" not in X.redact("http://a/xmltv.php?username=demo&password=s3cret")
    c.close()


def test_timezones():
    assert X.server_tz({"timezone": "UTC+02:00"}).utcoffset(None).total_seconds() == 7200
    assert X.server_tz({}).utcoffset(None).total_seconds() == 0


def test_account_problems():
    future = time.time() + 3600
    assert X.account_problem({"auth": 1, "status": "Active", "exp_date": str(int(future))}) is None
    assert X.account_problem({"auth": 1, "status": "Expired"})[0] == "account_expired"
    assert X.account_problem({"auth": 1, "status": "Active", "exp_date": "1000"})[0] == "account_expired"
    assert X.account_problem({"auth": 0})[0] == "auth_failed"
    assert X.connections({"active_cons": "1", "max_connections": "2"}) == (1, 2)
    assert X.connections({"max_connections": "0"}) == (0, None)


# ------------------------------------------------------------------ client contre le faux serveur
def test_auth_and_catalog_calls():
    app = make_app(php_warning=True)
    c = client(app)
    data = c.auth()
    assert data["user_info"]["max_connections"] == "2"
    assert len(c.live_streams()) == 30                # dictionnaire → liste
    assert len(c.live_streams("2")) == 14  # la dernière (impaire) est dans la catégorie adulte
    assert c.vod_info(2000)["info"]["duration_secs"] == "5400"
    epg = c.short_epg(1000)
    assert epg[0]["title"] == "Émission 0" and any(e["now"] for e in epg)
    assert c.output_format("ts") == "ts"


def test_bad_credentials_and_bad_server():
    with pytest.raises(MediaLabError) as e:
        client(make_app(), pw="faux").auth()
    assert e.value.code == "auth_failed"
    from fastapi import FastAPI
    plain = FastAPI()

    @plain.get("/player_api.php")
    def nope():
        return {"hello": "world"}
    with pytest.raises(MediaLabError) as e2:
        client(plain).auth()
    assert "Xtream" in e2.value.message


def test_json_rescue():
    assert X._json(b"Warning: x\n{\"a\": 1}") == {"a": 1}
    assert X._json(b"") == []
    with pytest.raises(MediaLabError):
        X._json(b"<html>error</html>")


# ------------------------------------------------------------------ M3U / XMLTV
M3U = """#EXTM3U
#EXTINF:-1 tvg-id="tf1.fr" tvg-name="TF1" tvg-logo="http://l/tf1.png" group-title="France, Généraliste" catchup="default" catchup-days="7",TF1 HD
#EXTVLCOPT:http-user-agent=Test
http://s/live/u/p/1.ts
#EXTINF:-1 group-title="Films",Un film, avec virgule
http://s/movie/u/p/2.mkv
#EXTINF:-1,Sans groupe
#EXTGRP:Divers
http://s/3.m3u8
"""


def test_m3u_parser():
    es = list(m3u.parse(io.StringIO(M3U)))
    assert [e["name"] for e in es] == ["TF1 HD", "Un film, avec virgule", "Sans groupe"]
    assert es[0]["group"] == "France, Généraliste" and es[0]["catchup"] == "default" and es[0]["opts"]["http-user-agent"] == "Test"
    assert [e["kind"] for e in es] == ["live", "movie", "live"] and es[2]["group"] == "Divers"
    assert es[0]["id"] == m3u.entry_id("http://s/live/u/p/1.ts") and "p/1" not in es[0]["id"]


def test_xmltv_parser_gzip_and_window():
    xml = ('<tv><channel id="a"><display-name>A</display-name></channel>'
           '<programme start="20261025200000 +0100" stop="20261025210000 +0100" channel="a"><title>T &amp; x</title></programme>'
           '<programme start="20200101000000 +0000" stop="20200101010000 +0000" channel="a"><title>Old</title></programme></tv>')
    got = list(xmltv.parse(io.BytesIO(gzip.compress(xml.encode())), since=1_700_000_000))
    assert got[0] == ("channel", {"id": "a", "names": ["A"], "icon": ""})
    progs = [g[1] for g in got if g[0] == "programme"]
    assert len(progs) == 1 and progs[0]["title"] == "T & x"
    assert progs[0]["start"] == xmltv.parse_time("20261025190000 +0000")
    assert xmltv.parse_time("20261025200000 -0500") - xmltv.parse_time("20261025200000 +0000") == 5 * 3600


# ------------------------------------------------------------------ base locale + synchro + catalogue
@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "s.db")


def add_fake_account(store, app):
    a = store.add_account("xtream", "Mon abo", server="http://testserver", username=USER, password=PASS)
    return a


def run_sync(store, app, a, monkeypatch):
    tc = TestClient(app)
    monkeypatch.setattr(sync, "client_for", lambda st, acc: _wrap(X.XtreamClient("http://testserver", USER, PASS, client=tc)))
    counts = sync.sync_catalog(store, a, lambda f, l: None)
    monkeypatch.setattr(sync, "_open_url", lambda url, ua: io.BytesIO(tc.get(url.replace("http://testserver", "")).content))
    n = sync.sync_epg(store, store.account(a["id"]), lambda f, l: None)
    return counts, n


class _wrap:
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        return self.c

    def __exit__(self, *a):
        pass

    def __getattr__(self, k):
        return getattr(self.c, k)


def test_password_is_encrypted(store):
    a = store.add_account("xtream", "x", server="http://s", username="u", password="motdepasse")
    raw = store.conn().execute("SELECT secret FROM accounts WHERE id=?", (a["id"],)).fetchone()[0]
    assert b"motdepasse" not in raw and "password" not in a and a["has_password"]
    assert store.account(a["id"], with_secret=True)["password"] == "motdepasse"


def test_sync_and_browse(store, monkeypatch):
    app = make_app(channels=40, vod=25, series=4)
    a = add_fake_account(store, app)
    counts, n = run_sync(store, app, a, monkeypatch)
    assert counts == {"live": 40, "movie": 25, "series": 4}
    assert n == 32 * 48  # 40 chaînes, 8 sans epg_id (une sur 5), 48 programmes chacune
    cats = catalog.categories(store, a["id"], "live", "main", show_adult=False)
    names = [c["name"] for c in cats]
    assert "XXX Adult" not in names and names[:2] == ["Tout", "Favoris"]
    assert next(c for c in cats if c["id"] == "__all")["n"] == 39  # la chaîne adulte est cachée
    assert any(c["name"] == "XXX Adult" for c in catalog.categories(store, a["id"], "live", "main", show_adult=True))
    page = catalog.items(store, a["id"], "live", "main", False, "1", limit=5)
    assert page["total"] == 20 and len(page["items"]) == 5 and "url" not in page["items"][0]
    found = catalog.items(store, a["id"], "live", "main", False, search="ete")  # sans accent
    assert [i["name"] for i in found["items"]] == ["Chaîne 4 Été"]
    with_now = catalog.items(store, a["id"], "live", "main", False, "__all", limit=3)["items"]
    assert with_now[1]["now"] and with_now[1]["now"]["title"].startswith("Prog")
    g = catalog.guide(store, a["id"], "main", False, "__all", int(time.time()) - 1800, hours=3, limit=10)
    assert len(g["channels"]) == 10 and g["channels"][1]["programmes"]


def test_favorites_hidden_history(store, monkeypatch):
    app = make_app()
    a = add_fake_account(store, app)
    run_sync(store, app, a, monkeypatch)
    aid = a["id"]
    catalog.toggle_favorite(store, "main", aid, "live", "1005", True)
    catalog.toggle_favorite(store, "main", aid, "live", "1001", True)
    favs = catalog.items(store, aid, "live", "main", False, "__fav")["items"]
    assert [f["id"] for f in favs] == ["1005", "1001"] and all(f["fav"] for f in favs)
    catalog.reorder_favorites(store, "main", aid, "live", ["1001", "1005"])
    assert [f["id"] for f in catalog.items(store, aid, "live", "main", False, "__fav")["items"]] == ["1001", "1005"]
    other = store.add_profile("Enfants", kids=True)
    assert catalog.items(store, aid, "live", other["id"], False, "__fav")["total"] == 0  # favoris par profil
    catalog.set_hidden(store, "main", aid, "live", "1000", True)
    assert "1000" not in [i["id"] for i in catalog.items(store, aid, "live", "main", False, limit=500)["items"]]
    catalog.save_progress(store, "main", aid, "movie", "2003", 600, 5400, "Film 4")
    assert catalog.resume_point(store, "main", aid, "movie", "2003") == 600
    assert catalog.continue_watching(store, "main", aid)[0]["item"] == "2003"
    catalog.save_progress(store, "main", aid, "movie", "2003", 5300, 5400, "Film 4")
    assert catalog.resume_point(store, "main", aid, "movie", "2003") == 0 and catalog.continue_watching(store, "main", aid) == []


def test_series_info_sorted_and_cached(store, monkeypatch):
    app = make_app()
    a = add_fake_account(store, app)
    run_sync(store, app, a, monkeypatch)
    c = X.XtreamClient("http://testserver", USER, PASS, client=TestClient(app))
    info = catalog.series_info(store, c, a["id"], "3000", "main")
    assert [s["season"] for s in info["seasons"]] == ["1", "2"]
    assert [e["episode"] for e in info["seasons"][0]["episodes"]] == [1, 2]
    again = catalog.series_info(store, None, a["id"], "3000", "main")  # sans réseau : depuis le cache
    assert again["seasons"] == info["seasons"]


def test_m3u_account(store, tmp_path):
    f = tmp_path / "liste.m3u"
    f.write_text(M3U, "utf-8")
    a = store.add_account("m3u", "Ma liste", m3u_url=str(f))
    counts = sync.sync_catalog(store, a, lambda *x: None)
    assert counts == {"live": 2, "movie": 1, "series": 0}
    ch = store.q("SELECT * FROM channels WHERE account=? ORDER BY position", (a["id"],))
    assert ch[0]["archive"] == 1 and ch[0]["archive_days"] == 7 and ch[0]["epg_id"] == "tf1.fr"


def test_pins_and_adult_words():
    h = hash_pin("1234")
    assert check_pin("1234", h) and not check_pin("0000", h) and check_pin("x", None)
    assert is_adult("XXX Adult") and is_adult("Adultes 18+") and not is_adult("Sport")
    assert norm("Été  Évasion") == "ete evasion"


def test_big_catalog_is_fast(store, monkeypatch):
    app = make_app(channels=20000, vod=40000, series=100)
    a = add_fake_account(store, app)
    tc = TestClient(app)
    monkeypatch.setattr(sync, "client_for", lambda st, acc: _wrap(X.XtreamClient("http://testserver", USER, PASS, client=tc)))
    t = time.perf_counter()
    sync.sync_catalog(store, a, lambda f, l: None)
    sync_s = time.perf_counter() - t
    t = time.perf_counter()
    for _ in range(5):
        catalog.items(store, a["id"], "movie", "main", False, "__all", "film 39", limit=60)
        catalog.items(store, a["id"], "live", "main", False, "2", limit=200)
    query_ms = (time.perf_counter() - t) / 10 * 1000
    print(f"\n60 100 entrées : synchro {sync_s:.1f}s, requête {query_ms:.0f} ms")
    assert query_ms < 150


def test_xmltv_repairs_common_faults():
    from medialab.stream import xmltv
    body = ('<?xml version="1.0"?><tv><channel id="a"><display-name>Golf & Co\x01</display-name></channel>'
            + "".join(f'<programme start="202610{25 + k % 2}20{k % 60:02d}00 +0100" stop="20261025210000 +0100" channel="a">'
                      f'<title>Tom & Jerry &amp; Cie &#233; {k}</title></programme>' for k in range(3000))
            + '<programme start="20261025220000 +0100" channel="a"><title>Cassé <b></title></programme></tv>')
    raw = gzip.compress(body.encode())
    got = []
    with pytest.raises(xmltv.Broken):
        for kind, item in xmltv.parse(io.BytesIO(raw)):
            got.append((kind, item))
    assert got[0] == ("channel", {"id": "a", "names": ["Golf & Co"], "icon": ""})
    progs = [i for k, i in got if k == "programme"]
    assert len(progs) == 3000 and progs[0]["title"] == "Tom & Jerry & Cie é 0"
    assert xmltv.parse_time("20261332250000 +0100") is None


def test_provider_that_only_accepts_known_players():
    """Behind some proxies a good login is refused (401) unless the player announces itself as one they know."""
    app = make_app()

    @app.middleware("http")
    async def only_vlc(request, call_next):
        from fastapi.responses import PlainTextResponse
        if not request.headers.get("user-agent", "").startswith("VLC/"):
            return PlainTextResponse("", status_code=401)
        return await call_next(request)

    c = client(app)
    assert c.auth()["user_info"]["auth"] == 1
    assert c.user_agent.startswith("VLC/") and len(c.live_categories()) > 0
    # a user agent chosen in the account is kept as is: the refusal is then a real one
    picked = X.XtreamClient("http://testserver", USER, PASS, client=TestClient(app), user_agent="MyBox/1.0")
    with pytest.raises(MediaLabError) as e:
        picked.auth()
    assert e.value.code == "auth_failed" and picked.user_agent == "MyBox/1.0"


# ------------------------------------------------------------------ versions SD / HD / FHD / 4K
def test_quality_names_are_grouped():
    from medialab.stream import quality as Q
    assert Q.parse("FR | TF1 FHD (HEVC)") == ("fr tf1", "FHD", 3)
    assert Q.parse("FR: TF1 HD")[0] == Q.parse("FR: TF1 ᴴᴰ")[0] == Q.parse("FR - TF1 4K UHD")[0] == Q.parse("FR TF1")[0] == "fr tf1"
    assert Q.parse("Canal+ Sport Full HD") == ("canal+ sport", "FHD", 3)
    assert Q.parse("Dune (2021) 4K")[0] != Q.parse("Dune (1984)")[0]
    assert Q.parse("HD1")[1] == "" and Q.parse("SD")[0] == "sd"


def test_lists_show_one_version_per_group(tmp_path):
    from medialab.stream import quality as Q
    st = Store(tmp_path / "s.db")
    names = ["TF1 SD", "TF1 HD", "TF1 FHD", "TF1 4K", "France 2 HD", "France 2 FHD", "Arte"]
    st.many("INSERT INTO channels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [("a", str(i), i + 1, n, norm(n), "", "c1", "", 0, 0, None, 0, i) for i, n in enumerate(names)])
    Q.index(st, "a", "live")
    best = catalog.items(st, "a", "live", "main", True, quality="best")
    assert best["total"] == 3 and [r["name"] for r in best["items"]] == ["TF1 4K", "France 2 FHD", "Arte"]
    tf1 = best["items"][0]
    assert [v["quality"] for v in tf1["variants"]] == ["4K", "FHD", "HD", "SD"] and best["items"][2]["variants"] == []
    # « HD » : la version HD, sinon la plus proche en dessous
    hd = catalog.items(st, "a", "live", "main", True, quality="hd")
    assert [r["name"] for r in hd["items"]] == ["TF1 HD", "France 2 HD", "Arte"]
    assert catalog.items(st, "a", "live", "main", True)["total"] == 7  # sans regroupement : tout
    assert catalog.group_ids(st, "a", "live", "main", True, "c1", "best") == ["3", "5", "6"]
    assert sorted(catalog.group_of(st, "a", "live", "0")) == ["0", "1", "2", "3"]


# ------------------------------------------------------------------ recherche intelligente
def test_smart_search_tolerates_typos_and_finds_people(tmp_path):
    from medialab.stream import quality as Q, search
    st = Store(tmp_path / "s.db")
    st.run("INSERT INTO accounts (id, kind, name, synced) VALUES ('a', 'xtream', 'x', 1)")
    st.many("INSERT INTO categories VALUES (?,?,?,?,?,?)", [("a", "movie", "c1", "Films", 0, 0), ("a", "movie", "c2", "Comédie", 1, 0)])
    films = ["Spider-Man: No Way Home", "Spider-Man 4K", "Léon", "Les Visiteurs", "Taxi 2", "Le Parrain"]
    st.many("INSERT INTO vod VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [("a", str(i), n, norm(n), "", "c2" if n == "Taxi 2" else "c1", 7.0, 2000, 0, "mp4", None, i) for i, n in enumerate(films)])
    st.run("INSERT INTO people VALUES ('a', 'movie', '2', 'thriller', 'jean reno, natalie portman', 'luc besson')")
    st.run("INSERT INTO people VALUES ('a', 'movie', '3', 'comedie', 'jean reno, christian clavier', 'jean-marie poire')")
    Q.index(st, "a", "movie")

    def names(q):
        return [r["name"] for r in search.search(st, "a", "main", True, q, "best")["movie"]]
    assert names("spidrman")[:1] in (["Spider-Man: No Way Home"], ["Spider-Man 4K"])
    assert names("leon") == ["Léon"]  # accents
    assert names("parain") == ["Le Parrain"]  # une faute
    assert set(names("jean reno")) == {"Léon", "Les Visiteurs"}
    r = search.search(st, "a", "main", True, "jean reno", "best")["movie"][0]
    assert r["why"] == "Avec Jean Reno"
    assert "Taxi 2" in names("comedie")  # catégorie
    assert names("zzzz") == []


# ------------------------------------------------------------------ zapping instantané
def test_preload_gives_way_and_swaps():
    from medialab.stream import player as P
    from medialab.stream.recorder import Connections

    # une chaîne ouverte d'avance cède sa connexion à un enregistrement
    c = Connections()
    evicted = []
    c.on_evict = evicted.append
    c.take("a", "player", "la lecture", 2)
    c.take("a", "preload", "le zapping instantané", 2)
    c.take("a", "rec:1", "un enregistrement", 2)
    assert evicted == ["a"] and c.count("a") == 2
    with pytest.raises(MediaLabError):
        c.take("a", "preload", "le zapping instantané", 2)

    class FakeMpv(dict):
        def __init__(self):
            super().__init__(volume=80, mute=False)
            self.cmds = []
        __getattr__ = dict.get

        def __setattr__(self, k, v):
            if k == "cmds":
                return super().__setattr__(k, v)
            self[k] = v

        def command(self, *a):
            self.cmds.append(a)

        def loadfile(self, *a, **k):
            self.cmds.append(("loadfile",) + a)

    class FakeHost:
        child = True

        def __init__(self):
            self.shown = None

        def place(self, x, y, w, h, visible, radius=0):
            self.shown = visible

    p = P.Player("t", lambda: {})
    p.mpv, p.host = FakeMpv(), FakeHost()
    p.rect = {"x": 0, "y": 0, "w": 640, "h": 360, "dpr": 1.0, "visible": True, "radius": 0}
    spare_m, spare_h = FakeMpv(), FakeHost()
    p._spare = {"mpv": spare_m, "host": spare_h, "id": "5", "url": "http://s/live/u/p/5.ts", "since": 0}
    old_m, old_h = p.mpv, p.host
    p.play("http://s/live/u/p/5.ts", {"kind": "live", "id": "5", "title": "Cinq"})
    assert p.mpv is spare_m and p.host is spare_h and spare_h.shown is True and old_h.shown is False
    assert ("stop",) in old_m.cmds and spare_m["volume"] == 80 and spare_m["mute"] is False
    assert p.preloaded() is None and p._spare["mpv"] is old_m and p.item["id"] == "5"
    # une autre chaîne : chargement normal, sans échange
    p.play("http://s/live/u/p/6.ts", {"kind": "live", "id": "6"})
    assert p.mpv is spare_m and ("loadfile", "http://s/live/u/p/6.ts", "replace") in spare_m.cmds


def test_fullscreen_bubbles_and_menus():
    """Les bulles du bandeau : chaque clic fait ce qui est écrit, la liste des sous-titres choisit une piste."""
    from medialab.stream import player as P

    class M(dict):
        __getattr__ = dict.get
        duration = 6000.0

        def __setattr__(self, k, v):
            self[k] = v

        def seek(self, *a):
            self.setdefault("seeks", []).append(a)

        def __setitem__(self, k, v):
            dict.__setitem__(self, k, v)

    p = P.Player("t", lambda: {})
    p.mpv = M(pause=False, mute=False, volume=100)
    p.item = {"kind": "movie", "id": "1", "title": "Film"}
    p.fullscreen = True
    actions = []
    p.on_action = lambda a, d: actions.append((a, d))
    tracks = [{"id": 1, "type": "sub", "lang": "fre", "selected": True}, {"id": 2, "type": "sub", "lang": "eng"}]

    def draw(**extra):
        _, p.hits = P.osd({**p.item, "controls": True, "fullscreen": True, "duration": 6000, "position": 60, "tracks": tracks,
                           "menu": p.menu, **extra})

    def center(hid):
        h = next(h for h in p.hits if h["id"] == hid)
        return h["x"] + h["w"] / 2, h["y"] + h["h"] / 2

    draw()
    assert p._click_at(*center("pause")) == "pause" and p.mpv["pause"] is True
    assert p._click_at(*center("mute")) == "mute" and p.mpv["mute"] is True
    p._click_at(*center("exit_fs"))
    assert actions[-1] == ("exit_fullscreen", {})
    p._click_at(*center("subs"))
    assert p.menu == "sid"
    draw()
    assert p._click_at(*center("track:sid:2")) == "track:sid:2" and p.mpv["sid"] == 2 and p.menu is None
    p._click_at(*center("subs"))
    draw()
    p._click_at(*center("track:sid:no"))
    assert p.mpv["sid"] == "no"
    assert p._click_at(960, 300) == "video"  # sur l'image : rien (deux de suite = plein écran, géré par _input_loop)
    # en direct : chaîne − / +
    p.item = {"kind": "live", "id": "9", "title": "TV"}
    _, p.hits = P.osd({**p.item, "controls": True, "fullscreen": True})
    p._click_at(*center("zap_down"))
    assert actions[-1] == ("zap", {"delta": 1})


def test_duration_estimated_when_the_file_has_none():
    """Certains films (.ts sans en-tête) n'ont pas de durée : on l'estime, et la barre vise un pourcentage."""
    from medialab.stream import player as P

    class M:
        duration = None
        percent_pos = 25.0
        time_pos = 600.0

        def __init__(self):
            self.seeks = []

        def seek(self, *a):
            self.seeks.append(a)

    p = P.Player("t", lambda: {})
    p.mpv = M()
    p.item = {"kind": "movie", "id": "1"}
    assert p._duration() == 2400
    p.seek(1200, absolute=True)
    assert p.mpv.seeks[-1] == (50.0, "absolute-percent")
    p.mpv.duration = 3000.0
    p.seek(1200, absolute=True)
    assert p.mpv.seeks[-1] == (1200, "absolute")
