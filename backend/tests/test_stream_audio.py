"""Le son : générique d'ouverture trouvé en comparant deux épisodes puis reconnu au suivant (« Passer l'intro »), et
sous-titres recalés sur les paroles. Calculs sur des sons simulés, puis avec le vrai libmpv et de vrais fichiers."""
import subprocess
import time

import numpy as np
import pytest

from medialab.stream import audio as A
from medialab.stream import player as P
from medialab.stream.store import Store


def _noise(rng, sec):
    x = rng.normal(-30, 6, int(sec * A.RATE))
    return np.convolve(x, np.ones(4) / 4, "same").astype(np.float32)


@pytest.mark.parametrize("seed", range(4))
def test_common_intro_found_between_two_episodes(seed):
    rng = np.random.default_rng(seed)
    intro = _noise(rng, 75) + 8
    a = np.concatenate([_noise(rng, 40), intro, _noise(rng, 300)])
    b = np.concatenate([_noise(rng, 95), intro + 3 + rng.normal(0, 1.5, len(intro)), _noise(rng, 250)])  # un peu plus fort
    start_a, start_b, dur = A.find_common(a, b)
    assert abs(start_a - 40) < 1 and abs(start_b - 95) < 1 and abs(dur - 75) < 1.5
    assert A.find_common(_noise(rng, 360), _noise(rng, 360)) is None  # deux épisodes sans générique commun


def test_intro_recognised_a_few_seconds_after_it_starts():
    rng = np.random.default_rng(7)
    intro = _noise(rng, 60) + 8
    assert A.locate(np.concatenate([_noise(rng, 30), intro[:120]]), intro) == pytest.approx(12, abs=0.2)
    assert A.locate(_noise(rng, 30), intro) is None


def test_memory_learns_from_the_second_episode(tmp_path):
    rng = np.random.default_rng(3)
    intro = _noise(rng, 50) + 8
    mem = A.IntroMemory(Store(tmp_path / "s.db"))
    assert mem.learn("acc", "s1", "e1", np.concatenate([_noise(rng, 30), intro, _noise(rng, 280)])) is None
    tpl, dur = mem.learn("acc", "s1", "e2", np.concatenate([_noise(rng, 70), intro, _noise(rng, 240)]))
    assert abs(dur - 50) < 1.5 and mem.template("acc", "s1")[1] == dur and mem.template("acc", "s2") is None


def _speech(rng, cues, n):
    env = np.full(n, -45, np.float32)
    for s, e in cues:
        env[int(s * A.RATE):int(e * A.RATE)] = rng.normal(-22, 3, int(e * A.RATE) - int(s * A.RATE))
    return env + rng.normal(0, 4, n).astype(np.float32)


def test_subtitles_offset_and_frame_rate_found():
    rng = np.random.default_rng(1)
    cues, t = [], 5.0
    while t < 600:
        d = rng.uniform(1, 4)
        cues.append((t, t + d))
        t += d + rng.uniform(0.5, 6)
    env = _speech(rng, cues, 6000)
    known = np.ones(6000, bool)
    known[:300] = False
    assert A.best_offset(env, known, [(s - 2.3, e - 2.3) for s, e in cues])["delay"] == 2.3
    assert A.best_offset(env, known, cues)["delay"] == 0.0
    r = 25 / 23.976
    res = A.best_offset(env, known, [(s / r + 1, e / r + 1) for s, e in cues])
    assert res["speed"] == pytest.approx(r, abs=1e-4) and res["delay"] == pytest.approx(-r, abs=0.15)
    assert A.best_offset(rng.normal(-30, 5, 6000).astype(np.float32), known, cues) is None  # rien de net : on ne touche à rien
    assert A.best_offset(env, np.zeros(6000, bool), cues) is None  # pas assez de son mesuré


def test_cues_from_srt_and_ass(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,500 --> 00:00:03,000\nBonjour\n\n2\n01:00:00,000 --> 01:00:02,250\nFin\n", encoding="utf-8")
    assert A.cues(srt) == [(1.5, 3.0), (3600.0, 3602.25)]
    ass = tmp_path / "a.ass"
    ass.write_text("[Events]\nDialogue: 0,0:00:05.10,0:00:06.00,Default,,0,0,0,,Salut\n", encoding="utf-8")
    assert A.cues(ass) == [(5.1, 6.0)]


def test_skip_intro_button_drawn_and_clickable():
    data, hits = P.osd({"kind": "episode", "title": "x", "controls": False, "skip_intro": True})
    assert "Passer l'intro" in data and any(h["id"] == "skip_intro" for h in hits)


# ------------------------------------------------------------------ vrai libmpv
def _episode(ffmpeg, path, intro_at, seed, length=60):
    """Un épisode : bruit qui varie, puis la même « musique » de 25 s à ``intro_at``, puis du bruit."""
    music = ("aevalsrc='0.6*sin(2*PI*(220+110*floor(mod(t*2,4)))*t)*(0.4+0.6*abs(sin(PI*t*0.7)))':s=48000:d=25")
    noise = f"anoisesrc=d={length}:c=pink:seed={seed}:a=0.4,volume='0.2+0.8*abs(sin(t*{0.3 + seed * 0.07}))':eval=frame"
    graph = (f"[0:a]atrim=0:{intro_at},asetpts=N/SR/TB[n1];[1:a]asetpts=N/SR/TB[m];"
             f"[0:a]atrim={intro_at}:{length - 25},asetpts=N/SR/TB[n2];[n1][m][n2]concat=n=3:v=0:a=1[a]")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", noise, "-f", "lavfi", "-i", music,
                    "-f", "lavfi", "-i", f"color=c=black:s=64x36:r=10:d={length}", "-filter_complex", graph,
                    "-map", "2:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
                   check=True)


