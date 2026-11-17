"""Le lecteur : libmpv dessine dans une fenêtre enfant posée sur la zone vidéo de l'interface.

Rien de l'interface web ne passe par-dessus la vidéo : les contrôles sur l'image (bandeau de la chaîne, barre de
lecture) sont dessinés par mpv lui-même (``osd-overlay`` en ASS), avec une vraie transparence, en fenêtré comme en
plein écran. Souris et clavier sur la vidéo arrivent par les raccourcis de mpv et déclenchent les mêmes actions que
l'interface. Un flux en direct qui coupe est relancé tout seul.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
import time
from ctypes import wintypes

import numpy as np
from pathlib import Path
from typing import Any, Callable

from ..config import app_root
from ..errors import MediaLabError
from ..events import bus
from ..logging_setup import get_logger
from . import audio
from .xtream import redact

log = get_logger("player")


# ------------------------------------------------------------------ libmpv
def mpv_dir() -> Path | None:
    roots = [app_root() / "bin" / "mpv", Path(__file__).resolve().parents[3] / "build" / "mpv"]
    if os.environ.get("SE7EN_MPV_DIR"):
        roots.insert(0, Path(os.environ["SE7EN_MPV_DIR"]))
    return next((r for r in roots if (r / "libmpv-2.dll").is_file()), None)


_mpv_mod = None


def _mpv():
    global _mpv_mod
    if _mpv_mod is None:
        d = mpv_dir()
        if d is None:
            raise MediaLabError("mpv_missing", "Le moteur de lecture (libmpv) est introuvable.", "Réinstalle se7en IPTV Player.")
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(d))
        import mpv  # noqa: PLC0415
        _mpv_mod = mpv
    return _mpv_mod


# ------------------------------------------------------------------ fenêtre hôte (Win32)
_u32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
_g32 = ctypes.WinDLL("gdi32") if sys.platform == "win32" else None
if _u32:
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.DefWindowProcW.restype = ctypes.c_ssize_t
    _u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                     wintypes.HINSTANCE, wintypes.LPVOID]
    _u32.CreateWindowExW.restype = wintypes.HWND
    _u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT]
    _u32.FindWindowW.restype = wintypes.HWND
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.SendMessageW.restype = ctypes.c_ssize_t
    _u32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
    _u32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    _u32.SetParent.restype = wintypes.HWND
    _u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _u32.MonitorFromWindow.restype = wintypes.HANDLE
    _u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u32.SetFocus.argtypes = [wintypes.HWND]
    _u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _u32.GetAsyncKeyState.restype = ctypes.c_short
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _u32.GetAncestor.restype = wintypes.HWND
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _k32.GetModuleHandleW.restype = wintypes.HMODULE
    _g32.GetStockObject.restype = wintypes.HGDIOBJ
    _g32.CreateRoundRectRgn.restype = wintypes.HANDLE

    class _WNDCLASS(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR)]

WM_CLOSE = 0x0010
MA_NOACTIVATE = 3
WM_FULLSCREEN = 0x8000 + 1  # WM_APP + 1 : wParam 1 = entrer, 0 = sortir (traité sur le fil de la fenêtre)
CHILD_STYLE = 0x40000000 | 0x04000000 | 0x02000000  # CHILD | CLIPSIBLINGS | CLIPCHILDREN
POPUP_STYLE = 0x80000000 | 0x10000000 | 0x02000000  # POPUP | VISIBLE | CLIPCHILDREN


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class Host:
    """Fenêtre où mpv dessine : enfant de la fenêtre de l'appli, ou fenêtre à part (mode navigateur / développement)."""

    def __init__(self, parent_title: str) -> None:
        self.parent_title = parent_title
        self.hwnd = None
        self.parent = None
        self.child = False
        self.fullscreen = False
        self.last = (0, 0, 1, 1, False, 0)  # dernière place dans la page (rendue en sortant du plein écran)
        self.on_close: Callable[[], None] = lambda: None
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)
        threading.Thread(target=self._loop, name="player-host", daemon=True).start()
        self._ready.wait(5)

    def _wndproc(self, h, m, w, l):
        if m == 0x0021:  # WM_MOUSEACTIVATE : dans la page, la vidéo ne vole pas le focus clavier à l'interface
            return MA_NOACTIVATE if self.child and not self.fullscreen else _u32.DefWindowProcW(h, m, w, l)
        if m == WM_FULLSCREEN:
            self._enter_fullscreen() if w else self._exit_fullscreen()
            return 0
        if m == WM_CLOSE and self.fullscreen:  # Alt+F4 en plein écran : on revient dans l'appli
            self.on_close()
            return 0
        if m == WM_CLOSE and not self.child:
            _u32.ShowWindow(h, 0)
            bus.publish("player", {"event": "closed"})
            return 0
        return _u32.DefWindowProcW(h, m, w, l)

    def _loop(self) -> None:
        wc = _WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.hInstance = _k32.GetModuleHandleW(None)
        # une classe par fenêtre : la classe porte la procédure de fenêtre, et chaque Host (lecteur affiché, lecteur de
        # réserve du zapping instantané) doit recevoir ses propres messages
        Host._count = getattr(Host, "_count", 0) + 1
        self.cls = f"se7enIPTVPlayerVideo{Host._count}"
        wc.lpszClassName = self.cls
        wc.hbrBackground = _g32.GetStockObject(4)  # noir
        wc.hCursor = _u32.LoadCursorW(None, 32512)
        _u32.RegisterClassW(ctypes.byref(wc))
        self.parent = _u32.FindWindowW(None, self.parent_title)
        if self.parent:
            self.child = True
            style = 0x40000000 | 0x04000000 | 0x02000000  # CHILD | CLIPSIBLINGS | CLIPCHILDREN (caché au départ)
            self.hwnd = _u32.CreateWindowExW(0, self.cls, "video", style, 0, 0, 16, 9, self.parent, None,
                                             wc.hInstance, None)
        else:  # pas de fenêtre d'appli (navigateur, tests) : une fenêtre à part
            style = 0x00CF0000  # WS_OVERLAPPEDWINDOW
            self.hwnd = _u32.CreateWindowExW(0, self.cls, "se7en IPTV Player", style, 120, 120, 960, 540, None, None,
                                             wc.hInstance, None)
        self._ready.set()
        msg = wintypes.MSG()
        while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _u32.TranslateMessage(ctypes.byref(msg))
            _u32.DispatchMessageW(ctypes.byref(msg))

    # ---------------------------------------------------------------- plein écran natif
    def set_fullscreen(self, on: bool) -> None:
        """La vidéo seule, dans sa propre fenêtre sur tout l'écran (celui de l'appli) : elle prend toute la place, garde
        le clavier (Échap, F, flèches) et la souris (double-clic, boutons du bandeau)."""
        if self.hwnd and self.child:  # SendMessage : fait sur le fil de la fenêtre, et fini quand on continue
            _u32.SendMessageW(self.hwnd, WM_FULLSCREEN, 1 if on else 0, 0)

    def _enter_fullscreen(self) -> None:
        if self.fullscreen:
            return
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        _u32.GetMonitorInfoW(_u32.MonitorFromWindow(self.parent or self.hwnd, 2), ctypes.byref(info))  # 2 : le plus proche
        r = info.rcMonitor
        self.fullscreen = True
        _u32.SetWindowRgn(self.hwnd, None, True)
        _u32.SetParent(self.hwnd, None)
        _u32.SetWindowLongPtrW(self.hwnd, -16, POPUP_STYLE)  # GWL_STYLE
        _u32.SetWindowLongPtrW(self.hwnd, -8, self.parent or 0)  # GWLP_HWNDPARENT : appartient à l'appli (pas d'icône à part)
        _u32.SetWindowPos(self.hwnd, wintypes.HWND(0), r.left, r.top, r.right - r.left, r.bottom - r.top, 0x0040 | 0x0020)
        # au premier plan même si la demande vient du téléphone, appli en arrière-plan (sans rester « toujours devant »)
        _u32.SetWindowPos(self.hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # TOPMOST, NOSIZE|NOMOVE|NOACTIVATE
        _u32.SetWindowPos(self.hwnd, wintypes.HWND(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # NOTOPMOST
        _u32.SetForegroundWindow(self.hwnd)
        _u32.SetFocus(self.hwnd)
        log.info("Video full screen %dx%d", r.right - r.left, r.bottom - r.top)

    def _exit_fullscreen(self) -> None:
        if not self.fullscreen:
            return
        self.fullscreen = False
        _u32.SetWindowLongPtrW(self.hwnd, -8, 0)
        _u32.SetWindowLongPtrW(self.hwnd, -16, CHILD_STYLE)
        _u32.SetParent(self.hwnd, self.parent)
        _u32.SetWindowPos(self.hwnd, wintypes.HWND(0), 0, 0, 1, 1, 0x0020 | 0x0010 | 0x0080)  # FRAMECHANGED, caché
        self.place(*self.last)
        if self.parent:
            _u32.SetForegroundWindow(self.parent)

    def place(self, x: int, y: int, w: int, h: int, visible: bool, radius: int = 0) -> None:
        if not self.hwnd:
            return
        self.last = (x, y, w, h, visible, radius)
        if self.fullscreen:  # en plein écran la page ne décide plus de la place de la vidéo
            return
        if not self.child:
            _u32.ShowWindow(self.hwnd, 5 if visible else 0)
            return
        flags = 0x0010 | (0x0040 if visible and w > 1 and h > 1 else 0x0080)  # NOACTIVATE | SHOW / HIDE
        _u32.SetWindowPos(self.hwnd, wintypes.HWND(0), x, y, max(1, w), max(1, h), flags)
        # pas de coins arrondis découpés (SetWindowRgn) : une région sur une fenêtre vidéo oblige Windows à recomposer
        # chaque image, et la lecture saccade


# ------------------------------------------------------------------ dessin des contrôles (ASS)
def _rr(x: float, y: float, w: float, h: float, r: float) -> str:
    r = min(r, w / 2, h / 2)
    k = r * 0.4477
    return (f"m {x + r:.0f} {y:.0f} l {x + w - r:.0f} {y:.0f} b {x + w - k:.0f} {y:.0f} {x + w:.0f} {y + k:.0f} {x + w:.0f} {y + r:.0f} "
            f"l {x + w:.0f} {y + h - r:.0f} b {x + w:.0f} {y + h - k:.0f} {x + w - k:.0f} {y + h:.0f} {x + w - r:.0f} {y + h:.0f} "
            f"l {x + r:.0f} {y + h:.0f} b {x + k:.0f} {y + h:.0f} {x:.0f} {y + h - k:.0f} {x:.0f} {y + h - r:.0f} "
            f"l {x:.0f} {y + r:.0f} b {x:.0f} {y + k:.0f} {x + k:.0f} {y:.0f} {x + r:.0f} {y:.0f}")


def _esc(s: str) -> str:
    return (s or "").replace("\\", "⧵").replace("{", "(").replace("}", ")").replace("\n", " ")


def _clock(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def nice_titles(title: str, subtitle: str = "") -> dict:
    """Titre et sous-titre lisibles à partir des noms du fournisseur, et la qualité en petit badge :
    « S1 E2 · |FR| MobLand 01x02 - Épisode 2 (UHD) » / « |FR| MobLand (UHD) » → « S1 E2 · Épisode 2 » / « MobLand » / « 4K »."""
    raw = f"{title} {subtitle}"
    low = raw.lower()
    badge = "4K" if re.search(r"\b(?:4k|uhd|2160p?)\b", low) else "HD" if re.search(r"\b(?:fhd|1080p?)\b", low) else ""

    def clean(s: str) -> str:
        s = _TAGS.sub(" ", s or "")
        s = _QUAL.sub(" ", s)
        s = re.sub(r"\b(?:S\d{1,2}\s*E\d{1,3}|\d{1,2}x\d{1,3})\b(?!\s*·)", " ", s, flags=re.I)
        s = re.sub(r"\s*\|\s*(?:19|20)\d\d\s*$", "", s)
        s = re.sub(r"(?:\s+(?:4k|uhd|fhd|hd|sd|hevc|\d{3,4}p))+\s*$", "", s, flags=re.I)
        s = re.sub(r"\s+", " ", s)
        return s.strip(" -–—:|·")

    sub = clean(subtitle)
    head, rest = "", title or ""
    m = re.match(r"^\s*(S\d+\s*E\d+)\s*·\s*(.*)$", rest)
    if m:
        head, rest = m.group(1), m.group(2)
    rest = clean(rest)
    if sub and rest.lower().startswith(sub.lower()) and len(rest) > len(sub):
        rest = rest[len(sub):].strip(" -–—:|·")
    elif sub and rest.lower() == sub.lower() and head:
        rest = ""
    show = " · ".join(x for x in (head, rest) if x) or clean(title) or (title or "")
    return {"title": show, "subtitle": sub, "badge": badge}


def _with_display(item: dict) -> dict:
    """L'élément lu, avec son titre lisible pour la page (``display_title``, ``display_subtitle``, ``badge``)."""
    names = nice_titles(item.get("title") or "", item.get("subtitle") or "")
    return {**item, "display_title": names["title"], "display_subtitle": names["subtitle"], "badge": names["badge"]}


_TAGS = re.compile(r"(?:^|\s)(?:\|[^|]{1,14}\||\[[A-Za-z]{2,4}\]|[A-Z]{2,3}:)(?=\s|$)")
_QUAL = re.compile(r"\s*[\(\[](?:4k|uhd|fhd|hd|sd|hevc|h\.?26[45]|x26[45]|blu-?ray|light|multi|vostfr|vost|vf[fq]?|vo|3d|hdr10?|dv|"
                   r"dolby vision|\d{3,4}p)(?:[\s/+,-]+[a-z0-9.]+)*[\)\]]", re.I)

# couleurs ASS (&HBBGGRR&)
WHITE, SOFT, DIM, RED = "&HFFFFFF&", "&HD9D9D9&", "&HA6A6A6&", "&H3B30E5&"
PANEL = "&H1C1816&"


def _shape(d: str, col: str = WHITE, alpha: str = "00", blur: float = 0) -> str:
    return rf"{{\an7\pos(0,0)\bord0\shad0{rf'\blur{blur:g}' if blur else ''}\1c{col}\1a&H{alpha}&\p1}}{d}"


def _text(x: float, y: float, t: str, size: float, an: int = 4, col: str = WHITE, bold: bool = True, shadow: bool = True) -> str:
    sh = r"\shad1\4c&H000000&\4a&H90&" if shadow else r"\shad0"
    return rf"{{\an{an}\pos({x:.0f},{y:.0f})\fnManrope\b{1 if bold else 0}\fs{size:.0f}\1c{col}\bord0{sh}}}{t}"


def _soft_box(ev: list[str], x: float, y: float, w: float, h: float, r: float, alpha: str = "26") -> None:
    """Panneau sombre aux bords nets, posé sur une ombre floue (rendu « carte » plutôt que bulle)."""
    ev.append(_shape(_rr(x, y + 6, w, h, r), "&H000000&", "90", blur=18))
    ev.append(_shape(_rr(x, y, w, h, r), PANEL, alpha))


def _veil(ev: list[str], top: bool, height: int, strength: float, W: int, H: int) -> None:
    """Dégradé noir en haut ou en bas de l'image, pour lire le texte même sur une scène claire."""
    # bandes jointives aux bords entiers (sinon les recouvrements d'un pixel dessinent des rayures)
    n = 60
    edges = [round(height * i / n) for i in range(n + 1)]
    for i in range(n):
        f = (i + 0.5) / n  # 0 → 1 en allant vers le bord
        a = 255 - int(255 * strength * f ** 1.8)
        if a >= 254:
            continue
        y0, y1 = (height - edges[i + 1], height - edges[i]) if top else (H - height + edges[i], H - height + edges[i + 1])
        ev.append(_shape(f"m 0 {y0} l {W} {y0} l {W} {y1} l 0 {y1}", "&H000000&", f"{a:02X}"))


def osd(state: dict, W: int = 1920, H: int = 1080) -> tuple[str, list[dict]]:
    """En plein écran : titre en haut, barre de lecture en bas. En fenêtre (la page a déjà ses boutons et son titre) :
    seulement les messages. Renvoie l'ASS et les zones cliquables."""
    ev: list[str] = []
    hits: list[dict] = []
    if state.get("controls"):
        _veil(ev, True, 230, 0.62, W, H)
        _banner(ev, state, W)
        hits += _controls(ev, state, W, H)
    card = state.get("next_card")
    if card:
        hits[:0] = _next_card(ev, card, state, W, H)
    elif state.get("skip_intro"):
        hits[:0] = _skip_intro(ev, state, W, H)
    msg = state.get("message")
    if msg:
        t = _esc(msg)
        w = min(W - 160, 72 + 15.5 * len(t))
        y = H / 2 - 36 if not state.get("controls") else H * 0.36
        _soft_box(ev, W / 2 - w / 2, y, w, 72, 20, "1E")
        ev.append(_text(W / 2, y + 36, t, 28, an=5, shadow=False))
    return "\n".join(ev), hits


def _banner(ev: list[str], state: dict, W: int) -> None:
    """En haut à gauche : nom de la série / chaîne en petit, titre en grand ; en direct : numéro, programme en cours."""
    names = nice_titles(state.get("title") or "", state.get("subtitle") or "")
    title, sub = _esc(names["title"]), _esc(names["subtitle"])
    live = state.get("kind") == "live"
    x = 72
    if state.get("number"):
        num = str(state["number"])
        bw = 34 + 22 * len(num)
        ev.append(rf"{{\an7\pos(0,0)\bord2\shad0\3c{WHITE}\3a&H70&\1a&HFF&\p1}}{_rr(x, 58, bw, 74, 16)}")
        ev.append(_text(x + bw / 2, 95, num, 40, an=5, shadow=False))
        x += bw + 24
    # ligne du dessus : série / chaîne · badges
    y1 = 66
    over = []
    if live:
        over.append(("EN DIRECT", RED))
    if names["badge"]:
        over.append((names["badge"], None))
    cx = x
    for label, fill in over:
        w = 22 + 12.5 * len(label)
        if fill:
            ev.append(_shape(_rr(cx, y1 - 15, w, 30, 7), fill))
        else:
            ev.append(rf"{{\an7\pos(0,0)\bord1.5\shad0\3c{WHITE}\3a&H50&\1a&HFF&\p1}}{_rr(cx, y1 - 15, w, 30, 7)}")
        ev.append(_text(cx + w / 2, y1 + 1, label, 18, an=5, shadow=False))
        cx += w + 12
    if sub and sub.lower() != title.lower() and not title.lower().startswith(sub.lower() + " "):
        ev.append(_text(cx + (4 if over else 0), y1, sub[:60], 26, col=SOFT))
    ev.append(rf"{{\an7\pos({x},{y1 + 20})\fnManrope\b1\fs50\1c{WHITE}\bord0\shad2\4c&H000000&\4a&HB0&}}{title[:70]}")
    # en direct : programme en cours et suivant, à droite
    now = state.get("now")
    if live and now and now.get("start") and now.get("stop"):
        frac = max(0.0, min(1.0, (time.time() - now["start"]) / max(1, now["stop"] - now["start"])))
        rx, bw = W - 72, 460
        ev.append(_text(rx, 66, f"{_hm(now['start'])} – {_hm(now['stop'])}", 20, an=6, col=DIM))
        ev.append(_text(rx, 102, _esc(now.get("title") or "")[:38], 30, an=6))
        ev.append(_shape(_rr(rx - bw, 130, bw, 4, 2), WHITE, "B0"))
        ev.append(_shape(_rr(rx - bw, 130, max(4, bw * frac), 4, 2), RED))
        nxt = state.get("next")
        if nxt and nxt.get("start"):
            ev.append(_text(rx, 160, f"Ensuite · {_hm(nxt['start'])}  {_esc(nxt.get('title') or '')[:34]}", 22, an=6, col=SOFT, bold=False))


# ------------------------------------------------------------------ pistes
# nom français des langues : codes ISO 639-2 (B et T), ISO 639-1, variantes régionales (« fr-FR ») et noms anglais
_LANG_GROUPS = {
    "Français": "fre fra fr french francais français", "Anglais": "eng en english",
    "Arabe": "ara ar arabic", "Espagnol": "spa es spanish castellano", "Allemand": "ger deu de german",
    "Italien": "ita it italian", "Portugais": "por pt portuguese", "Turc": "tur tr turkish",
    "Néerlandais": "dut nld nl dutch", "Polonais": "pol pl polish", "Russe": "rus ru russian",
    "Japonais": "jpn ja japanese", "Coréen": "kor ko korean", "Chinois": "chi zho zh chinese mandarin",
    "Cantonais": "yue cantonese", "Grec": "gre ell el greek", "Hébreu": "heb he hebrew", "Hongrois": "hun hu hungarian",
    "Tchèque": "cze ces cs czech", "Slovaque": "slo slk sk slovak", "Roumain": "rum ron ro romanian",
    "Suédois": "swe sv swedish", "Norvégien": "nor no nob nb nno nn norwegian", "Danois": "dan da danish",
    "Finnois": "fin fi finnish", "Islandais": "ice isl is icelandic", "Thaï": "tha th thai",
    "Vietnamien": "vie vi vietnamese", "Indonésien": "ind id indonesian", "Malais": "may msa ms malay",
    "Ukrainien": "ukr uk ukrainian", "Bulgare": "bul bg bulgarian", "Croate": "hrv hr croatian",
    "Serbe": "srp sr serbian", "Slovène": "slv sl slovenian", "Bosniaque": "bos bs bosnian",
    "Albanais": "alb sqi sq albanian", "Macédonien": "mac mkd mk macedonian", "Lituanien": "lit lt lithuanian",
    "Letton": "lav lv latvian", "Estonien": "est et estonian", "Persan": "per fas fa persian farsi",
    "Hindi": "hin hi hindi", "Ourdou": "urd ur urdu", "Bengali": "ben bn bengali", "Tamoul": "tam ta tamil",
    "Télougou": "tel te telugu", "Kurde": "kur ku kurdish", "Arménien": "arm hye hy armenian",
    "Géorgien": "geo kat ka georgian", "Catalan": "cat ca catalan", "Basque": "baq eus eu basque",
    "Galicien": "glg gl galician", "Berbère": "ber kab tzm kabyle amazigh", "Swahili": "swa sw swahili",
    "Tagalog": "tgl fil tl tagalog filipino", "Latin": "lat la latin",
    "Version originale": "qaa vo vost original", "Plusieurs langues": "mul multi",
}
LANGS = {code: name for name, codes in _LANG_GROUPS.items() for code in codes.split()}
_CODECS = {"ac3": "Dolby Digital", "eac3": "Dolby Digital Plus", "truehd": "Dolby TrueHD", "dts": "DTS", "aac": "AAC",
           "mp3": "MP3", "mp2": "MP2", "opus": "Opus", "vorbis": "Vorbis", "flac": "FLAC", "pcm_s16le": "PCM",
           "hdmv_pgs_subtitle": "image", "dvd_subtitle": "image", "dvb_subtitle": "image", "dvb_teletext": "télétexte"}
_NOISE = re.compile(r"^(?:audio|sub(?:title)?s?|track|piste|stream|und)(?:[ _-]?\d+)?$", re.I)  # « audio_1 » des flux HLS


def lang_name(code: str | None) -> str:
    c = (code or "").strip().lower()
    if not c or c in ("und", "unk", "zxx", "mis"):
        return ""
    return LANGS.get(c) or LANGS.get(c.replace("_", "-").split("-")[0]) or c.upper()


def track_label(t: dict, n: int) -> str:
    """Nom lisible d'une piste : langue, titre du fichier (VFF, VFQ, Commentaires…) et ce qui la distingue des autres
    (sous-titres forcés ou pour sourds et malentendants, son 5.1)."""
    name = lang_name(t.get("lang"))
    title = (t.get("title") or "").strip()
    parts = [name] if name else []
    if title and not _NOISE.match(title) and title.lower() not in name.lower() and lang_name(title) != name:
        parts.append(title)
    low = title.lower()
    if t.get("forced") and "forc" not in low:
        parts.append("Forcés")
    if t.get("hearing_impaired") and not any(k in low for k in ("sdh", "malentend", "sourd", "hearing")):
        parts.append("Sourds et malentendants")
    ch = t.get("channels") or 0
    if t.get("type") == "audio" and ch > 2 and "5.1" not in low and "7.1" not in low:
        parts.append({6: "5.1", 8: "7.1"}.get(ch, f"{ch} canaux"))
    return (" · ".join(parts) or f"Piste {n}")[:48]


def track_labels(tracks: list[dict]) -> list[str]:
    """Noms des pistes d'un même type ; deux pistes au même nom sont départagées par leur format, puis numérotées."""
    base = [track_label(t, n + 1) for n, t in enumerate(tracks)]
    labels = list(base)
    for i, t in enumerate(tracks):
        twins = [u for u, lab in zip(tracks, base) if lab == base[i]]
        if len(twins) > 1 and len({(u.get("codec") or "").lower() for u in twins}) > 1:
            codec = (t.get("codec") or "").lower()
            labels[i] = f"{base[i]} · {_CODECS.get(codec, codec.upper())}" if codec else base[i]
    final = list(labels)
    seen: dict[str, int] = {}
    for i, lab in enumerate(labels):
        if labels.count(lab) > 1:
            seen[lab] = seen.get(lab, 0) + 1
            final[i] = f"{lab} ({seen[lab]})"
    return final


def _circle(cx: float, cy: float, r: float) -> str:
    return _rr(cx - r, cy - r, 2 * r, 2 * r, r)


def _poly(pts: list[tuple[float, float]]) -> str:
    return "m " + " l ".join(f"{x:.1f} {y:.1f}" for x, y in pts)


def _arc(cx: float, cy: float, r: float, w: float, a0: float, a1: float, n: int = 28) -> list[tuple[float, float]]:
    """Anneau d'épaisseur w entre deux angles (degrés, 0 = droite, sens des aiguilles d'une montre)."""
    import math
    angles = [math.radians(a0 + (a1 - a0) * i / n) for i in range(n + 1)]
    outer = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in angles]
    inner = [(cx + (r - w) * math.cos(a), cy + (r - w) * math.sin(a)) for a in reversed(angles)]
    return outer + inner


def _bar(x0: float, y0: float, x1: float, y1: float, t: float) -> list[tuple[float, float]]:
    """Trait épais de (x0, y0) à (x1, y1)."""
    import math
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy) or 1
    ox, oy = -dy / n * t / 2, dx / n * t / 2
    return [(x0 + ox, y0 + oy), (x1 + ox, y1 + oy), (x1 - ox, y1 - oy), (x0 - ox, y0 - oy)]


def _icon(kind: str, cx: float, cy: float, state: dict | None = None) -> list[str]:
    """Pictogramme blanc, plat, d'environ 36 px, centré sur (cx, cy) ; une ombre douce le détache de l'image."""
    shapes: list[str] = []
    texts: list[str] = []
    x, y = cx, cy
    if kind == "play":
        shapes.append(_poly([(x - 11, y - 17), (x + 17, y), (x - 11, y + 17)]))
    elif kind == "pause":
        shapes += [_rr(x - 13, y - 16, 9, 32, 2.5), _rr(x + 4, y - 16, 9, 32, 2.5)]
    elif kind in ("back", "fwd"):
        # flèche circulaire : vers la gauche pour reculer, vers la droite pour avancer, « 10 » au milieu
        r, w = 17, 3.2
        pts = _arc(x, y, r, w, -90, 210) if kind == "back" else _arc(x, y, r, w, -30, 270)
        shapes.append(_poly(pts))
        top = y - r + w / 2
        tip = [(x - 8, top), (x + 1, top - 7.5), (x + 1, top + 7.5)] if kind == "back" else [(x + 8, top), (x - 1, top - 7.5), (x - 1, top + 7.5)]
        shapes.append(_poly(tip))
        texts.append(rf"{{\an5\pos({x:.0f},{y + 1:.0f})\fnManrope\b1\fs15\1c{WHITE}\bord0\shad0}}10")
    elif kind == "subs":
        texts.append(rf"{{\an7\pos(0,0)\bord2.6\shad0\3c{WHITE}\1a&HFF&\p1}}{_rr(x - 17, y - 12, 34, 24, 5)}")
        shapes += [_rr(x - 10, y - 1, 8, 3.2, 1.6), _rr(x + 0, y - 1, 10, 3.2, 1.6),
                   _rr(x - 10, y + 5, 13, 3.2, 1.6), _rr(x + 5, y + 5, 5, 3.2, 1.6)]
    elif kind == "audio":
        for n, h in enumerate((8, 18, 28, 14, 22, 8)):
            shapes.append(_rr(x - 16 + n * 6, y - h / 2, 3.4, h, 1.7))
    elif kind in ("up", "down"):
        d = -1 if kind == "up" else 1
        shapes.append(_poly(_bar(x - 11, y - 5 * d, x, y + 6 * d, 3.6)))
        shapes.append(_poly(_bar(x, y + 6 * d, x + 11, y - 5 * d, 3.6)))
    elif kind == "mute":
        shapes.append(_poly([(x - 16, y - 6), (x - 9, y - 6), (x + 1, y - 14), (x + 1, y + 14), (x - 9, y + 6), (x - 16, y + 6)]))
        vol = (state or {}).get("volume") or 0
        if (state or {}).get("muted") or vol <= 0:
            shapes.append(_poly(_bar(x + 7, y - 6, x + 17, y + 6, 3)))
            shapes.append(_poly(_bar(x + 17, y - 6, x + 7, y + 6, 3)))
        else:
            shapes.append(_poly(_arc(x + 1, y, 9, 3, -45, 45, 12)))
            if vol > 45:
                shapes.append(_poly(_arc(x + 1, y, 16, 3, -50, 50, 16)))
    elif kind == "gear":
        import math
        shapes.append(_poly(_arc(x, y, 11, 4.2, 0, 360, 36)))
        for k in range(8):  # huit dents
            a = math.radians(k * 45)
            shapes.append(_poly(_bar(x + 9 * math.cos(a), y + 9 * math.sin(a), x + 15.5 * math.cos(a), y + 15.5 * math.sin(a), 5.6)))
    elif kind in ("fs_on", "fs_off"):
        out = kind == "fs_on"
        a, b, arm, t = (15, 12, 9, 3.4) if out else (6, 5, 9, 3.4)
        for sx in (-1, 1):
            for sy in (-1, 1):
                px, py = x + sx * a, y + sy * b
                dx, dy = (-sx, -sy) if out else (sx, sy)
                shapes.append(_rr(min(px, px + dx * arm) - t / 2, py - t / 2, arm + t, t, 1.2))
                shapes.append(_rr(px - t / 2, min(py, py + dy * arm) - t / 2, t, arm + t, 1.2))
    out_ev = [_shape(" ".join(shapes), "&H000000&", "A0", blur=3).replace(r"\pos(0,0)", r"\pos(0,1)")] if shapes else []
    out_ev += [_shape(s) for s in shapes]
    return out_ev + texts


def _tip(ev: list[str], cx: float, bottom: float, text: str, W: int) -> None:
    """Nom du bouton survolé (ou temps visé sur la barre) dans une petite étiquette sombre."""
    t = _esc(text)
    w = 30 + 10.6 * len(t)
    cx = max(w / 2 + 24, min(W - w / 2 - 24, cx))
    _soft_box(ev, cx - w / 2, bottom - 40, w, 40, 10, "18")
    ev.append(_text(cx, bottom - 20, t, 20, an=5, shadow=False))


VOL_W = 120  # largeur de la réglette du volume (bandeau 1920 × 1080)


def _controls(ev: list[str], state: dict, W: int, H: int) -> list[dict]:
    """En bas, comme les grands services de streaming : la barre de progression sur toute la largeur, puis une ligne
    d'icônes plates. À gauche : lecture / pause, −10 s, +10 s, son, temps. À droite : chaîne − / + (direct), langue
    audio, sous-titres, plein écran. Audio et sous-titres ouvrent une liste."""
    hits: list[dict] = []
    live = state.get("kind") == "live"
    paused = bool(state.get("paused"))
    full = bool(state.get("fullscreen"))
    hover = state.get("hover")
    vol = "Son coupé" if state.get("muted") else f"Son {int(state.get('volume') or 0)} %"
    _veil(ev, False, 300, 0.78, W, H)
    pad = 72
    cy = H - 66
    ty = H - 128
    tip = None
    # ---- barre de progression
    dur, pos = state.get("duration") or 0, state.get("position") or 0
    tx, tw = pad, W - 2 * pad
    if dur > 0 and not live:
        frac = max(0.0, min(1.0, pos / dur))
        over = bool(hover and tx - 12 <= hover[0] <= tx + tw + 12 and ty - 22 <= hover[1] <= ty + 22)
        bh = 8 if over else 4
        y0 = ty - bh / 2
        ev.append(_shape(_rr(tx, y0, tw, bh, bh / 2), WHITE, "B4"))
        cache = state.get("cached") or 0
        if cache > pos:
            ev.append(_shape(_rr(tx, y0, max(bh, tw * min(1, cache / dur)), bh, bh / 2), WHITE, "98"))
        if over:
            hf = max(0.0, min(1.0, (hover[0] - tx) / tw))
            ev.append(_shape(_rr(tx, y0, max(bh, tw * hf), bh, bh / 2), WHITE, "80"))
            tip = (tx + tw * hf, _clock(hf * dur), ty - 18)
        ev.append(_shape(_rr(tx, y0, max(bh, tw * frac), bh, bh / 2), RED))
        if over:
            ev.append(_shape(_circle(tx + tw * frac, ty, 9), RED))
        hits.append({"id": "seek", "x": tx, "y": ty - 22, "w": tw, "h": 44})
    # ---- boutons
    left = [("pause", "play" if paused else "pause", "Lecture (Espace)" if paused else "Pause (Espace)")]
    if not live:
        left += [("back", "back", "Reculer de 10 s"), ("fwd", "fwd", "Avancer de 10 s")]
    left += [("mute", "mute", vol), ("vol", "slider", vol)]
    right = []
    if live:
        right += [("zap_up", "up", "Chaîne précédente"), ("zap_down", "down", "Chaîne suivante")]
    right += [("opts", "gear", "Vitesse et format d'image"), ("audio", "audio", "Langue audio"), ("subs", "subs", "Sous-titres"),
              ("exit_fs" if full else "fs", "fs_off" if full else "fs_on", "Quitter le plein écran (Échap)" if full else "Plein écran (F)")]
    S = 64  # place d'un bouton

    def button(hid: str, icon: str, name: str, cx: float) -> None:
        nonlocal tip
        hot = bool(hover and abs(hover[0] - cx) <= S / 2 and abs(hover[1] - cy) <= S / 2)
        if hot or {"aid": "audio", "sid": "subs", "opt": "opts"}.get(state.get("menu") or "") == hid:
            ev.append(_shape(_circle(cx, cy, 28), WHITE, "E0"))
        ev.extend(_icon(icon, cx, cy, state))
        hits.append({"id": hid, "x": cx - S / 2, "y": cy - S / 2, "w": S, "h": S})
        if hot:
            tip = (cx, name, cy - 38)

    x = pad - 14
    for hid, icon, name in left:
        if icon == "slider":
            w = VOL_W
            v = 0 if state.get("muted") else max(0.0, min(1.0, (state.get("volume") or 0) / 100))
            hot = bool(hover and x - 6 <= hover[0] <= x + w + 6 and cy - 22 <= hover[1] <= cy + 22)
            h = 6 if hot else 4
            ev.append(_shape(_rr(x, cy - h / 2, w, h, h / 2), WHITE, "B0"))
            if v > 0:
                ev.append(_shape(_rr(x, cy - h / 2, max(h, w * v), h, h / 2), WHITE))
            ev.append(_shape(_circle(x + w * v, cy, 8 if hot else 6), WHITE))
            hits.append({"id": "vol", "x": x, "y": cy - 22, "w": w, "h": 44})
            if hot:
                tip = (x + w / 2, name, cy - 38)
            x += w + 28
            continue
        button(hid, icon, name, x + S / 2)
        x += S + (8 if hid == "pause" else 0)
    # temps : où on en est, et à quelle heure ça finit
    if dur > 0 and not live:
        end = time.strftime("%H:%M", time.localtime(time.time() + max(0.0, dur - pos) / max(0.1, state.get("speed") or 1)))
        ev.append(_text(x, cy, f"{_clock(pos)} / {_clock(dur)}" + rf"{{\b0\fs22\1c{DIM}}}    Fin à {end}", 24))
    else:
        txt = "Mise en mémoire…" if state.get("buffering") else f"En direct · {time.strftime('%H:%M')}"
        ev.append(_shape(_circle(x + 6, cy, 6), RED))
        ev.append(_text(x + 22, cy, txt, 24))
    x = W - pad + 14 - S * len(right)
    for hid, icon, name in right:
        button(hid, icon, name, x + S / 2)
        x += S
    menu = state.get("menu")
    if tip and not menu:
        _tip(ev, tip[0], tip[2], tip[1], W)
    if menu in ("aid", "sid"):
        _track_menu(ev, hits, state, menu, W)
    elif menu == "opt":
        _opts_menu(ev, hits, state, W)
    return hits


def _track_menu(ev: list[str], hits: list[dict], state: dict, menu: str, W: int) -> None:
    """Liste des pistes (audio ou sous-titres) au-dessus de son bouton ; plusieurs colonnes s'il y en a beaucoup."""
    kind = "audio" if menu == "aid" else "sub"
    hover = state.get("hover")
    tracks = [t for t in state.get("tracks") or [] if t.get("type") == kind]
    rows = ([{"id": "no", "label": "Désactivés", "selected": not any(t.get("selected") for t in tracks)}] if menu == "sid" else []) + [
        {"id": str(t.get("id")), "label": _esc(lab), "selected": bool(t.get("selected"))}
        for t, lab in zip(tracks, track_labels(tracks))]
    if not tracks:  # le fichier du fournisseur n'en contient pas : on le dit, plutôt qu'une liste vide
        rows.append({"id": "", "label": "Aucun sous-titre dans ce fichier" if menu == "sid" else "Une seule langue",
                     "selected": False, "note": True})
    anchor = next(h for h in hits if h["id"] == ("audio" if menu == "aid" else "subs"))
    foot = 2 * 56 + 12 if menu == "sid" else 0  # réglages des sous-titres sous la liste
    room = anchor["y"] - 16 - 16 - 60 - foot
    cw = 380
    for rh in (52, 44, 36):
        per = max(1, int(room // rh))
        cols = -(-len(rows) // per)
        if cols * cw + 32 <= W:
            break
    cols = min(cols, max(1, int((W - 32) // cw)))
    per = -(-len(rows) // cols)
    rows = rows[:per * cols]
    mw, mh = max(cw * cols + 16, 520 if foot else 0), 60 + rh * per + 12 + foot
    mx = max(16, min(W - mw - 16, anchor["x"] + anchor["w"] / 2 - mw / 2))
    my = anchor["y"] - 12 - mh
    _soft_box(ev, mx, my, mw, mh, 18, "12")
    ev.append(_text(mx + 28, my + 32, "Langue audio" if menu == "aid" else "Sous-titres", 20, col=DIM, shadow=False))
    fs = 24 if rh >= 52 else 21
    for n, row in enumerate(rows):
        cx0 = mx + 8 + (n // per) * cw
        ry = my + 56 + (n % per) * rh
        hot = bool(hover and row["id"] and cx0 <= hover[0] <= cx0 + cw and ry <= hover[1] <= ry + rh)
        if hot:
            ev.append(_shape(_rr(cx0 + 4, ry + 3, cw - 8, rh - 6, 10), WHITE, "EA"))
        if row["selected"]:  # coche à gauche
            mx0, my0 = cx0 + 24, ry + rh / 2
            ev.append(_shape(_poly(_bar(mx0 - 7, my0, mx0 - 2, my0 + 5, 3.2)) + " " + _poly(_bar(mx0 - 2, my0 + 5, mx0 + 8, my0 - 6, 3.2)), WHITE))
        colour = "&H8C8C8C&" if row.get("note") else (WHITE if row["selected"] else SOFT)
        ev.append(_text(cx0 + 46, ry + rh / 2, row["label"], 20 if row.get("note") else fs, col=colour,
                        bold=bool(row["selected"]), shadow=False))
        if row["id"]:
            hits.insert(0, {"id": f"track:{menu}:{row['id']}", "x": cx0, "y": ry, "w": cw, "h": rh})
    if foot:
        fy = my + 60 + rh * per + 12
        ev.append(_shape(_rr(mx + 20, fy - 6, mw - 40, 1.5, 0.75), WHITE, "D8"))
        delay = float(state.get("sub_delay") or 0)
        scale = float(state.get("sub_scale") or 1)
        _stepper(ev, hits, mx, fy + 28, mw, "Décalage", f"{delay:+.1f} s".replace(".", ","), "subdelay:-", "subdelay:+",
                 "subdelay:0" if abs(delay) > 0.01 else None, hover)
        _stepper(ev, hits, mx, fy + 84, mw, "Taille", f"{round(scale * 100)} %", "subsize:-", "subsize:+",
                 "subsize:0" if abs(scale - 1) > 0.01 else None, hover)
    # après les pistes (elles passent d'abord), avant les boutons : un clic dans la liste hors piste ne fait rien
    hits.insert(sum(1 for h in hits if h["id"].startswith(("track:", "subdelay:", "subsize:"))),
                {"id": "menu_area", "x": mx, "y": my, "w": mw, "h": mh})


def _stepper(ev: list[str], hits: list[dict], mx: float, cy: float, mw: float, label: str, value: str, minus: str,
             plus: str, reset: str | None, hover) -> None:
    """Une ligne « Décalage   [−]  +0,5 s  [+]   Remettre » dans un menu."""
    ev.append(_text(mx + 28, cy, label, 22, col=SOFT, bold=False, shadow=False))
    bx = mx + mw - 28 - 44 - 120 - 44 - (110 if reset else 0)
    for hid, cx, sign in ((minus, bx + 22, "−"), (plus, bx + 44 + 120 + 22, "+")):
        hot = bool(hover and abs(hover[0] - cx) <= 22 and abs(hover[1] - cy) <= 22)
        ev.append(_shape(_circle(cx, cy, 20), WHITE, "C8" if hot else "E2"))
        ev.append(_text(cx, cy - 1, sign, 28, an=5, shadow=False))
        hits.insert(0, {"id": hid, "x": cx - 22, "y": cy - 22, "w": 44, "h": 44})
    ev.append(_text(bx + 44 + 60, cy, value, 22, an=5, shadow=False))
    if reset:
        rx = mx + mw - 28 - 96
        hot = bool(hover and rx <= hover[0] <= rx + 96 and abs(hover[1] - cy) <= 20)
        ev.append(_text(rx + 48, cy, "Remettre", 19, an=5, col=WHITE if hot else DIM, bold=hot, shadow=False))
        hits.insert(0, {"id": reset, "x": rx, "y": cy - 20, "w": 96, "h": 40})


SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
ASPECTS = (("-1", "Auto"), ("16:9", "16:9"), ("4:3", "4:3"), ("2.35:1", "2,35"), ("zoom", "Remplir"))


def _opts_menu(ev: list[str], hits: list[dict], state: dict, W: int) -> None:
    """Réglages en plein écran : vitesse de lecture et format d'image, en pastilles."""
    hover = state.get("hover")
    anchor = next(h for h in hits if h["id"] == "opts")
    live = state.get("kind") == "live"
    rows = ([("Vitesse", [(f"speed:{v:g}", "Normale" if v == 1 else f"×{v:g}".replace(".", ","),
                           abs(float(state.get("speed") or 1) - v) < 0.01) for v in SPEEDS])] if not live else [])
    zoom = float(state.get("panscan") or 0) > 0.5
    rows.append(("Format d'image", [(f"aspect:{v}", lab, zoom if v == "zoom" else (not zoom and str(state.get("aspect") or "-1") in (v, v.replace(":", "/"))))
                                     for v, lab in ASPECTS]))
    mw = 48 + max(sum(30 + 12.2 * len(lab) + 10 for _h, lab, _o in chips) - 10 for _l, chips in rows)
    mh = 24 + len(rows) * 104 + 8
    mx = max(16, min(W - mw - 16, anchor["x"] + anchor["w"] / 2 - mw / 2))
    my = anchor["y"] - 12 - mh
    _soft_box(ev, mx, my, mw, mh, 18, "12")
    for r, (label, chips) in enumerate(rows):
        ry = my + 24 + r * 104
        ev.append(_text(mx + 28, ry + 14, label, 20, col=DIM, shadow=False))
        cx = mx + 24
        for hid, lab, on in chips:
            w = 30 + 12.2 * len(lab)
            hot = bool(hover and cx <= hover[0] <= cx + w and ry + 34 <= hover[1] <= ry + 80)
            if on:
                ev.append(_shape(_rr(cx, ry + 36, w, 44, 22), WHITE))
            else:
                ev.append(_shape(_rr(cx, ry + 36, w, 44, 22), WHITE, "C8" if hot else "E6"))
            ev.append(_text(cx + w / 2, ry + 58, lab, 20, an=5, col="&H16120F&" if on else WHITE, shadow=False))
            hits.insert(0, {"id": hid, "x": cx, "y": ry + 36, "w": w, "h": 44})
            cx += w + 10
    hits.insert(sum(1 for h in hits if h["id"].startswith(("speed:", "aspect:"))), {"id": "menu_area", "x": mx, "y": my, "w": mw, "h": mh})


def _skip_intro(ev: list[str], state: dict, W: int, H: int) -> list[dict]:
    """« Passer l'intro » en bas à droite pendant le générique d'ouverture."""
    hover = state.get("hover")
    w, h = 250, 60
    x = W - 72 - w
    y = (H - 128 - 40 - h) if state.get("controls") else (H - 72 - h)
    hot = bool(hover and x <= hover[0] <= x + w and y <= hover[1] <= y + h)
    ev.append(_shape(_rr(x, y + 6, w, h, 14), "&H000000&", "90", blur=16))
    ev.append(_shape(_rr(x, y, w, h, 14), WHITE if hot else "&HE6E6E6&"))
    ev.append(_shape(_poly([(x + 28, y + 19), (x + 42, y + 30), (x + 28, y + 41)]) + " " +
                     _poly([(x + 42, y + 19), (x + 56, y + 30), (x + 42, y + 41)]), "&H16120F&"))
    ev.append(_text(x + 72, y + 30, "Passer l'intro", 24, col="&H16120F&", shadow=False))
    return [{"id": "skip_intro", "x": x, "y": y, "w": w, "h": h}]


NEXT_COUNTDOWN = 10.0  # secondes avant de lancer l'épisode suivant


def _next_card(ev: list[str], card: dict, state: dict, W: int, H: int) -> list[dict]:
    """« Épisode suivant » en bas à droite pendant le générique : le lancer tout de suite, ou rester."""
    hover = state.get("hover")
    w, h = 560, 176
    x = W - 72 - w
    y = (H - 128 - 40 - h) if state.get("controls") else (H - 64 - h)
    _soft_box(ev, x, y, w, h, 18, "10")
    ev.append(_text(x + 28, y + 34, "ÉPISODE SUIVANT", 18, col=DIM, shadow=False))
    ev.append(_text(x + 28, y + 70, _esc(card.get("title") or "")[:34], 28, shadow=False))
    # bouton principal : se remplit pendant le compte à rebours
    bx, by, bw, bh = x + 24, y + 106, 300, 50
    hot = bool(hover and bx <= hover[0] <= bx + bw and by <= hover[1] <= by + bh)
    ev.append(_shape(_rr(bx, by, bw, bh, 12), "&HC8C8C8&" if not hot else "&HDCDCDC&"))
    frac = max(0.0, min(1.0, 1 - float(card.get("left") or 0) / NEXT_COUNTDOWN))
    if card.get("paused"):
        frac = 1.0
    if frac > 0:
        ev.append(_shape(_rr(bx, by, max(24, bw * frac), bh, 12), WHITE))
    ev.append(_shape(_poly([(bx + 26, by + 15), (bx + 44, by + 25), (bx + 26, by + 35)]), "&H16120F&"))
    left = max(0, int(-(-float(card.get("left") or 0) // 1)))
    label = "Lecture" if card.get("paused") else f"Lecture dans {left} s"
    ev.append(_text(bx + 58, by + 25, label, 22, col="&H16120F&", shadow=False))
    sx, sw = bx + bw + 12, w - 24 - bw - 12 - 24
    hot2 = bool(hover and sx <= hover[0] <= sx + sw and by <= hover[1] <= by + bh)
    ev.append(_shape(_rr(sx, by, sw, bh, 12), WHITE, "C8" if hot2 else "E4"))
    ev.append(_text(sx + sw / 2, by + 25, "Rester", 22, an=5, shadow=False))
    return [{"id": "next_now", "x": bx, "y": by, "w": bw, "h": bh}, {"id": "next_stay", "x": sx, "y": by, "w": sw, "h": bh},
            {"id": "card_area", "x": x, "y": y, "w": w, "h": h}]


TAIL_GUESS_MAX = 360.0  # place habituelle du générique de fin, au plus (secondes avant la fin)

# Le son, mesuré au passage sans le modifier : son volume (ebur128) et sa « couleur » (aspectralstats, fenêtres de 0,17 s).
AUDIO_PROBE = ("@vad:lavfi=[ebur128=metadata=1,"
               "aspectralstats=win_size=8192:overlap=0.75:measure=centroid+spread+rolloff+flatness+entropy]")


# Mesure de la luminosité de chaque scène, lissée. Réglages par défaut de mpv : moyenne sur 20 images (< 1 s), « changement
# de scène » dès 1 à 3 dB d'écart (le niveau saute alors d'un coup) et pic pris sur le pixel le plus clair (une lampe,
# un reflet suffisent à tout faire bouger). D'où, de temps en temps en plein film, une image qui s'éclaircit ou
# s'assombrit brusquement « comme si elle se recalibrait ». Ici : adaptation douce sur ~3 s, seuls les vrais
# changements extrêmes (noir complet → plein soleil) recalent vite, et les petits points très clairs sont ignorés.
HDR_PEAK_SMOOTHING = {"hdr_peak_decay_rate": "80", "hdr_scene_threshold_low": "8", "hdr_scene_threshold_high": "16",
                      "hdr_peak_percentile": "99.995"}


def video_options(s: dict) -> dict:
    """Image fluide et rendu HDR (réglages de l'appli → options de mpv)."""
    if s.get("smooth", True):
        # images calées sur la fréquence de l'écran (240 Hz) et fondues entre elles : plus de saccades dans les
        # panoramiques ni sur le foot en 25 / 50 i/s (sans « effet feuilleton » : aucune image n'est inventée)
        o = {"video_sync": "display-resample", "interpolation": "yes", "tscale": "oversample"}
    else:
        o = {"video_sync": "audio", "interpolation": "no"}
    if s.get("hdr", True):
        # 4K HDR : écran HDR (Windows en mode HDR) → vraie sortie HDR ; sinon conversion soignée vers ton écran
        # (luminosité de chaque scène mesurée, couleurs gardées, détails des zones claires rendus)
        o.update(target_colorspace_hint="auto", hdr_compute_peak="yes", hdr_contrast_recovery="0.30",
                 gamut_mapping_mode="perceptual", tone_mapping="auto", **HDR_PEAK_SMOOTHING)
    else:
        o.update(target_colorspace_hint="no", hdr_contrast_recovery="0", hdr_compute_peak="auto", **HDR_PEAK_SMOOTHING)
    # vraie interpolation (images intermédiaires calculées, voir interp.py) : la vidéo décodée doit revenir en mémoire
    vf = ""
    if s.get("interpolation"):
        from . import interp
        vf = interp.prepare() or ""
    o["vf"] = vf
    hw = s.get("hwdec", "auto-safe")
    o["hwdec"] = ("auto-copy" if hw != "no" else "no") if vf else hw
    return o


# ------------------------------------------------------------------ lecteur
class Player:
    def __init__(self, app_title: str, settings: Callable[[], dict]) -> None:
        self.app_title = app_title
        self.settings = settings
        self.host: Host | None = None
        self.mpv = None
        self.lock = threading.RLock()
        self.item: dict | None = None
        self.url: str | None = None
        self.rect = {"x": 0, "y": 0, "w": 0, "h": 0, "dpr": 1.0, "visible": False, "radius": 16}
        self.osd_until = 0.0
        self.message: tuple[str, float] | None = None
        self.hits: list[dict] = []
        self.retries = 0
        self.stopped_by_user = False
        self.on_action: Callable[[str, dict], None] = lambda a, d: None  # zapping, plein écran… gérés par l'API
        self._osd_thread: threading.Thread | None = None
        self._progress_cb: Callable[[dict, float, float], None] | None = None
        # zapping instantané : un 2e lecteur, caché et muet, ouvre d'avance la chaîne suivante ; zapper dessus échange
        # les deux lecteurs (l'image est déjà là). Seulement si l'abonnement permet une connexion de plus (api.py).
        self._spare: dict | None = None
        self.fullscreen = False
        self.last_pos = 0.0  # dernière position connue (pour reprendre au même endroit après une coupure)
        self._retry_pending = False  # une relance attend déjà (évite d'en lancer deux pour la même coupure)
        self._restart_at = 0.0  # dernier redémarrage de l'image (playback-restart)
        self.menu: str | None = None  # liste ouverte au-dessus des boutons : "aid" (audio), "sid" (sous-titres), "opt"
        self._nx: dict | None = None  # carte « Épisode suivant » de l'épisode en cours
        self.intro_mem = None  # audio.IntroMemory (réglé par l'API)
        self.account_id: Callable[[], str] = lambda: ""
        self._aud: dict | None = None  # mesures du son de ce qui joue
        self._audio_thread = threading.Thread(target=self._audio_loop, name="player-audio", daemon=True)
        self._audio_thread.start()
        self.hover: tuple[float, float] | None = None  # souris sur la vidéo, en coordonnées du bandeau (1920 × 1080)

    # ---------------------------------------------------------------- création
    def _ensure(self):
        if self.mpv is not None:
            return self.mpv
        self.host = Host(self.app_title)
        self.host.on_close = lambda: self.on_action("exit_fullscreen", {})
        self.mpv = self._create(self.host)
        self._osd_thread = threading.Thread(target=self._osd_loop, name="player-osd", daemon=True)
        self._osd_thread.start()
        threading.Thread(target=self._input_loop, name="player-input", daemon=True).start()
        self._apply_rect()
        log.info("Player ready (libmpv in %s, %s)", mpv_dir(), "child window" if self.host.child else "own window")
        return self.mpv

    def _create(self, host: "Host", muted: bool = False):
        """Un lecteur mpv qui dessine dans ``host``. Ses évènements ne comptent que tant qu'il est le lecteur affiché."""
        mpv = _mpv()
        s = self.settings()
        fonts = Path(__file__).resolve().parent / "fonts"
        # load_scripts / ytdl : pas de scripts Lua intégrés (inutiles ici, et ytdl_hook lancerait yt-dlp sur les flux)
        opts = dict(wid=str(int(host.hwnd)), hwdec=s.get("hwdec", "auto-safe"), vo="gpu-next", keep_open="yes",
                    load_scripts="no", ytdl="no", config="no", terminal="no", input_media_keys="no",
                    idle="yes", force_window="yes", osc="no", osd_level="0", input_default_bindings="no",
                    input_vo_keyboard="yes", cursor_autohide="1500", cache="yes",
                    demuxer_max_bytes=f"{int(s.get('buffer_mb', 150))}MiB",
                    demuxer_readahead_secs=str(int(s.get('buffer_s', 10))), network_timeout="15",
                    stream_lavf_o="reconnect=1,reconnect_streamed=1,reconnect_delay_max=5,reconnect_on_network_error=1",
                    sub_auto="fuzzy", screenshot_directory=str(Path.home() / "Pictures" / "se7en IPTV Player"),
                    screenshot_format="png", volume=str(int(s.get("volume", 100))), volume_max="150",
                    mute="yes" if muted else "no",
                    deinterlace="auto", sub_scale=str(float(s.get("sub_scale") or 1)),
                    alang=s.get("audio_lang", "fr,fre,fra,en,eng"), slang=s.get("sub_lang", "fr,fre,fra"),
                    subs_with_matching_audio="forced")
        opts.update(video_options(s))
        # volume du son 10 fois par seconde (générique d'ouverture, recalage des sous-titres) ; le son n'est pas modifié
        opts["af"] = AUDIO_PROBE
        if fonts.is_dir():
            opts.update(osd_fonts_dir=str(fonts), sub_fonts_dir=str(fonts))
        if os.environ.get("SE7EN_MPV_LOG"):  # diagnostic : journal complet de mpv dans ce fichier
            opts["log_file"] = os.environ["SE7EN_MPV_LOG"]
        m = mpv.MPV(log_handler=self._mpv_log, loglevel="warn", **opts)
        self._bind_inputs(m)
        m.observe_property("eof-reached", lambda _n, v: v and m is self.mpv and self._ended())
        m.event_callback("end-file")(lambda ev: m is self.mpv and self._end_file(ev))
        # l'image repart : la coupure est passée, le compteur d'essais repart de zéro (sinon 6 coupures en une soirée
        # suffiraient à abandonner pour de bon) ; s'il recoupe aussitôt (< 20 s), on garde le compte (pas de boucle sans fin)
        m.event_callback("playback-restart")(lambda _ev: m is self.mpv and self._restarted())
        return m

    # ---------------------------------------------------------------- zapping instantané
    def preload(self, url: str, item: dict, user_agent: str = "") -> None:
        """Ouvre ``item`` d'avance, caché et muet, pour que zapper dessus soit immédiat."""
        with self.lock:
            if self.mpv is None or (self._spare and self._spare.get("id") == item.get("id")):
                return
            if self._spare is None:
                host = Host(self.app_title)
                host.on_close = lambda: self.on_action("exit_fullscreen", {})
                if not host.child:  # sans fenêtre d'appli (tests, navigateur) : pas de 2e lecteur
                    return
                self._spare = {"host": host, "mpv": self._create(host, muted=True)}
            sp = self._spare
            sp.update(id=item.get("id"), url=url, since=time.monotonic())
            m = sp["mpv"]
            m.user_agent = user_agent or ""
            m.mute = True
            m.loadfile(url, "replace")
            m.pause = False
            log.info("Preload %s %s", item.get("kind"), redact(url))

    def preloaded(self) -> str | None:
        return (self._spare or {}).get("id")

    def drop_preload(self) -> bool:
        """Ferme la chaîne ouverte d'avance (sa connexion est libérée). True s'il y en avait une."""
        with self.lock:
            sp = self._spare
            if not sp or not sp.get("id"):
                return False
            sp["id"] = sp["url"] = None
            try:
                sp["mpv"].command("stop")
            except Exception:  # noqa: BLE001
                pass
            return True

    def _swap(self, url: str, item: dict) -> None:
        """La chaîne demandée est déjà ouverte en coulisse : on l'affiche, l'ancienne devient le lecteur de réserve."""
        sp = self._spare
        old_m, old_h = self.mpv, self.host
        vol, mute = old_m.volume, old_m.mute
        self.mpv, self.host = sp["mpv"], sp["host"]
        self.item, self.url, self.retries, self.stopped_by_user = _with_display(item), url, 0, False
        self.mpv.volume, self.mpv.mute = vol, mute
        if self.fullscreen:  # en plein écran, la nouvelle vidéo prend l'écran et l'ancienne retourne dans la page
            self.host.set_fullscreen(True)
            old_h.set_fullscreen(False)
        self._apply_rect()  # la nouvelle vidéo passe devant…
        old_h.place(0, 0, 1, 1, False)  # … l'ancienne disparaît
        try:
            old_m.command("stop")
            old_m.mute = True
        except Exception:  # noqa: BLE001
            pass
        self._spare = {"host": old_h, "mpv": old_m, "id": None, "url": None}
        log.info("Zap from preload in %.0f ms after preloading", (time.monotonic() - sp.get("since", time.monotonic())) * 1000)

    def _mpv_log(self, level, prefix, text) -> None:
        if level in ("error", "fatal"):
            log.warning("mpv %s: %s", prefix, redact(text.strip()))
            m = re.search(r"HTTP error (\d{3})", text)
            if m:
                self.last_http = (int(m.group(1)), time.monotonic())

    def _bind_inputs(self, m) -> None:
        """Souris et clavier sont lus par _input_loop (la fenêtre interne de mpv les avalait en plein écran) ; mpv ne
        garde que la molette pour le volume."""
        def on(key: str, fn: Callable[[], None]) -> None:
            m.register_key_binding(key, lambda state, name, char: state[0] in "dp" and fn())

        on("WHEEL_UP", lambda: self.volume(delta=5))
        on("WHEEL_DOWN", lambda: self.volume(delta=-5))

    # ---------------------------------------------------------------- souris et clavier
    KEYS = {0x1B: "esc", 0x46: "f", 0x20: "space", 0x25: "left", 0x27: "right", 0x26: "up", 0x28: "down", 0x4D: "m",
            0x08: "back", 0x49: "i", 0x21: "up", 0x22: "down", **{0x30 + d: str(d) for d in range(10)},
            **{0x60 + d: str(d) for d in range(10)}}

    def _video_rect(self) -> tuple[int, int, int, int] | None:
        h = self.host.hwnd if self.host else None
        if not h or not _u32.IsWindowVisible(h):
            return None
        r = wintypes.RECT()
        _u32.GetWindowRect(h, ctypes.byref(r))
        return (r.left, r.top, r.right - r.left, r.bottom - r.top) if r.right - r.left > 20 else None

    def _input_loop(self) -> None:
        """Toutes les 25 ms : où est la souris sur la vidéo (bandeau, bulle survolée), clics et double-clic ; en plein
        écran aussi le clavier (Échap / F, espace, flèches, chiffres…)."""
        pt = wintypes.POINT()
        last_pt = (0, 0)
        down: set[int] = set()
        last_click = (0.0, 0.0, 0.0)  # moment, x, y du dernier clic sur l'image (pour le double-clic)
        drag_vol = None  # réglette du volume attrapée (bouton gauche appuyé dessus)
        while True:
            time.sleep(0.025)
            try:
                if self.mpv is None or self.host is None or not self.item:
                    self.hover = None
                    continue
                rect = self._video_rect()
                fg = _u32.GetAncestor(_u32.GetForegroundWindow(), 2)  # GA_ROOT
                ours = fg and (fg == self.host.hwnd or fg == self.host.parent)
                _u32.GetCursorPos(ctypes.byref(pt))
                inside = bool(rect and ours and rect[0] <= pt.x < rect[0] + rect[2] and rect[1] <= pt.y < rect[1] + rect[3])
                if inside:
                    hx, hy = (pt.x - rect[0]) * 1920 / rect[2], (pt.y - rect[1]) * 1080 / rect[3]
                    if (pt.x, pt.y) != last_pt:
                        self.show_osd(3)
                        self.hover = (hx, hy)
                    last_pt = (pt.x, pt.y)
                else:
                    self.hover = None
                # clic gauche : à l'image près, au relâchement
                pressed = bool(_u32.GetAsyncKeyState(0x01) & 0x8000)
                if pressed and 0x01 not in down:
                    down.add(0x01)
                    vol = next((h for h in self.hits if h["id"] == "vol"), None) if inside else None
                    dragging = bool(vol and vol["x"] - 6 <= hx <= vol["x"] + vol["w"] + 6 and vol["y"] <= hy <= vol["y"] + vol["h"])
                    drag_vol = vol if dragging else None
                elif pressed and drag_vol and inside:  # on glisse sur la réglette du volume
                    self.volume(value=round(max(0.0, min(1.0, (hx - drag_vol["x"]) / drag_vol["w"])) * 100))
                elif not pressed and 0x01 in down:
                    down.discard(0x01)
                    if drag_vol:
                        drag_vol = None
                    elif inside:
                        what = self._click_at(hx, hy)
                        now = time.monotonic()
                        if what == "video":
                            if now - last_click[0] < 0.35 and abs(hx - last_click[1]) < 40 and abs(hy - last_click[2]) < 40:
                                self.on_action("fullscreen", {})
                                last_click = (0.0, 0.0, 0.0)
                            else:
                                last_click = (now, hx, hy)
                # clavier : en plein écran seulement (dans la page, c'est l'interface qui a le clavier)
                if not (self.fullscreen and fg == self.host.hwnd):
                    down &= {0x01}
                    continue
                for vk, key in self.KEYS.items():
                    p = bool(_u32.GetAsyncKeyState(vk) & 0x8000)
                    if p and vk not in down:
                        down.add(vk)
                        self._key(key)
                    elif not p:
                        down.discard(vk)
            except Exception as e:  # noqa: BLE001 - le lecteur a pu être fermé
                log.debug("input: %s", e)

    def _key(self, key: str) -> None:
        if key in ("esc", "f"):
            if self.menu:
                self.menu = None
            else:
                self.on_action("exit_fullscreen", {})
        elif key == "space":
            self.toggle_pause()
        elif key in ("up", "down"):
            if self.item and self.item.get("kind") == "live":
                self.on_action("zap", {"delta": -1 if key == "up" else 1})
            else:
                self.volume(delta=5 if key == "up" else -5)
        elif key in ("left", "right"):
            self.seek(-10 if key == "left" else 10)
        elif key == "m":
            self.toggle_mute()
        elif key == "back":
            self.on_action("previous_channel", {})
        elif key == "i":
            self.show_osd(6)
        elif key.isdigit():
            self.on_action("digit", {"digit": key})

    # ---------------------------------------------------------------- lecture
    def play(self, url: str, item: dict, start: float = 0.0, user_agent: str = "", langs: dict | None = None) -> None:
        """``langs`` : langues choisies la dernière fois par ce profil ({"aid": "eng", "sid": "fre" ou "no"})."""
        with self.lock:
            m = self._ensure()
            sp = self._spare
            if sp and sp.get("id") is not None and sp.get("id") == item.get("id") and sp.get("url") == url:
                self._swap(url, item)
                self.show_osd(5)
                bus.publish("player", {"event": "play", "item": self.item})
                return
            self.item = dict(item)
            self.url = url
            self.last_pos = float(start or 0)
            self.retries = 0
            self.stopped_by_user = False
            m.user_agent = user_agent or ""
            # une piste choisie à la main (aid=3) resterait pour le fichier suivant, quelle que soit sa langue (et sans
            # son s'il en a moins) : chaque nouveau film repart du choix par langue
            m.aid = "auto"
            m.sid = "auto"
            opts = {}
            if start > 0:
                opts["start"] = str(int(start))
            s = self.settings()
            if (langs or {}).get("aid"):
                opts["alang"] = f"{langs['aid']},{s.get('audio_lang', 'fr,fre,fra,en,eng')}"
            sid = (langs or {}).get("sid")
            if sid == "no":  # sous-titres coupés la dernière fois : seulement les forcés
                opts.update(slang="none", subs_fallback="no")  # (une liste vide est refusée par loadfile)
            elif sid:  # choisis exprès, même dans la langue du son
                opts.update(slang=f"{sid},{s.get('sub_lang', 'fr,fre,fra')}", subs_with_matching_audio="yes")
            log.info("Play %s %s", item.get("kind"), redact(url))
            # valeurs écrites « %longueur%valeur » : python-mpv joint les options par des virgules, et une liste de
            # langues (« eng,fr,fre ») serait coupée -> loadfile refusé, le film ne démarrait pas
            m.loadfile(url, "replace", **{k: f"%{len(str(v).encode())}%{v}" for k, v in opts.items()})
            m.pause = False
            self.show_osd(5)
            bus.publish("player", {"event": "play", "item": self.item})

    def set_fullscreen(self, on: bool) -> None:
        self.fullscreen = on
        if self.host is not None:
            self.host.set_fullscreen(on)
        self.show_osd(4 if on else 2)

    def stop(self) -> None:
        if self.fullscreen:  # plus rien à regarder : retour dans l'appli
            self.on_action("exit_fullscreen", {})
        self.drop_preload()
        with self.lock:
            self.stopped_by_user = True
            if self.mpv is not None:
                self._save_progress()
                self.mpv.command("stop")
            self.item = None
            self.url = None
            bus.publish("player", {"event": "stop"})

    def toggle_pause(self) -> None:
        if self.mpv is not None and self.item:
            self.mpv.pause = not self.mpv.pause
            self.show_osd(4)
            bus.publish("player", {"event": "state"})

    def _duration(self) -> float:
        """Durée du film : celle du fichier, sinon estimée d'après la position et le pourcentage lu (flux sans en-tête)."""
        m = self.mpv
        if m is None:
            return 0.0
        try:
            d = float(m.duration or 0)
            if d > 0:
                return d
            pct, pos = float(m.percent_pos or 0), float(m.time_pos or 0)
        except Exception:  # noqa: BLE001
            return 0.0
        return pos * 100 / pct if pct > 0.2 and pos > 10 else 0.0

    def seek(self, seconds: float, absolute: bool = False) -> None:
        if self.mpv is None or not self.item or self.item.get("kind") == "live":
            return
        try:
            # « exact » : sinon, sur un flux réseau, mpv s'arrête sur l'image-clé d'avant (jusqu'à 10 s plus tôt que
            # l'endroit cliqué ; m.seek() de python-mpv demande « default-precise », qui fait ça)
            if absolute and not self.mpv.duration and self._duration() > 0:  # durée estimée : on vise un pourcentage
                self.mpv.command("seek", max(0.0, min(100.0, seconds / self._duration() * 100)), "absolute-percent+exact")
            else:
                self.mpv.command("seek", seconds, "absolute+exact" if absolute else "relative+exact")
        except Exception:  # noqa: BLE001 - flux non navigable
            return
        self.show_osd(3)

    def volume(self, value: float | None = None, delta: float = 0) -> None:
        if self.mpv is None:
            return
        v = float(self.mpv.volume or 100) + delta if value is None else value
        self.mpv.volume = max(0, min(150, v))
        if self.mpv.mute and v > 0:
            self.mpv.mute = False
        self.show_osd(2.5)  # la réglette du bandeau montre le niveau (plus de gros « Volume 80 % » au milieu)

    def toggle_mute(self) -> None:
        if self.mpv is not None:
            self.mpv.mute = not self.mpv.mute
            self.flash("Son coupé" if self.mpv.mute else "Son rétabli")

    def set(self, prop: str, value: Any) -> None:
        if self.mpv is None:
            return
        allowed = {"aid", "sid", "speed", "video-aspect-override", "panscan", "video-unscaled", "keepaspect", "sub-delay", "sub-scale",
                   "audio-delay", "af", "hwdec", "volume", "mute", "ontop"}
        if prop not in allowed:
            raise MediaLabError("invalid", "Réglage du lecteur inconnu.")
        if prop == "sub-delay" and self._aud:
            self._aud["manual"] = True
        setattr(self.mpv, prop.replace("-", "_"), value)

    def add_subtitle(self, path: str) -> None:
        if self.mpv is not None:
            self.mpv.command("sub-add", path, "select")

    def add_external_sub(self, item_id: str, path: str, title: str, lang: str, select: bool) -> bool:
        """Ajoute un sous-titre trouvé en ligne comme une piste normale, si ``item_id`` est toujours ce qui joue."""
        with self.lock:
            if self.mpv is None or not self.item or self.item.get("id") != item_id:
                return False
            self.mpv.command("sub-add", path, "select" if select else "auto", title, lang)
            return True

    def still(self, width: int = 480) -> bytes | None:
        """L'image en cours, petite, en JPEG : l'interface la montre floutée quand une fenêtre recouvre la vidéo (la
        vraie vidéo est une fenêtre à part que la page ne peut pas recouvrir)."""
        with self.lock:
            if self.mpv is None or not self.item:
                return None
            try:
                img = self.mpv.screenshot_raw("video")
            except Exception:  # noqa: BLE001 - pas encore d'image, flux coupé…
                return None
        if img is None or not img.width:
            return None
        import io
        img = img.convert("RGB")
        if img.width > width:
            img = img.resize((width, max(1, round(img.height * width / img.width))))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=70)
        return out.getvalue()

    def screenshot(self) -> str | None:
        if self.mpv is None or not self.item:
            return None
        Path(self.mpv.screenshot_directory).mkdir(parents=True, exist_ok=True)
        self.mpv.command("screenshot")
        self.flash("Capture enregistrée dans Images › se7en IPTV Player")
        return str(self.mpv.screenshot_directory)

    # ---------------------------------------------------------------- position dans l'interface
    def place(self, x: float, y: float, w: float, h: float, dpr: float = 1.0, visible: bool = True, radius: int = 16) -> None:
        self.rect = {"x": x, "y": y, "w": w, "h": h, "dpr": dpr or 1.0, "visible": visible, "radius": radius}
        self._apply_rect()

    def _apply_rect(self) -> None:
        if self.host is None:
            return
        r = self.rect
        d = r["dpr"]
        self.host.place(int(round(r["x"] * d)), int(round(r["y"] * d)), int(round(r["w"] * d)), int(round(r["h"] * d)),
                        bool(r["visible"]), int(r.get("radius", 0) * d))

    # ---------------------------------------------------------------- OSD
    def show_osd(self, seconds: float = 3.0) -> None:
        self.osd_until = max(self.osd_until, time.monotonic() + seconds)

    def flash(self, text: str, seconds: float = 1.6) -> None:
        self.message = (text, time.monotonic() + seconds)
        self.show_osd(seconds)

    def update_item(self, **values: Any) -> None:
        if self.item is not None:
            self.item.update(values)

    def _click_at(self, x: float, y: float) -> str:
        """Clic aux coordonnées du bandeau (1920 × 1080). Renvoie « video » si ce n'était sur aucun bouton."""
        self.show_osd()
        for h in list(self.hits):
            if not (h["x"] <= x <= h["x"] + h["w"] and h["y"] <= y <= h["y"] + h["h"]):
                continue
            hid = h["id"]
            if hid.startswith("track:"):
                _t, prop, val = hid.split(":", 2)
                try:
                    self.set(prop, "no" if val == "no" else int(val))
                    self.on_action("track", {"kind": prop, "id": "no" if val == "no" else int(val)})
                except Exception:  # noqa: BLE001
                    pass
                self.menu = None
            elif hid in ("menu_area", "card_area"):
                pass
            elif hid.startswith(("subdelay:", "subsize:", "speed:", "aspect:")):
                self._menu_setting(hid)
            elif hid == "skip_intro":
                self.skip_intro()
            elif hid in ("next_now", "next_stay"):
                self._next_choice(hid == "next_now")
            elif hid in ("audio", "subs", "opts"):
                want = {"audio": "aid", "subs": "sid", "opts": "opt"}[hid]
                self.menu = None if self.menu == want else want
            else:
                self.menu = None
                if hid == "pause":
                    self.toggle_pause()
                elif hid == "mute":
                    self.toggle_mute()
                elif hid in ("back", "fwd"):
                    self.seek(-10 if hid == "back" else 10)
                elif hid == "vol":
                    self.volume(value=round(max(0.0, min(1.0, (x - h["x"]) / h["w"])) * 100))
                elif hid == "seek" and self._duration() > 0:
                    self.seek(max(0.0, min(1.0, (x - h["x"]) / h["w"])) * self._duration(), absolute=True)
                elif hid == "exit_fs":
                    self.on_action("exit_fullscreen", {})
                elif hid == "fs":
                    self.on_action("fullscreen", {})
                elif hid in ("zap_up", "zap_down"):
                    self.on_action("zap", {"delta": -1 if hid == "zap_up" else 1})
            return hid
        if self.menu:
            self.menu = None
            return "menu"
        self.on_action("click", {})
        return "video"

    def _menu_setting(self, hid: str) -> None:
        """Pastilles et boutons − / + des menus du plein écran."""
        what, val = hid.split(":", 1)
        m = self.mpv
        if m is None:
            return
        try:
            if what == "subdelay":
                if self._aud:
                    self._aud["manual"] = True
                d = 0.0 if val == "0" else round(float(m.sub_delay or 0) + (0.5 if val == "+" else -0.5), 1)
                m.sub_delay = d
            elif what == "subsize":
                v = 1.0 if val == "0" else round(max(0.5, min(2.5, float(m.sub_scale or 1) + (0.1 if val == "+" else -0.1))), 2)
                m.sub_scale = v
                self.on_action("sub_scale", {"value": v})  # gardée pour les prochains films
            elif what == "speed":
                m.speed = float(val)
            elif what == "aspect":
                if val == "zoom":
                    m.video_aspect_override, m.panscan = "-1", 1.0
                else:
                    m.video_aspect_override, m.panscan = val, 0.0
        except Exception as e:  # noqa: BLE001
            log.debug("menu: %s", e)

    def apply_video_settings(self, s: dict) -> None:
        """Image fluide / HDR / taille des sous-titres changés dans les réglages : appliqués tout de suite."""
        for m in (self.mpv, (self._spare or {}).get("mpv")):
            if m is None:
                continue
            for k, v in {**video_options(s), "sub_scale": str(float(s.get("sub_scale") or 1))}.items():
                try:
                    m[k.replace("_", "-")] = v
                except Exception as e:  # noqa: BLE001
                    log.debug("option %s: %s", k, e)

    # ---------------------------------------------------------------- épisode suivant
    def _credits_start(self, dur: float) -> float:
        """Début du générique de fin : reconnu au son, sinon un chapitre « Générique / Credits », sinon la place qu'il
        occupe d'habitude dans cette série, sinon les dernières secondes."""
        it = self.item or {}
        a = self._aud
        if a and a.get("id") == it.get("id") and a.get("outro"):
            return float(a["outro"])
        if "credits_at" not in it:
            at = None
            try:
                for ch in self.mpv.chapter_list or []:
                    if re.search(r"cr[ée]dit|g[ée]n[ée]rique|end ?credits|outro|ending", str(ch.get("title") or ""), re.I) \
                            and float(ch.get("time") or 0) > dur * 0.6:
                        at = float(ch["time"])
                        break
            except Exception:  # noqa: BLE001
                pass
            it["credits_at"] = at
        if it["credits_at"]:
            return it["credits_at"]
        tpl = a.get("otpl") if a and a.get("id") == it.get("id") else None
        if tpl and 20 <= tpl[2] <= min(TAIL_GUESS_MAX, dur * 0.25):
            return dur - tpl[2]
        return dur - max(25.0, min(90.0, dur * 0.03))

    def _next_state(self, st: dict) -> dict | None:
        """La carte « Épisode suivant » à afficher (ou rien), et le lancement quand le compte à rebours est fini."""
        it = self.item or {}
        ne = it.get("next_ep")
        dur, pos = float(st.get("duration") or 0), float(st.get("position") or 0)
        if it.get("kind") != "episode" or not ne or dur < 300 or not self.settings().get("auto_next", True):
            return None
        nx = self._nx if self._nx and self._nx.get("id") == it.get("id") else None
        if pos < self._credits_start(dur):
            if nx:  # retour en arrière avant le générique : la carte reviendra
                self._nx = None
            return None
        now = time.monotonic()
        if nx is None:
            nx = self._nx = {"id": it.get("id"), "left": NEXT_COUNTDOWN, "t": now, "stay": False, "fired": False}
        if nx["stay"] or nx["fired"]:
            return None
        paused = bool(st.get("paused"))
        if not paused:
            nx["left"] -= now - nx["t"]
        nx["t"] = now
        if nx["left"] <= 0:
            self._next_choice(True)
            return None
        title = nice_titles(ne.get("label") or ne.get("title") or "", it.get("subtitle") or "")["title"]
        return {"title": title, "left": nx["left"], "paused": paused}

    def _next_choice(self, go: bool) -> None:
        nx = self._nx
        if nx is None or nx.get("fired"):
            return
        if not go:
            nx["stay"] = True
            return
        nx["fired"] = True
        ne = dict((self.item or {}).get("next_ep") or {})
        ne["series"] = (self.item or {}).get("series")
        threading.Thread(target=self.on_action, args=("next_episode", ne), name="next-episode", daemon=True).start()

    # ---------------------------------------------------------------- le son : intro, sous-titres
    def _audio_loop(self) -> None:
        """Le son 10 fois par seconde (filtre @vad) : volume et empreinte, rangés par instant du film."""
        n = 0
        while True:
            time.sleep(0.1)
            m, it = self.mpv, self.item
            a = self._aud
            if a is not None and (not it or a.get("id") != it.get("id")):
                self._learn_tail(a)  # épisode quitté (suivant, arrêt) : ce qu'on a entendu de sa fin
            if m is None or not it or it.get("kind") not in ("movie", "episode"):
                continue
            try:
                if m.pause:
                    continue
                md = m._get_property("af-metadata/vad")
                t = m.audio_pts
                val = float((md or {}).get("lavfi.r128.M"))
            except Exception:  # noqa: BLE001 — pas encore de son, filtre absent
                continue
            if t is None or t < 0:
                continue
            a = self._aud
            if a is None or a.get("id") != it.get("id"):
                a = self._aud = {"id": it.get("id"), "samples": {}, "spec": {}, "intro": None, "tpl": None,
                                 "learned": False, "outro": None, "otpl": None, "tail_done": False, "dur": 0.0,
                                 "series": it.get("series"), "kind": it.get("kind"), "acc": self.account_id(),
                                 "synced": {}, "manual": False, "skipped": False}
            k = int(round((float(t) - 0.1) * audio.RATE))
            a["samples"][k] = val
            f = audio.feature(md)
            if f is not None:
                a["spec"][k] = f
            n += 1
            if n % 10 == 0:
                try:
                    self._audio_tick(a, float(t))
                except Exception as e:  # noqa: BLE001
                    log.debug("audio: %s", e)

    def _audio_tick(self, a: dict, t: float) -> None:
        """Toutes les secondes : reconnaître les génériques, apprendre ceux de la série, recaler les sous-titres."""
        it = self.item or {}
        acc, series = a["acc"], a["series"]
        mem = self.intro_mem
        if a["kind"] == "episode" and series and mem is not None:
            try:
                a["dur"] = float(self.mpv.duration or 0) or a["dur"]
            except Exception:  # noqa: BLE001
                pass
            dur = a["dur"]
            if a["tpl"] is None:
                a["tpl"] = mem.template(acc, series, "head") or False
            if a["otpl"] is None:
                a["otpl"] = mem.template(acc, series, "tail") or False
            R = audio.RATE
            k = int(t * R)
            # générique d'ouverture : reconnu dans les 40 dernières secondes entendues
            # recalculé chaque seconde tant que son début est dans les 40 s entendues : plus on en a entendu, plus la fin
            # est sûre (une estimation qui saute ailleurs, sur un refrain, est ignorée)
            if a["tpl"] and not a["skipped"] and t < audio.HEAD + 60:
                tpl, idur, _at = a["tpl"]
                prev = a["intro"]
                if prev is None or t < prev - idur + 30:
                    k0 = max(0, k - 40 * R)
                    start = audio.locate_start(audio.features(a["spec"], k0, k + 1), tpl)
                    end = None if start is None else k0 / R + start + idur  # fin du générique (instant du film)
                    if end is not None and end - t > 4 and (prev is None or abs(end - prev) <= 4):
                        a["intro"] = end
            if not a["learned"] and t >= audio.HEAD:
                a["learned"] = True
                if audio.measured(a["spec"], 0, audio.HEAD * R) > 0.35:  # vu depuis le début : on peut s'en servir
                    head = audio.features(a["spec"], 0, audio.HEAD * R)
                    threading.Thread(target=self._learned, args=(a, "head", head, 0.0, dur),
                                     name="intro-learn", daemon=True).start()
            # générique de fin : reconnu pendant qu'il passe
            if dur > 300 and t > dur - audio.TAIL:
                if a["otpl"] and a["outro"] is None:
                    k0 = max(int((dur - audio.TAIL) * R), k - 40 * R)
                    start = audio.locate_start(audio.features(a["spec"], k0, k + 1), a["otpl"][0])
                    if start is not None:
                        a["outro"] = k0 / R + start
                        log.info("End credits recognised at %.0f s", a["outro"])
                if t > dur - 3:
                    self._learn_tail(a)
            if a["intro"] and it.get("id") == a["id"] and not a.get("intro_logged"):
                a["intro_logged"] = True
                log.info("Opening credits recognised, end at %.0f s", a["intro"])
        if self.settings().get("auto_sync", True) and not a["manual"] and int(t) % 20 == 0:
            self._resync(a)

    def _learned(self, a: dict, part: str, data, origin: float, dur: float) -> None:
        try:
            got = self.intro_mem.learn(a["acc"], a["series"], str(a["id"]), part, data, origin, dur)
        except Exception as e:  # noqa: BLE001
            log.debug("intro learn: %s", e)
            return
        if got:
            log.info("%s credits learnt: %.0f s long (%s)", "Opening" if part == "head" else "End", got[1], a["series"])

    def _learn_tail(self, a: dict) -> None:
        """La fin de l'épisode qu'on a entendue (au moins 2 minutes des 7 dernières) sert à trouver le générique de fin."""
        if a.get("tail_done") or a.get("kind") != "episode" or not a.get("series") or self.intro_mem is None:
            return
        a["tail_done"] = True
        dur = float(a.get("dur") or 0)
        if dur < 600:
            return
        R = audio.RATE
        k0 = int((dur - audio.TAIL) * R)
        last = max((k for k in a["spec"] if k >= k0), default=None)
        if last is None or last - k0 < 120 * R or audio.measured(a["spec"], k0, last) < 0.6:
            return
        tail = audio.features(a["spec"], k0)
        threading.Thread(target=self._learned, args=(a, "tail", tail, dur - audio.TAIL, dur),
                         name="outro-learn", daemon=True).start()

    def _resync(self, a: dict) -> None:
        m = self.mpv
        if m is None:
            return
        tr = next((x for x in (m.track_list or []) if x.get("type") == "sub" and x.get("selected") and x.get("external")), None)
        path = (tr or {}).get("external-filename")
        if not path:
            return
        done = a["synced"].get(tr["id"])
        n = len(a["samples"])
        if done and (done["n"] * 2 > n or done.get("final")):  # recalculé quand on a deux fois plus de son
            return
        keys = np.fromiter(a["samples"].keys(), dtype=np.int64)
        vals = np.fromiter(a["samples"].values(), dtype=np.float32)
        size = int(keys.max()) + 1
        env = np.full(size, audio.FLOOR, dtype=np.float32)
        known = np.zeros(size, dtype=bool)
        ok = keys >= 0
        env[keys[ok]] = np.maximum(audio.FLOOR, vals[ok])
        known[keys[ok]] = True
        res = audio.best_offset(env, known, audio.cues(path))
        a["synced"][tr["id"]] = {"n": n, "final": n > 40 * 60 * audio.RATE}
        if not res:
            return
        cur_d, cur_s = float(m.sub_delay or 0), float(m.sub_speed or 1)
        if abs(res["delay"] - cur_d) < 0.3 and abs(res["speed"] - cur_s) < 0.001:
            return
        m.sub_speed = res["speed"]
        m.sub_delay = res["delay"]
        log.info("Subtitles resynced: delay %.1f s, speed %.4f (score %.2f)", res["delay"], res["speed"], res["score"])
        self.flash(f"Sous-titres recalés automatiquement ({res['delay']:+.1f} s)".replace(".", ","), 3)

    def _intro_visible(self, st: dict) -> bool:
        """Le bouton « Passer l'intro » : générique reconnu par le son, ou chapitre « Intro / Générique »."""
        a = self._aud
        pos = float(st.get("position") or 0)
        if a and a.get("id") == (self.item or {}).get("id") and a.get("intro") and not a.get("skipped") and pos < a["intro"] - 2:
            return True
        end = self._intro_chapter(pos)
        return end is not None and not (a or {}).get("skipped") and pos < end - 2

    def _intro_chapter(self, pos: float) -> float | None:
        it = self.item or {}
        if "intro_ch" not in it:
            found = None
            try:
                chs = self.mpv.chapter_list or []
                for k, ch in enumerate(chs):
                    if re.search(r"\b(intro|opening|ouverture|g[ée]n[ée]rique d.?ouverture|opening credits)\b", str(ch.get("title") or ""), re.I) \
                            and k + 1 < len(chs) and float(ch.get("time") or 0) < 600:
                        found = (float(ch.get("time") or 0), float(chs[k + 1]["time"]))
                        break
            except Exception:  # noqa: BLE001
                pass
            it["intro_ch"] = found
        ch = it.get("intro_ch")
        return ch[1] if ch and ch[0] <= pos < ch[1] else None

    def skip_intro(self) -> bool:
        a = self._aud
        pos = float(getattr(self.mpv, "time_pos", 0) or 0) if self.mpv else 0.0
        end = (a or {}).get("intro") or self._intro_chapter(pos)
        if not end:
            return False
        if a:
            a["skipped"] = True
        self.seek(float(end), absolute=True)
        return True

    def _osd_loop(self) -> None:
        last = None
        last_m = None
        tick = 0
        saved = time.monotonic()
        while True:
            time.sleep(0.05 if self.hover or self.menu else 0.25)  # réactif quand la souris est sur la vidéo
            tick += 1
            m = self.mpv
            if m is None:
                continue
            if m is not last_m:  # zapping instantané : l'autre lecteur est passé devant
                last, last_m = None, m
            try:
                st = self.snapshot(light=not self.menu)
                card = self._next_state(st) if self.item else None
                intro = self._intro_visible(st) if self.item and not card else False
                visible = bool(self.item) and (time.monotonic() < self.osd_until or st.get("paused") or st.get("buffering")
                                               or self.menu is not None or card is not None or intro)
                if self.message and time.monotonic() > self.message[1]:
                    self.message = None
                if visible:
                    data, self.hits = osd({**(self.item or {}), **st, "controls": self.fullscreen, "fullscreen": self.fullscreen,
                                           "hover": self.hover, "menu": self.menu, "next_card": card, "skip_intro": intro,
                                           "message": self.message[0] if self.message else None})
                else:
                    data, self.hits = "", []
                if data != last:
                    m.command("osd-overlay", 1, "ass-events", data or " ", 1920, 1080, 0, "no", "no")
                    last = data
                if self.item and time.monotonic() - saved > 10:  # toutes les 10 s : position de reprise
                    saved = time.monotonic()
                    self._save_progress()
            except Exception as e:  # noqa: BLE001 - le lecteur a pu être fermé
                log.debug("osd: %s", e)

    # ---------------------------------------------------------------- état
    def snapshot(self, light: bool = False) -> dict:
        m = self.mpv
        if m is None or not self.item:
            return {"playing": False}

        def g(name: str, default: Any = None) -> Any:
            # propriété de lecture (m.time_pos) : m["time-pos"] lit un *réglage* de mpv, vide pour la position, les
            # pistes, le tampon… (c'est pour ça que le minuteur restait à 0:00 et les sous-titres « aucun »)
            try:
                v = getattr(m, name.replace("-", "_"))
                return default if v is None else v
            except Exception:  # noqa: BLE001
                return default
        st = {"playing": True, "paused": bool(g("pause", False)), "position": g("time-pos", 0), "duration": self._duration(),
              "buffering": bool(g("paused-for-cache", False)), "cached": (g("time-pos", 0) or 0) + (g("demuxer-cache-duration", 0) or 0),
              "volume": g("volume", 100), "muted": bool(g("mute", False))}
        if st["position"]:
            self.last_pos = float(st["position"])
        if light:
            return st
        vp = g("video-params", {}) or {}
        st.update({"item": self.item, "width": vp.get("w"), "height": vp.get("h"), "fps": g("estimated-vf-fps"),
                   "video_codec": g("video-codec"), "audio_codec": g("audio-codec-name"), "hwdec": g("hwdec-current"),
                   "video_bitrate": g("video-bitrate"), "audio_bitrate": g("audio-bitrate"),
                   "dropped": (g("frame-drop-count", 0) or 0) + (g("decoder-frame-drop-count", 0) or 0),
                   "cache_s": g("demuxer-cache-duration", 0), "speed": g("speed", 1),
                   "aspect": g("video-aspect-override", "-1"), "panscan": g("panscan", 0),
                   "sub_delay": g("sub-delay", 0), "sub_scale": g("sub-scale", 1), "tracks": [
                       {"id": t.get("id"), "type": t.get("type"), "lang": t.get("lang"), "title": t.get("title"),
                        "codec": t.get("codec"), "selected": bool(t.get("selected")), "external": bool(t.get("external")),
                        "forced": bool(t.get("forced")), "default": bool(t.get("default")),
                        "hearing_impaired": bool(t.get("hearing-impaired")), "channels": t.get("demux-channel-count") or 0}
                       for t in (g("track-list", []) or []) if t.get("type") in ("audio", "sub")]})
        for kind in ("audio", "sub"):
            same = [t for t in st["tracks"] if t["type"] == kind]
            for t, lab in zip(same, track_labels(same)):
                t["label"] = lab
        return st

    def on_progress(self, cb: Callable[[dict, float, float], None]) -> None:
        self._progress_cb = cb

    def _save_progress(self) -> None:
        if not self.item or self.item.get("kind") not in ("movie", "episode") or self._progress_cb is None or self.mpv is None:
            return
        try:
            pos, dur = float(self.mpv.time_pos or 0), self._duration()
        except Exception:  # noqa: BLE001
            return
        if dur > 0:
            self._progress_cb(self.item, pos, dur)

    # ---------------------------------------------------------------- fin, erreurs, reconnexion
    def _ended(self) -> None:
        self._save_progress()
        kind = (self.item or {}).get("kind")
        if kind in ("movie", "episode"):
            bus.publish("player", {"event": "ended", "item": self.item})
        elif kind == "live" and not self.stopped_by_user and self.url:
            # keep_open=yes : une chaîne qui coupe n'émet que eof-reached (pas end-file) et resterait figée
            self._retry("eof")
        elif kind == "replay" and not self.stopped_by_user and self.url:
            # replay coupé avant la fin du programme : on reprend où on en était (fin normale : rien à faire)
            it = self.item or {}
            length = float(it.get("stop") or 0) - float(it.get("start") or 0)
            if length <= 0 or self.last_pos < length - 30:
                self._retry("eof")

    def _end_file(self, ev) -> None:
        try:
            reason = ev.data.reason
        except Exception:  # noqa: BLE001
            reason = None
        if isinstance(reason, int):
            name = {0: "eof", 1: "restarted", 2: "aborted", 3: "quit", 4: "error", 5: "redirect"}.get(reason, "")
        else:
            name = getattr(reason, "name", str(reason or "")).lower()
        if self.stopped_by_user or not self.item or not self.url:
            return
        if "error" in name or (name == "eof" and self.item.get("kind") == "live"):
            self._retry(name)

    def _restarted(self) -> None:
        self._restart_at = time.monotonic()

    def _retry(self, name: str) -> None:
        """Relance le flux après une coupure (6 essais, de plus en plus espacés)."""
        if self.stopped_by_user or not self.item or not self.url:
            return
        if self._retry_pending:  # eof-reached et end-file pour la même coupure : une seule relance
            return
        if self._restart_at and time.monotonic() - self._restart_at > 20:
            self.retries = 0  # la lecture avait bien repris depuis la dernière coupure
        self._restart_at = 0.0
        if self.retries < 6:
            self.retries += 1
            delay = min(8, 1.5 * self.retries)
            code, when = getattr(self, "last_http", (0, 0.0))
            if code and time.monotonic() - when < 30:
                why = ("Le serveur refuse la connexion (abonnement déjà utilisé ailleurs ?)" if code in (401, 403, 429, 458, 509)
                       else f"Le serveur ne répond pas ({code})")
            else:
                why = "Le flux s'est coupé"
            self.flash(f"{why} · nouvel essai ({self.retries}/6)", delay + 1)
            log.warning("Stream ended (%s), retry %d in %.1fs: %s", name, self.retries, delay, redact(self.url))

            at = self.last_pos if self.item.get("kind") in ("movie", "episode", "replay") else 0
            self._retry_pending = True

            def retry(url=self.url, item_id=(self.item or {}).get("id")):
                try:
                    time.sleep(delay)
                    if self.url == url and (self.item or {}).get("id") == item_id and not self.stopped_by_user:
                        self.mpv.loadfile(url, "replace", **({"start": f"{at:.1f}"} if at > 5 else {}))
                except Exception:  # noqa: BLE001
                    log.exception("Retry failed")
                finally:
                    self._retry_pending = False
            threading.Thread(target=retry, daemon=True).start()
        else:
            self.flash("Le flux ne répond plus.", 8)
            bus.publish("player", {"event": "error", "message": "Le flux ne répond plus : le serveur est peut-être saturé."})

    def shutdown(self) -> None:
        try:
            self._save_progress()
            for m in (self.mpv, (self._spare or {}).get("mpv")):
                if m is not None:
                    try:
                        m.command("stop")  # arrêter la lecture avant de fermer le moteur
                    except Exception:  # noqa: BLE001
                        pass
                    m.terminate()
        except Exception:  # noqa: BLE001
            pass
