"""Affiches et logos à la bonne taille, gardés sur le disque.

Les fournisseurs envoient souvent des affiches de 1000 × 1500 (500 Ko et plus) pour une case de 200 px : les décoder
pendant le défilement fait sauter des images, et elles sont retéléchargées à chaque lancement. Ici chaque image est
téléchargée une fois, réduite à la taille affichée (WebP), gardée dans ``cache/img`` et servie ensuite instantanément.
"""
from __future__ import annotations

import hashlib
import io
import os
import threading
import time
from pathlib import Path

from ..config import paths
from ..logging_setup import get_logger

log = get_logger("stream.images")

# largeurs servies (en pixels réels) : peu de variantes par image, donc un cache qui sert vraiment
SIZES = (64, 96, 128, 192, 256, 384, 512, 768, 1280)
MAX_BYTES = 15 * 1024 * 1024          # une « image » plus grosse n'en est pas une
CACHE_LIMIT = 700 * 1024 * 1024       # au-delà, les plus anciennes sont effacées
FAIL_TTL = 15 * 60                    # une image introuvable n'est pas redemandée pendant 15 min

# comme le faisait la fenêtre de l'appli : certains hébergeurs d'images refusent les agents « python »
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_failed: dict[str, float] = {}
# Fils à part pour les images : une grille de 60 affiches ne doit jamais occuper les fils qui servent les listes,
# le guide ou les commandes du lecteur (sinon l'interface attendrait derrière les téléchargements).
_pool = None
_pool_guard = threading.Lock()


def pool():
    global _pool
    with _pool_guard:
        if _pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="img")
        return _pool
_pruned = 0.0


class ImageError(Exception):
    pass


def bucket(width: int) -> int:
    """La plus petite largeur servie qui couvre ``width``."""
    for s in SIZES:
        if s >= width:
            return s
    return SIZES[-1]


def cache_dir() -> Path:
    d = paths().cache / "img"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(url: str, width: int) -> str:
    return hashlib.sha1(f"{url}\n{width}".encode("utf-8")).hexdigest()


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        if len(_locks) > 2000:
            _locks.clear()
        return _locks.setdefault(key, threading.Lock())


def shrink(data: bytes, width: int) -> bytes:
    """Réduit ``data`` à ``width`` pixels de large (jamais agrandie), en WebP ; la transparence des logos est gardée."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as im:
        if im.format == "JPEG":
            im.draft("RGB", (width, width * 4))  # décodage JPEG directement à petite échelle : bien plus rapide
        im = ImageOps.exif_transpose(im)
        alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if alpha else "RGB")
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "WEBP", quality=82, method=4)
        return out.getvalue()


def _download(url: str, user_agent: str) -> bytes:
    import httpx

    headers = {"User-Agent": user_agent or BROWSER_UA, "Accept": "image/avif,image/webp,image/*,*/*;q=0.5"}
    with httpx.Client(timeout=httpx.Timeout(15, connect=8), follow_redirects=True, headers=headers) as c:
        with c.stream("GET", url) as r:
            if r.status_code != 200:
                raise ImageError(f"HTTP {r.status_code}")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    raise ImageError("trop grosse")
            return bytes(buf)


def cached(url: str, width: int) -> Path | None:
    """L'image déjà réduite, sans rien télécharger (vérifié directement, sans passer par un fil)."""
    if not url.lower().startswith(("http://", "https://")):
        return None
    key = _key(url, bucket(max(16, int(width))))
    p = cache_dir() / key[:2] / f"{key}.webp"
    return p if p.exists() else None


def get(url: str, width: int, user_agent: str = "") -> Path:
    """Chemin de l'image ``url`` réduite à ``width`` px (téléchargée la première fois). Lève ImageError sinon."""
    if not url.lower().startswith(("http://", "https://")):
        raise ImageError("adresse refusée")
    width = bucket(max(16, int(width)))
    key = _key(url, width)
    path = cache_dir() / key[:2] / f"{key}.webp"
    if path.exists():
        return path
    if time.time() - _failed.get(key, 0) < FAIL_TTL:
        raise ImageError("introuvable (récemment)")
    with _lock(key):
        if path.exists():
            return path
        try:
            data = shrink(_download(url, user_agent), width)
        except Exception as e:  # noqa: BLE001 — réseau, image abîmée, format inconnu : la case affiche le nom
            _failed[key] = time.time()
            raise ImageError(str(e)) from e
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    _maybe_prune()
    return path


def _maybe_prune() -> None:
    global _pruned
    if time.time() - _pruned < 600:
        return
    _pruned = time.time()
    threading.Thread(target=prune, name="img-prune", daemon=True).start()


def prune(limit: int = CACHE_LIMIT) -> int:
    """Efface les images les moins récemment écrites au-delà de ``limit`` octets. Renvoie le nombre effacé."""
    files = []
    total = 0
    for p in cache_dir().rglob("*.webp"):
        try:
            st = p.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, p))
        total += st.st_size
    removed = 0
    for _mtime, size, p in sorted(files):
        if total <= limit:
            break
        try:
            p.unlink()
            total -= size
            removed += 1
        except OSError:
            pass
    if removed:
        log.info("Image cache: %d old images removed", removed)
    return removed