@pytest.mark.skipif(P.mpv_dir() is None, reason="libmpv absent")
def test_real_mpv_learns_the_intro_then_offers_to_skip_it(tmp_path, ffmpeg_bin, monkeypatch):
    monkeypatch.setattr(A, "HEAD", 55)
    for k, at in enumerate((8, 20, 14)):
        _episode(ffmpeg_bin, tmp_path / f"e{k + 1}.mkv", at, seed=k + 1)
    mpv = P._mpv()
    p = P.Player("t", lambda: {})
    p.settings = lambda: {"auto_sync": False}
    p.intro_mem = A.IntroMemory(Store(tmp_path / "s.db"))
    p.account_id = lambda: "acc"
    p.mpv = mpv.MPV(vo="null", ao="null", config="no", af=P.AUDIO_PROBE, speed="2")
    try:
        for k in (1, 2):  # deux épisodes vus depuis le début : le générique est appris
            p.item = {"kind": "episode", "id": f"e{k}", "series": "s1", "title": f"E{k}"}
            p.mpv.play(str(tmp_path / f"e{k}.mkv"))
            deadline = time.time() + 30
            while time.time() < deadline and not (p._aud and p._aud.get("id") == f"e{k}" and p._aud.get("learned")):
                time.sleep(0.2)
            time.sleep(1.0)
        found = p.intro_mem.template("acc", "s1")
        assert found, "générique non appris"
        assert 22 <= found[1] <= 28
        # troisième épisode : le générique (à 14 s) est reconnu pendant qu'il passe
        p.item = {"kind": "episode", "id": "e3", "series": "s1", "title": "E3"}
        p.mpv.play(str(tmp_path / "e3.mkv"))
        deadline = time.time() + 20
        while time.time() < deadline and not (p._aud and p._aud.get("id") == "e3" and p._aud.get("intro")):
            time.sleep(0.1)
        assert p._aud.get("intro") == pytest.approx(39, abs=2.5)  # fin du générique : 14 + 25 s
        p.mpv.speed = 1
        assert p._intro_visible({"position": float(p.mpv.time_pos or 0)})
        assert p.skip_intro()
        time.sleep(0.6)
        assert float(p.mpv.time_pos or 0) == pytest.approx(p._aud["intro"], abs=1.5)
        assert not p._intro_visible({"position": float(p.mpv.time_pos or 0)})
    finally:
        p.mpv.terminate()
        p.mpv = None


def test_player_resyncs_external_subtitles_once_enough_is_heard(tmp_path):
    rng = np.random.default_rng(5)
    cues, t = [], 3.0
    while t < 500:
        d = rng.uniform(1, 4)
        cues.append((t, t + d))
        t += d + rng.uniform(0.5, 6)
    env = _speech(rng, cues, 5000)
    srt = tmp_path / "os-1.srt"  # répliques 2,3 s trop tôt
    srt.write_text("".join(f"{i + 1}\n{_tc(s - 2.3)} --> {_tc(e - 2.3)}\nRéplique\n\n" for i, (s, e) in enumerate(cues)), encoding="utf-8")

    class M(dict):
        __getattr__ = dict.get

        def __setattr__(self, k, v):
            self[k] = v

    p = P.Player("t", lambda: {})
    p.mpv = M(sub_delay=0.0, sub_speed=1.0, track_list=[{"id": 3, "type": "sub", "selected": True, "external": True,
                                                         "external-filename": str(srt)}])
    flashes = []
    p.flash = lambda text, seconds=1.6: flashes.append(text)
    a = {"id": "f", "samples": {i: float(v) for i, v in enumerate(env)}, "synced": {}, "manual": False}
    p._resync(a)
    assert p.mpv["sub_delay"] == 2.3 and flashes == ["Sous-titres recalés automatiquement (+2,3 s)"]
    p.mpv["sub_delay"] = 0.0
    p._resync(a)  # déjà calculé avec ce son : on ne recommence pas tout de suite
    assert p.mpv["sub_delay"] == 0.0
    p.mpv = None


def _tc(t):
    t = max(0.0, t)
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d},{int(round(t % 1 * 1000)) % 1000:03d}"
