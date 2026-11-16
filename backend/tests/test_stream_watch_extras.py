"""Épisode suivant pendant le générique, réglages du plein écran (sous-titres, vitesse, format), image fluide / HDR,
et compléments des fiches (distribution, bande-annonce, note IMDb)."""
import gzip
import time

import pytest

from medialab.stream import api as A
from medialab.stream import extras as E
from medialab.stream import player as P
from medialab.stream.store import Store


class M(dict):
    """Un faux mpv : ses propriétés sont des clés."""
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v

    def command(self, *a):
        pass


def _player(**item):
    p = P.Player("t", lambda: {})
    p.settings = lambda: {"auto_next": True}
    p.mpv = M(pause=False, mute=False, volume=100, sub_delay=0.0, sub_scale=1.0, speed=1.0, chapter_list=[])
    p.item = {"kind": "episode", "id": "e2", "series": "s1", "title": "S1 E2 · Ep 2", "subtitle": "|FR| Série (4K)", **item}
    actions = []
    p.on_action = lambda a, d: actions.append((a, d))
    return p, actions


# ------------------------------------------------------------------ épisode suivant
def test_next_episode_of_crosses_seasons():
    info = {"seasons": [{"season": "1", "episodes": [{"id": "a", "episode": 1, "title": "Un"}, {"id": "b", "episode": 2, "title": "Deux"}]},
                        {"season": "2", "episodes": [{"id": "c", "episode": 1, "title": "|FR| Retour"}]}]}
    assert A.next_episode_of(info, "a")["id"] == "b"
    assert A.next_episode_of(info, "b") == {"id": "c", "title": "|FR| Retour", "label": "S2 E1 · |FR| Retour"}
    assert A.next_episode_of(info, "c") is None and A.next_episode_of(info, "zz") is None


def test_card_appears_in_the_credits_counts_down_then_plays_the_next_episode(monkeypatch):
    p, actions = _player(next_ep={"id": "e3", "title": "Ep 3", "label": "S1 E3 · |FR| Ep 3 (4K)"})
    st = {"duration": 3000.0, "position": 2000.0, "paused": False}
    assert p._next_state(st) is None  # en plein épisode : rien
    st["position"] = 2950.0  # dans les 90 dernières secondes
    card = p._next_state(st)
    assert card["title"] == "S1 E3 · Ep 3" and card["left"] == pytest.approx(P.NEXT_COUNTDOWN, abs=0.1)
    # le compte à rebours s'arrête en pause
    p._nx["t"] -= 4
    st["paused"] = True
    assert p._next_state(st)["left"] == pytest.approx(P.NEXT_COUNTDOWN, abs=0.1)
    st["paused"] = False
    p._nx["t"] -= P.NEXT_COUNTDOWN + 1
    started = []
    monkeypatch.setattr(P.threading, "Thread", lambda target, args, **k: type("T", (), {"start": lambda self: started.append(args)})())
    assert p._next_state(st) is None
    assert started == [("next_episode", {"id": "e3", "title": "Ep 3", "label": "S1 E3 · |FR| Ep 3 (4K)", "series": "s1"})]
    assert p._next_state(st) is None  # une seule fois


def test_stay_hides_the_card_and_a_credits_chapter_is_used():
    p, _ = _player(next_ep={"id": "e3", "label": "S1 E3"})
    p.mpv["chapter_list"] = [{"title": "Intro", "time": 0}, {"title": "Générique de fin", "time": 2700.0}]
    st = {"duration": 3000.0, "position": 2710.0, "paused": False}
    assert p._next_state(st) is not None  # le chapitre « Générique » commence avant les 90 dernières secondes
    p._next_choice(False)
    assert p._next_state(st) is None
    p.settings = lambda: {"auto_next": False}
    p._nx = None
    assert p._next_state(st) is None  # réglage coupé


def test_card_buttons_are_clickable_even_in_a_window():
    data, hits = P.osd({"kind": "episode", "title": "x", "controls": False, "next_card": {"title": "S1 E3", "left": 4}})
    assert {"next_now", "next_stay"} <= {h["id"] for h in hits} and "Lecture dans 4 s" in data
    p, _ = _player(next_ep={"id": "e3", "label": "S1 E3"})
    p._nx = {"id": "e2", "left": 5, "t": time.monotonic(), "stay": False, "fired": False}
    p.hits = hits
    h = next(h for h in hits if h["id"] == "next_stay")
    assert p._click_at(h["x"] + 5, h["y"] + 5) == "next_stay" and p._nx["stay"]


# ------------------------------------------------------------------ menus du plein écran
def _click(p, hid):
    h = next(h for h in p.hits if h["id"] == hid)
    return p._click_at(h["x"] + h["w"] / 2, h["y"] + h["h"] / 2)


def test_subtitle_delay_and_size_from_the_subtitle_list():
    p, actions = _player()
    tracks = [{"id": 1, "type": "sub", "lang": "fre", "selected": True}]
    st = {**p.item, "controls": True, "fullscreen": True, "duration": 3000, "position": 10, "tracks": tracks, "menu": "sid",
          "sub_delay": 0.0, "sub_scale": 1.0}
    _, p.hits = P.osd(st)
    p.menu = "sid"
    _click(p, "subdelay:+")
    _click(p, "subdelay:+")
    _click(p, "subsize:+")
    assert p.mpv["sub_delay"] == 1.0 and p.mpv["sub_scale"] == 1.1 and p.menu == "sid"
    assert actions[-1] == ("sub_scale", {"value": 1.1})
    _, p.hits = P.osd({**st, "sub_delay": 1.0, "sub_scale": 1.1})
    _click(p, "subdelay:0")
    assert p.mpv["sub_delay"] == 0.0


