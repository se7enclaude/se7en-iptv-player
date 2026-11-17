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


def _sound(rng, sec):
    """Empreinte (n, D) d'un son qui varie : chaque mesure bouge à son rythme, en niveaux 0–255."""
    n = int(sec * A.RATE)
    cols = [np.convolve(rng.normal(128, 40, n), np.ones(5) / 5, "same") for _ in range(A.D)]
    return np.clip(np.stack(cols, 1), 0, 255).astype(np.float32)


def _jitter(rng, x, amount=4.0):
    """Le même son mesuré une autre fois : un peu de bruit, un volume un peu différent."""
    y = x + rng.normal(0, amount, x.shape)
    y[:, 0] += 6
    return np.clip(y, 0, 255).astype(np.float32)


@pytest.mark.parametrize("seed", range(4))
def test_common_intro_found_between_two_episodes(seed):
    rng = np.random.default_rng(seed)
    intro = _sound(rng, 75)
    a = np.concatenate([_sound(rng, 40), intro, _sound(rng, 300)])
    b = np.concatenate([_sound(rng, 260), _jitter(rng, intro), _sound(rng, 250)])  # long avant-générique
    start_a, start_b, dur = A.find_common(a, b)
    assert abs(start_a - 40) < 1.5 and abs(start_b - 260) < 1.5 and abs(dur - 75) < 2.5
    assert A.find_common(_sound(rng, 400), _sound(rng, 400)) is None  # deux épisodes sans générique commun


def test_intro_start_recognised_a_few_seconds_after_it_starts():
    rng = np.random.default_rng(7)
    intro = _sound(rng, 60)
    live = np.concatenate([_sound(rng, 30), _jitter(rng, intro[:150])])
    assert A.locate_start(live, intro) == pytest.approx(30, abs=0.2)
    assert A.locate_start(_sound(rng, 40), intro) is None


def test_features_from_mpv_metadata_and_packing():
    md = {"lavfi.r128.M": "-20", "lavfi.aspectralstats.1.centroid": "1000", "lavfi.aspectralstats.1.spread": "800",
          "lavfi.aspectralstats.1.rolloff": "4000", "lavfi.aspectralstats.1.flatness": "0.01",
          "lavfi.aspectralstats.1.entropy": "0.5"}
    f = A.feature(md)
    assert f[0] == -20 and f[1] == pytest.approx(np.log2(1000)) and f[4] == pytest.approx(-2)
    assert A.feature({"lavfi.r128.M": "-20"}) is None  # filtre de couleur absent : on ne mesure rien
    x = A.features({0: f, 3: f, 5: [-30] + f[1:]})
    assert x.shape == (6, A.D) and x[5, 0] < x[2, 0]  # trous comblés, fin à la dernière mesure
    assert (A.unpack(A.pack(x)) == np.round(x)).all()
    env = np.array([-60, -30, 0], dtype=np.float32)
    assert (A.unpack(A.pack(env)) == env).all()  # l'enveloppe (volume seul) se range toujours pareil


