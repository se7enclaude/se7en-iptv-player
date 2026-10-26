"""Le vrai libmpv (pas une imitation) : la position avance, les langues audio et les sous-titres du fichier sont listés
et se changent. (Régression : m["time-pos"] lit un réglage, pas l'état de lecture → minuteur à 0:00, « aucun
sous-titre ».)"""
import subprocess
import time

import pytest

from medialab.stream import player as P


@pytest.fixture
def multi_mkv(tmp_path, ffmpeg_bin):
    for lang, word in (("fr", "Sous-titre"), ("en", "Subtitle")):
        (tmp_path / f"{lang}.srt").write_text("".join(f"{i + 1}\n00:00:{i:02d},000 --> 00:00:{i:02d},800\n{word} {i}\n\n"
                                                      for i in range(20)), encoding="utf-8")
    out = tmp_path / "multi.mkv"
    subprocess.run([ffmpeg_bin, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-f", "lavfi", "-i", "sine=frequency=660",
                    "-i", str(tmp_path / "fr.srt"), "-i", str(tmp_path / "en.srt"), "-t", "20",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3", "-map", "4",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "srt",
                    "-metadata:s:a:0", "language=fre", "-metadata:s:a:1", "language=eng",
                    "-metadata:s:s:0", "language=fre", "-metadata:s:s:1", "language=eng", str(out)], check=True)
    return out


def test_position_tracks_and_switching_with_real_mpv(multi_mkv):
    if P.mpv_dir() is None:
        pytest.skip("libmpv absent")
    mpv = P._mpv()
    p = P.Player("t", lambda: {})
    p.mpv = mpv.MPV(vo="null", ao="null", config="no", load_scripts="no")
    p.item = {"kind": "movie", "id": "1", "title": "Film"}
    try:
        p.mpv.play(str(multi_mkv))
        for _ in range(100):
            if (p.mpv.time_pos or 0) > 1.5:
                break
            time.sleep(0.05)
        st = p.snapshot()
        assert st["position"] > 1 and 19 < st["duration"] < 21
        audio = [t for t in st["tracks"] if t["type"] == "audio"]
        subs = [t for t in st["tracks"] if t["type"] == "sub"]
        assert [t["lang"] for t in audio] == ["fre", "eng"] and [t["lang"] for t in subs] == ["fre", "eng"]
        p.set("aid", audio[1]["id"])
        p.set("sid", subs[1]["id"])
        time.sleep(0.3)
        st = p.snapshot()
        assert [t["selected"] for t in st["tracks"] if t["type"] == "audio"] == [False, True]
        assert [t["selected"] for t in st["tracks"] if t["type"] == "sub"] == [False, True]
        p.seek(10, absolute=True)
        time.sleep(0.5)
        assert p.snapshot(light=True)["position"] >= 9.5
    finally:
        p.mpv.terminate()
        p.mpv = None