def test_speed_and_picture_format_from_the_settings_button():
    p, _ = _player()
    st = {**p.item, "controls": True, "fullscreen": True, "duration": 3000, "position": 10, "tracks": []}
    _, p.hits = P.osd(st)
    _click(p, "opts")
    assert p.menu == "opt"
    _, p.hits = P.osd({**st, "menu": "opt", "speed": 1.0, "aspect": "-1"})
    _click(p, "speed:1.5")
    _click(p, "aspect:4:3")
    assert p.mpv["speed"] == 1.5 and p.mpv["video_aspect_override"] == "4:3" and p.mpv["panscan"] == 0.0
    _click(p, "aspect:zoom")
    assert p.mpv["panscan"] == 1.0
    for h in p.hits:
        assert 0 <= h["x"] and h["x"] + h["w"] <= 1920 and 0 <= h["y"] and h["y"] + h["h"] <= 1080


def test_live_settings_menu_has_no_speed():
    _, hits = P.osd({"kind": "live", "title": "TF1", "controls": True, "fullscreen": True, "menu": "opt"})
    ids = {h["id"] for h in hits}
    assert "aspect:16:9" in ids and not any(i.startswith("speed:") for i in ids)


def test_smooth_and_hdr_options():
    on = P.video_options({})
    assert on["interpolation"] == "yes" and on["video_sync"] == "display-resample" and on["target_colorspace_hint"] == "auto"
    off = P.video_options({"smooth": False, "hdr": False})
    assert off["interpolation"] == "no" and off["target_colorspace_hint"] == "no"


# ------------------------------------------------------------------ fiches
def _wd(rows):
    return {"results": {"bindings": [{k: {"value": v} for k, v in r.items()} for r in rows]}}


def test_cast_ordered_by_credits_then_fame_with_photos():
    res = _wd([{"imdb": "tt0068646", "yt": "abcdef12", "dirLabel": "Francis Ford Coppola", "castLabel": "Petit rôle", "links": "3"},
               {"castLabel": "Marlon Brando", "img": "http://commons.wikimedia.org/wiki/Special:FilePath/Brando.jpg", "links": "90"},
               {"castLabel": "Al Pacino", "links": "80"}, {"castLabel": "Q123456"}])
    f = E.parse_facts(res)
    assert [c["name"] for c in f["cast"]] == ["Marlon Brando", "Al Pacino", "Petit rôle"]
    assert f["cast"][0]["photo"].startswith("https://") and f["cast"][0]["photo"].endswith("?width=240")
    assert (f["imdb"], f["trailer"], f["director"]) == ("tt0068646", "abcdef12", "Francis Ford Coppola")


def test_imdb_ratings_file_and_sheet_fill(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    tsv = "tconst\taverageRating\tnumVotes\ntt0068646\t9.2\t2272738\ntt0944947\t9.2\t2684863\nbad\tx\ty\n"
    assert E.build_ratings(gzip.compress(tsv.encode()), db) == 2
    assert E.imdb_rating("tt0068646", db=db, refresh=False) == {"rating": 9.2, "votes": 2272738}
    assert E.imdb_rating("nope", db=db, refresh=False) is None
    monkeypatch.setattr(E, "ratings_db", lambda: db)
    monkeypatch.setattr(E, "update_ratings_later", lambda: None)
    st = Store(tmp_path / "s.db")
    res = _wd([{"imdb": "tt0068646", "yt": "abcdef12", "castLabel": "Marlon Brando", "img": "http://x/Special:FilePath/B.jpg"}])
    d = E.fill(st, "movie", {"cast_list": [{"name": "Marlon Brando", "role": "", "photo": ""}], "trailer": ""}, "238", fetch=lambda q: res)
    assert d["imdb_rating"] == 9.2 and d["trailer"] == "abcdef12" and d["cast_list"][0]["photo"]
    # gardé : pas de deuxième appel à Wikidata
    d2 = E.fill(st, "movie", {}, "238", fetch=lambda q: (_ for _ in ()).throw(AssertionError("appel inutile")))
    assert d2["imdb"] == "tt0068646"


def test_sheet_enrichment_calls_the_extras(tmp_path, monkeypatch):
    """catalog.enrich complète la fiche (sans clé TMDB) avec la note IMDb, la distribution et la bande-annonce."""
    from medialab.stream import catalog, tmdb
    monkeypatch.setattr(tmdb, "details", lambda *a, **k: None)
    seen = []
    monkeypatch.setattr(E, "fill", lambda st, typ, d, t, fetch=None: seen.append((typ, t)) or d.update(imdb_rating=9.2) or d)
    st = Store(tmp_path / "s.db")
    d = catalog.enrich(st, "acc", "movie", {"id": "10", "name": "|FR| Le Parrain", "tmdb_id": "238"}, "p", True, "best")
    assert seen == [("movie", "238")] and d["imdb_rating"] == 9.2