def test_memory_learns_intro_and_end_credits(tmp_path):
    rng = np.random.default_rng(3)
    intro, outro = _sound(rng, 50), _sound(rng, 80)
    mem = A.IntroMemory(Store(tmp_path / "s.db"))
    assert mem.learn("acc", "s1", "e1", "head", np.concatenate([_sound(rng, 30), intro, _sound(rng, 400)])) is None
    tpl, dur, at = mem.learn("acc", "s1", "e2", "head", np.concatenate([_sound(rng, 200), _jitter(rng, intro), _sound(rng, 300)]))
    assert abs(dur - 50) < 2 and abs(at - 200) < 1 and mem.template("acc", "s1")[1] == dur
    assert mem.template("acc", "s2") is None and mem.template("acc", "s1", "tail") is None
    # fins : 7 dernières minutes de deux épisodes de 45 et 52 min, générique 90 s puis 70 s avant la fin
    mem.learn("acc", "s1", "e1", "tail", np.concatenate([_sound(rng, A.TAIL - 90), outro, _sound(rng, 10)]), 45 * 60 - A.TAIL, 45 * 60)
    got = mem.learn("acc", "s1", "e2", "tail", np.concatenate([_sound(rng, A.TAIL - 70), _jitter(rng, outro[:600])]),
                    52 * 60 - A.TAIL, 52 * 60)  # quitté 10 s avant la fin : la fin manque
    assert got and abs(got[2] - 70) < 1.5 and abs(got[1] - 60) < 4
    # un troisième épisode confirme le générique : il ne peut plus être remplacé par un passage vu une seule fois
    mem.learn("acc", "s1", "e3", "head", np.concatenate([_sound(rng, 5), _jitter(rng, intro), _sound(rng, 400)]))
    assert mem.store.one("SELECT support FROM intro_tpl WHERE part='head'")["support"] == 2
    other = _sound(rng, 40)
    mem.learn("acc", "s1", "e4", "head", np.concatenate([_sound(rng, 10), other, _sound(rng, 400)]))
    assert mem.template("acc", "s1")[1] == pytest.approx(dur, abs=3)


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
SR = 48000


def _music(seed, sec):
    """Un morceau : batterie, basse et accords qui suivent une grille, mélodie qui change à chaque temps."""
    rng = np.random.default_rng(seed)
    n, beat = int(sec * SR), int(SR * 60 / rng.uniform(90, 130))
    t = np.arange(4 * beat) / SR
    root, grid = rng.uniform(100, 160), rng.choice([0, 3, 5, 7, 8, 10], 8)
    out = np.zeros(n + 4 * beat)

    def tone(f, k=4):
        return sum(0.6 ** h * np.sin(2 * np.pi * f * (h + 1) * t + rng.uniform(0, 6)) for h in range(k))

    for k, b in enumerate(range(0, n, beat)):
        tb = t[:beat]
        env = np.exp(-tb / 0.06)
        out[b:b + beat] += 0.8 * np.sin(2 * np.pi * 55 * tb * (1 + env)) * env
        if k % 2:
            out[b:b + beat] += 0.3 * rng.normal(0, 1, beat) * np.exp(-tb / 0.08)
        if k % 4 == 0:
            c = grid[(k // 4) % 8]
            out[b:b + 4 * beat] += sum(0.08 * tone(root * 2 ** ((c + i) / 12)) for i in (0, 4, 7)) * np.linspace(1, 0.5, 4 * beat)
            out[b:b + 4 * beat] += 0.2 * tone(root / 2 * 2 ** (c / 12), 3)
        f = root * 2 * 2 ** (rng.choice([0, 2, 3, 5, 7, 10, 12]) / 12)
        out[b:b + beat] += 0.12 * tone(f, 6)[:beat] * np.exp(-tb / 0.25)
    return out[:n] / np.abs(out).max() * 0.5


def _scene(seed, sec):
    """Une scène : des répliques (voix, syllabes) entre des silences, et quelques bruits."""
    rng = np.random.default_rng(seed)
    n, out, i = int(sec * SR), np.zeros(int(sec * SR)), 0
    while i < n:
        L = min(int(rng.uniform(0.8, 3.5) * SR), n - i)
        t = np.arange(L) / SR
        f0 = rng.uniform(90, 230)
        v = sum(0.7 ** h * np.sin(2 * np.pi * f0 * (h + 1) * t) for h in range(8))
        out[i:i + L] = 0.25 * v * (0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(3, 6) * t) ** 2) * rng.uniform(0.4, 1)
        i += L + int(rng.uniform(0.2, 1.5) * SR)
    return out + 0.01 * rng.normal(0, 1, n)


def _episode(ffmpeg, path, intro_at, seed, length=60):
    """Un épisode : une scène, puis le même générique de 25 s à ``intro_at``, puis une autre scène."""
    import wave
    x = np.concatenate([_scene(seed * 10, intro_at), _music(1234, 25), _scene(seed * 10 + 1, length - 25 - intro_at)])
    pcm = (np.stack([x, x], 1).clip(-1, 1) * 32000).astype(np.int16)
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), "-f", "lavfi", "-i",
                    f"color=c=black:s=64x36:r=10:d={length}", "-map", "1:v", "-map", "0:a", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


