"""Enregistreur : la vidéo d'attente du fournisseur n'est pas gardée 125 fois, et les morceaux finissent en un fichier."""
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from medialab.components import find_binary
from medialab.stream import recorder as R
from medialab.stream.store import Store


def _ffmpeg():
    try:
        return find_binary("ffmpeg")
    except Exception:  # noqa: BLE001
        return None


def _clip(path: Path, seconds: float, freq: int) -> None:
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=160x90:rate=25:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}", "-c:v", "libx264", "-c:a", "aac", "-f", "mpegts",
                    str(path)], check=True)


def _duration(path: Path) -> float:
    probe = str(Path(_ffmpeg()).with_name("ffprobe.exe"))
    out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


class _P:
    """Un FFmpeg fini : son fichier et l'heure de son lancement."""
    def __init__(self, out: Path, started: float):
        self.out, self.started, self.returncode = out, started, 0

    def poll(self):
        return 0


@pytest.fixture
def rec(tmp_path):
    store = Store(tmp_path / "s.db")
    r = R.Recorder(store, R.Connections(), lambda rec: ("http://x/live.ts", "", None), lambda: tmp_path)
    r._stop.set()  # pas de boucle en arrière-plan pendant le test
    main = tmp_path / "Match - Chaîne - 2026-10-26 00h02.ts"
    now = time.time()
    store.run("INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("r1", "a", "c", "Chaîne", "Match", now - 60, now + 3600, "live", "recording", str(main), 0, None, now))
    return r, main


def test_same_short_video_is_kept_once_retries_slow_down_then_gives_up(rec, monkeypatch):
    r, main = rec
    monkeypatch.setattr(R, "join_parts", lambda main, junk=frozenset(): None)
    main.write_bytes(b"clip d'attente" * 1000)
    st = {"seen": set(), "fails": 0, "next": 0.0, "checked": None}
    now = time.time()
    r.procs["r1"] = _P(main, now - 3)
    r._judge_part(r.get("r1"), r.procs["r1"], st, now)
    assert main.exists() and st["fails"] == 1 and st["next"] == now + R.RETRY_DELAYS[0]
    delays = []
    for k in range(2, R.MAX_SAME + 1):
        part = main.with_name(f"{main.stem} partie {k}.ts")
        shutil.copy(main, part)  # le fournisseur renvoie exactement la même vidéo
        r.procs["r1"] = _P(part, now - 3)
        r._judge_part(r.get("r1"), r.procs["r1"], st, now)
        assert not part.exists()  # copie effacée tout de suite
        delays.append(st["next"] - now)
    assert delays[:3] == [20, 40, 60] and max(delays) == 60
    got = r.get("r1")
    assert got["status"] == "failed" and "même courte vidéo" in got["error"]


def test_a_real_part_resets_the_retry_delay(rec):
    r, main = rec
    st = {"seen": set(), "fails": 3, "next": time.time() + 60, "checked": None}
    part = main.with_name(f"{main.stem} partie 2.ts")
    part.write_bytes(b"vrai direct" * 5000)
    now = time.time()
    r._judge_part(r.get("r1"), _P(part, now - 600), st, now)  # a tourné 10 minutes : c'était le direct
    assert part.exists() and st["fails"] == 0 and st["next"] == 0.0


@pytest.mark.skipif(_ffmpeg() is None, reason="FFmpeg absent")
def test_parts_are_joined_into_one_file_without_duplicates_or_waiting_video(tmp_path):
    main = tmp_path / "Match.ts"
    _clip(main, 3, 300)                                   # vidéo d'attente du fournisseur
    shutil.copy(main, tmp_path / "Match partie 2.ts")     # la même, encore
    _clip(tmp_path / "Match partie 3.ts", 2, 500)         # le direct
    _clip(tmp_path / "Match partie 4.ts", 2, 700)         # la suite après une coupure
    junk = {R.file_digest(main)}
    R.join_parts(main, junk)
    assert [p.name for p in tmp_path.iterdir()] == ["Match.ts"]
    assert 3.5 < _duration(main) < 4.6  # 2 + 2 s, sans les 3 s d'attente


@pytest.mark.skipif(_ffmpeg() is None, reason="FFmpeg absent")
def test_only_the_waiting_video_is_kept_if_that_is_all_there_is(tmp_path):
    main = tmp_path / "Match.ts"
    _clip(main, 2, 300)
    shutil.copy(main, tmp_path / "Match partie 2.ts")
    R.join_parts(main, {R.file_digest(main)})
    assert [p.name for p in tmp_path.iterdir()] == ["Match.ts"] and main.stat().st_size > 0
