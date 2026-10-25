"""Bundle every component into dist/se7en-iptv-player/bin so se7en IPTV Player works right after install, offline.

    backend\.venv\Scripts\python packaging\fetch_components.py

  bin/ffmpeg      FFmpeg (enregistrements en copie de flux)
  bin/mpv         libmpv-2.dll (moteur de lecture, build GPL de shinchiro)

FFmpeg comes from the official GitHub release and is checked against its SHA-256; libmpv is pinned below.
Both are cached in build/component-cache so later builds do not download them again.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
DIST = ROOT / "dist" / "se7en-iptv-player"
BIN = DIST / "bin"
CACHE = ROOT / "build" / "component-cache"
COMPONENTS = ("ffmpeg",)

MPV_URL = ("https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261021/"
           "mpv-dev-x86_64-20261021-git-e471496c56.7z")
MPV_DLL_SHA256 = "04e4d27dc385dc18ddd93fbe69c75e635b864f3c6ec53a9122df54c2f7842f4a"
MPV_NOTE = """libmpv-2.dll - mpv (https://mpv.io), build de https://github.com/shinchiro/mpv-winbuild-cmake (20261021).
Licence : GPL-3.0-or-later. Code source : https://github.com/mpv-player/mpv
"""


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_mpv() -> Path:
    for cand in (CACHE / "mpv" / "libmpv-2.dll", ROOT / "build" / "mpv" / "libmpv-2.dll"):
        if cand.is_file() and sha256(cand) == MPV_DLL_SHA256:
            return cand
    from medialab import components
    print("Downloading libmpv...", flush=True)
    tmp = Path(tempfile.mkdtemp())
    archive = tmp / "mpv-dev.7z"
    components._download(MPV_URL, archive, "mpv")
    # tar.exe de Windows lit ce 7z (py7zr ne gère pas BCJ2)
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    subprocess.run([str(tar), "-xf", str(archive), "-C", str(tmp), "libmpv-2.dll"], check=True)
    dll = tmp / "libmpv-2.dll"
    if sha256(dll) != MPV_DLL_SHA256:
        raise SystemExit("libmpv checksum mismatch")
    (CACHE / "mpv").mkdir(parents=True, exist_ok=True)
    shutil.copy2(dll, CACHE / "mpv" / "libmpv-2.dll")
    return CACHE / "mpv" / "libmpv-2.dll"


def main() -> int:
    if not DIST.exists():
        print("Run PyInstaller first (dist/se7en-iptv-player missing).")
        return 1
    CACHE.mkdir(parents=True, exist_ok=True)
    os.environ["SE7ENIPTVPLAYER_DATA_DIR"] = str(CACHE / "data")
    from medialab import components

    for cid in COMPONENTS:
        src = CACHE / "data" / "components" / cid
        if not (src.exists() and any(src.rglob("*.exe"))):
            print(f"Downloading {cid}...", flush=True)
            components.install(cid)
        else:
            print(f"{cid}: cached", flush=True)
        dst = BIN / cid
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        print(f"  -> {dst}  ({sum(f.stat().st_size for f in dst.rglob('*') if f.is_file()) / 1e6:.0f} MB)", flush=True)

    dst = BIN / "mpv"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fetch_mpv(), dst / "libmpv-2.dll")
    (dst / "LICENSE-mpv.txt").write_text(MPV_NOTE, encoding="utf-8")
    print(f"libmpv -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