@pytest.mark.skipif(P.mpv_dir() is None, reason="libmpv absent")
def test_real_mpv_learns_the_intro_then_offers_to_skip_it(tmp_path, ffmpeg_bin, monkeypatch):
    monkeypatch.setattr(A, "HEAD", 40)
    for k, at in enumerate((5, 12, 9)):
        _episode(ffmpeg_bin, tmp_path / f"e{k + 1}.mkv", at, seed=k + 1, length=45)
    mpv = P._mpv()
    p = P.Player("t", lambda: {})
    p.settings = lambda: {"auto_sync": False}
    p.intro_mem = A.IntroMemory(Store(tmp_path / "s.db"))
    p.account_id = lambda: "acc"
    p.mpv = mpv.MPV(vo="null", ao="null", config="no", af=P.AUDIO_PROBE)  # vitesse normale : mesures prises comme en vrai
    try:
        for k in (1, 2):  # deux épisodes vus depuis le début : le générique est appris
            p.item = {"kind": "episode", "id": f"e{k}", "series": "s1", "title": f"E{k}"}
            p.mpv.play(str(tmp_path / f"e{k}.mkv"))
            deadline = time.time() + 50
            while time.time() < deadline and not (p._aud and p._aud.get("id") == f"e{k}" and p._aud.get("learned")):
                time.sleep(0.2)
            time.sleep(1.0)
        found = p.intro_mem.template("acc", "s1")
        assert found, "générique non appris"
        assert found[1] == pytest.approx(25, abs=3.5) and found[2] == pytest.approx(12, abs=3.5), found[1:]
        # troisième épisode : le générique (à 9 s) est reconnu pendant qu'il passe
        p.item = {"kind": "episode", "id": "e3", "series": "s1", "title": "E3"}
        p.mpv.play(str(tmp_path / "e3.mkv"))
        deadline = time.time() + 25
        while time.time() < deadline and not (p._aud and p._aud.get("id") == "e3" and p._aud.get("intro")):
            time.sleep(0.1)
        assert p._aud.get("intro") == pytest.approx(34, abs=2.5), (found[1:], p._aud.get("intro"))  # fin : 9 + 25 s
        assert p._intro_visible({"position": float(p.mpv.time_pos or 0)})
        assert p.skip_intro()
        time.sleep(0.6)
        assert float(p.mpv.time_pos or 0) == pytest.approx(p._aud["intro"], abs=1.5)
        assert not p._intro_visible({"position": float(p.mpv.time_pos or 0)})
    finally:
        p.mpv.terminate()
        p.mpv = None


def test_end_credits_start_heard_then_usual_place_then_guess():
    p = P.Player("t", lambda: {})
    p.mpv = type("M", (), {"chapter_list": []})()
    p.item = {"kind": "episode", "id": "e1"}
    assert p._credits_start(2700) == 2700 - 81  # rien de connu : 3 % de la durée
    p._aud = {"id": "e1", "outro": None, "otpl": (None, 60.0, 95.0)}
    assert p._credits_start(2700) == 2700 - 95  # place habituelle dans la série
    p._aud["otpl"] = (None, 60.0, 900.0)
    assert p._credits_start(2700) == 2700 - 81  # place invraisemblable : ignorée
    p._aud["outro"] = 2580.0
    assert p._credits_start(2700) == 2580.0  # reconnu au son dans cet épisode
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
