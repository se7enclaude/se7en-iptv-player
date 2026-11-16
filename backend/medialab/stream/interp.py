"""Vraie interpolation d'images : de nouvelles images sont calculées entre celles de la vidéo (mouvement estimé), comme
le mode « 100 Hz » des téléviseurs. 25 i/s → 50, 50 i/s → 100 (jamais plus que la fréquence de l'écran).

Elle s'appuie sur deux modules libres, téléchargés la première fois qu'on active l'option (≈ 25 Mo) depuis PyPI, le
dépôt officiel des modules Python, et vérifiés par leur empreinte SHA-256 publiée :
- VapourSynth (traitement vidéo image par image, chargé par le lecteur mpv) ;
- MVTools (estimation du mouvement et création des images intermédiaires).
Elle demande du processeur : réservée aux vidéos jusqu'à la HD (1080p) ; la 4K passe telle quelle.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path

from ..config import paths
from ..logging_setup import get_logger

log = get_logger("stream.interp")

PACKAGES = (("vapoursynth", "82"), ("vapoursynth-mvtools", "29"))
_lock = threading.Lock()
_state: dict = {"installing": False, "error": None, "progress": 0.0}
_ready = False

SCRIPT = r'''# se7en IPTV Player : images intermédiaires calculées par MVTools
import vapoursynth as vs
core = vs.core
clip = video_in
src = container_fps if container_fps and container_fps > 1 else 25.0
screen = display_fps if display_fps and display_fps > 1 else 60.0
factor = 2
if clip.height > 1200 or src > 61 or src * factor > screen * 1.05:
    clip.set_output()      # 4K, déjà fluide, ou écran trop lent : rien à faire
else:
    clip = core.std.AssumeFPS(clip, fpsnum=int(round(src * 1000)), fpsden=1000)
    sup = core.mv.Super(clip, pel=2, hpad=16, vpad=16)
    bv = core.mv.Analyse(sup, blksize=16, isb=True, chroma=True, search=3, searchparam=1)
    fv = core.mv.Analyse(sup, blksize=16, isb=False, chroma=True, search=3, searchparam=1)
    # mode 3 : images mélangées avec les vecteurs de mouvement ; thscd : changement de plan → pas d'image inventée
    clip = core.mv.BlockFPS(clip, sup, bv, fv, num=int(round(src * factor * 1000)), den=1000, mode=3, thscd2=12)
    clip.set_output()
'''


def home() -> Path:
    return paths().data / "components" / "interpolation"


def installed() -> bool:
    h = home()
    return (h / "vapoursynth" / "vsscript.dll").is_file() and (h / "vapoursynth" / "plugins" / "mvtools.dll").is_file()


def status() -> dict:
    return {"installed": installed(), **_state}


def _pypi_file(name: str, version: str) -> dict:
    """Le fichier Windows 64 bits d'un module sur PyPI, avec son empreinte."""
    import httpx
    r = httpx.get(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30, follow_redirects=True)
    r.raise_for_status()
    files = [f for f in r.json()["urls"] if f["filename"].endswith("win_amd64.whl")]
    if not files:
        raise RuntimeError(f"{name} {version} : pas de version Windows")
    return files[0]


def install(fetch=None) -> None:
    """Télécharge et installe les deux modules (une fois). Empreintes vérifiées ; fichiers de débogage ignorés."""
    global _ready
    with _lock:
        if _state["installing"]:
            return
        _state.update(installing=True, error=None, progress=0.0)
    tmp = home().with_name("interpolation.tmp")
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for k, (name, version) in enumerate(PACKAGES):
            f = _pypi_file(name, version) if fetch is None else fetch(name, version)
            data = _download(f["url"]) if fetch is None else f["data"]
            if hashlib.sha256(data).hexdigest() != f["digests"]["sha256"]:
                raise RuntimeError(f"{name} : empreinte incorrecte, fichier refusé")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for member in z.infolist():
                    n = member.filename
                    if n.endswith((".pdb", ".h", ".pc")) or ".dist-info/" in n or "/include/" in n or n.endswith("vspipe.exe"):
                        continue
                    target = (tmp / n).resolve()
                    if not str(target).startswith(str(tmp.resolve())):
                        raise RuntimeError("archive invalide")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            _state["progress"] = (k + 1) / len(PACKAGES)
        (tmp / "interp.vpy").write_text(SCRIPT, encoding="utf-8")
        shutil.rmtree(home(), ignore_errors=True)
        tmp.replace(home())
        _ready = False
        log.info("Interpolation modules installed in %s", home())
    except Exception as e:  # noqa: BLE001
        log.warning("Interpolation install failed: %s", e)
        _state["error"] = str(e) or "Téléchargement impossible."
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        _state["installing"] = False


def _download(url: str) -> bytes:
    import httpx
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        return r.read()


def prepare() -> str | None:
    """Rend les modules utilisables par le lecteur (une fois par lancement) ; renvoie le filtre vidéo de mpv."""
    global _ready
    if not installed():
        return None
    h = home()
    vs_dir = h / "vapoursynth"
    if not _ready:
        try:
            if str(h) not in sys.path:
                sys.path.insert(0, str(h))
            os.environ["PATH"] = str(vs_dir) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(vs_dir))
            # VapourSynth doit savoir quel Python l'héberge : celui de l'appli (fichier de réglages de VapourSynth)
            from vapoursynth import _utils as vsu  # type: ignore[import-not-found]
            vsu.vapoursynth_config()
            if not (h / "interp.vpy").is_file() or (h / "interp.vpy").read_text(encoding="utf-8") != SCRIPT:
                (h / "interp.vpy").write_text(SCRIPT, encoding="utf-8")
            _ready = True
        except Exception as e:  # noqa: BLE001
            log.warning("Interpolation unavailable: %s", e)
            return None
    vpy = str(h / "interp.vpy")
    return f"vapoursynth=file=%{len(vpy)}%{vpy}:buffered-frames=4:concurrent-frames=auto"


def install_later() -> None:
    threading.Thread(target=install, name="interp-install", daemon=True).start()


def to_json() -> str:
    return json.dumps(status())
