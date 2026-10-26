"""Pistes audio et sous-titres : noms lisibles, liste qui tient dans l'écran même avec 30 sous-titres, langue retenue
reprise au film suivant, et (vrai libmpv) pas de sous-titres complets par-dessus un film dans la même langue."""
import subprocess
import time

import pytest

from medialab.stream import player as P


def t(id_, kind="sub", lang=None, title=None, **kw):
    return {"id": id_, "type": kind, "lang": lang, "title": title, **kw}


def test_labels_names_flags_and_duplicates():
    audio = [t(1, "audio", "fre", "VFF"), t(2, "audio", "fre", "VFQ", channels=6), t(3, "audio", "eng", channels=6),
             t(4, "audio", "qaa"), t(5, "audio", "fr-FR"), t(6, "audio", "und"), t(7, "audio", "kor", "audio_2")]
    assert P.track_labels(audio) == ["Français · VFF", "Français · VFQ · 5.1", "Anglais · 5.1", "Version originale",
                                     "Français", "Piste 6", "Coréen"]
    subs = [t(1, lang="fre"), t(2, lang="fre", forced=True), t(3, lang="fre", title="SDH", hearing_impaired=True),
            t(4, lang="gre"), t(5, lang="xyz")]
    assert P.track_labels(subs) == ["Français", "Français · Forcés", "Français · SDH", "Grec", "XYZ"]
    same = [t(1, "audio", "eng", codec="ac3"), t(2, "audio", "eng", codec="aac"), t(3, "audio", "eng", codec="aac")]
    assert P.track_labels(same) == ["Anglais · Dolby Digital", "Anglais · AAC (1)", "Anglais · AAC (2)"]


@pytest.mark.parametrize("n", [1, 12, 33, 60])
def test_track_menu_fits_on_screen(n):
    subs = [t(i + 1, lang="fre", selected=False) for i in range(n)]
    st = {"kind": "movie", "id": "1", "title": "Film", "controls": True, "fullscreen": True, "duration": 100,
          "position": 1, "volume": 100, "menu": "sid", "tracks": subs}
    _data, hits = P.osd(st)
    rows = [h for h in hits if h["id"].startswith("track:sid:")]
    assert len(rows) == n + 1  # + « Désactivés »
    for h in rows:
        assert h["y"] >= 0 and h["x"] >= 0 and h["x"] + h["w"] <= 1920 and h["y"] + h["h"] <= 1080
    boxes = {(h["x"], h["y"]) for h in rows}
    assert len(boxes) == len(rows)  # aucune piste cachée sous une autre


class FakeMpv:
    def __init__(self):
        self.aid = self.sid = None
        self.calls = []
        self.user_agent = ""
        self.pause = False

    def loadfile(self, url, mode, **opts):
        self.calls.append({k: v.split("%", 2)[2] for k, v in opts.items()})


@pytest.mark.parametrize("langs,expected", [
    (None, {}),
    ({"aid": "eng"}, {"alang": "eng,fr,fre,fra,en,eng"}),
    ({"sid": "no"}, {"slang": "none", "subs_fallback": "no"}),
    ({"aid": "fre", "sid": "fre"}, {"alang": "fre,fr,fre,fra,en,eng", "slang": "fre,fr,fre,fra",
                                    "subs_with_matching_audio": "yes"}),
])
def test_remembered_languages_used_for_next_film(monkeypatch, langs, expected):
    p = P.Player("t", lambda: {})
    p.mpv = FakeMpv()
    monkeypatch.setattr(p, "_ensure", lambda: p.mpv)
    monkeypatch.setattr(p, "show_osd", lambda *a: None)
    p.play("http://x/film.mkv", {"kind": "movie", "id": "1"}, 0, "", langs)
    assert p.mpv.calls == [expected]


@pytest.fixture
def default_subs_mp4(tmp_path, ffmpeg_bin):
    (tmp_path / "fr.srt").write_text("1\n00:00:00,000 --> 00:00:05,000\nBonjour\n", encoding="utf-8")
    out = tmp_path / "film.mp4"
    subprocess.run([ffmpeg_bin, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-i", str(tmp_path / "fr.srt"), "-t", "5",
                    "-map", "0:v", "-map", "1:a", "-map", "2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-c:s", "mov_text", "-metadata:s:a:0", "language=fre", "-metadata:s:s:0", "language=fre",
                    "-disposition:s:0", "default", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def real_mpv():
    """Un seul vrai moteur pour ces tests : en créer et en fermer plusieurs à la suite fait parfois planter
    python-mpv à la fermeture (son fil d'évènements), sans rapport avec ce qui est testé."""
    if P.mpv_dir() is None:
        pytest.skip("libmpv absent")
    m = P._mpv().MPV(vo="null", ao="null", config="no", load_scripts="no", alang="fr,fre,fra,en,eng",
                     slang="fr,fre,fra", subs_with_matching_audio="forced")
    yield m
    _stop(m)  # arrêter la lecture avant de fermer le moteur
    m.terminate()


def _stop(m):
    """Attendre que le film précédent soit fermé (loadfile est asynchrone : sinon on lirait ses pistes à lui)."""
    m.command("stop")
    for _ in range(100):
        if m.idle_active:
            return
        time.sleep(0.02)


def _playing(m):
    for _ in range(100):
        if (m.time_pos or 0) > 0.3:
            return True
        time.sleep(0.05)
    return False


def test_no_full_french_subtitles_over_french_audio(default_subs_mp4, real_mpv):
    m = real_mpv
    _stop(m)
    m.aid = m.sid = "auto"
    m.play(str(default_subs_mp4))
    assert _playing(m)
    sel = {tr["type"] for tr in m.track_list if tr.get("selected")}
    assert "audio" in sel and "sub" not in sel


@pytest.mark.parametrize("langs", [None, {"aid": "eng"}, {"sid": "no"}, {"aid": "fre", "sid": "fre"}])
def test_remembered_languages_accepted_by_real_mpv(default_subs_mp4, real_mpv, monkeypatch, langs):
    """Régression : « sous-titres coupés » donnait slang="" et une langue retenue « eng,fr,… » était coupée aux
    virgules : loadfile refusé -> le film suivant ne démarrait pas."""
    m = real_mpv
    p = P.Player("t", lambda: {})
    p.mpv = m
    monkeypatch.setattr(p, "_ensure", lambda: m)
    monkeypatch.setattr(p, "show_osd", lambda *a: None)
    _stop(m)
    p.play(str(default_subs_mp4), {"kind": "movie", "id": "1"}, 0, "", langs)
    assert _playing(m)
    subs = {tr["type"] for tr in m.track_list if tr.get("selected")}
    assert ("sub" in subs) == bool(langs and langs.get("sid") == "fre")
    # une piste choisie à la main ne passe pas au film suivant (régression : aid=2 restait -> mauvaise langue, sans son)
    m.aid = 2
    m.sid = "no"
    _stop(m)
    p.play(str(default_subs_mp4), {"kind": "movie", "id": "2"}, 0, "", None)
    assert _playing(m)
    time.sleep(0.2)
    assert [tr.get("lang") for tr in m.track_list if tr.get("selected") and tr["type"] == "audio"] == ["fre"]
