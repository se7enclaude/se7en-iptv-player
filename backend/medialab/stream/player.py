"""Le lecteur : libmpv dessine dans une fenêtre enfant posée sur la zone vidéo de l'interface.

Rien de l'interface web ne passe par-dessus la vidéo : les contrôles sur l'image (bandeau de la chaîne, barre de
lecture) sont dessinés par mpv lui-même (``osd-overlay`` en ASS), avec une vraie transparence, en fenêtré comme en
plein écran. Souris et clavier sur la vidéo arrivent par les raccourcis de mpv et déclenchent les mêmes actions que
l'interface. Un flux en direct qui coupe est relancé tout seul.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

from ..config import app_root
from ..errors import MediaLabError
from ..events import bus
from ..logging_setup import get_logger
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


def osd(state: dict, W: int = 1920, H: int = 1080) -> tuple[str, list[dict]]:
    """Bandeau (chaîne / titre, programme en cours et suivant) + barre de lecture. Renvoie l'ASS et les zones cliquables."""
    ev: list[str] = []
    hits: list[dict] = []
    glass = r"\bord0\shad0\1c&H4A3B27&\1a&H48&"
    edge = r"\bord2\shad0\3c&HFFFFFF&\3a&HA8&\1a&HFF&"
    # ---- bandeau du haut
    title = _esc(state.get("title") or "")
    sub = _esc(state.get("subtitle") or "")
    live = state.get("kind") == "live"
    tx = 56
    if state.get("number"):
        ev.append(rf"{{\an7\pos(0,0){glass}\p1}}{_rr(48, 40, 110, 96, 30)}")
        ev.append(rf"{{\an5\pos(103,88)\fnAnton\fs54\1c&HFFFFFF&\bord0\shad0}}{state['number']}")
        tx = 178
    ev.append(rf"{{\an7\pos({tx},34)\fnAnton\fs64\1c&HFFFFFF&\bord0\shad2\4c&H000000&\4a&H70&}}{title}")
    y2 = 112
    if live:
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&H9AA1E9&\1a&H10&\p1}}{_rr(tx, y2, 140, 40, 20)}")
        ev.append(rf"{{\an5\pos({tx + 70},{y2 + 20})\fnAnton\fs26\1c&HFFFFFF&\bord0\shad0}}EN DIRECT")
        sx = tx + 156
    else:
        sx = tx
    if sub:
        ev.append(rf"{{\an4\pos({sx},{y2 + 20})\fnManrope\b1\fs30\1c&HFFFFFF&\bord0\shad1\4a&H80&}}{sub}")
    now = state.get("now")
    if now and now.get("start") and now.get("stop"):
        frac = max(0.0, min(1.0, (time.time() - now["start"]) / max(1, now["stop"] - now["start"])))
        bw = 560
        ev.append(rf"{{\an7\pos(0,0){glass}\p1}}{_rr(W - bw - 56, 40, bw, 120, 30)}")
        ev.append(rf"{{\an7\pos({W - bw - 30},54)\fnManrope\b1\fs28\1c&HFFFFFF&\bord0\shad0}}{_hm(now['start'])}  {_esc(now.get('title') or '')[:34]}")
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&HB0&\p1}}{_rr(W - bw - 30, 100, bw - 52, 10, 5)}")
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HB2B9F1&\1a&H00&\p1}}{_rr(W - bw - 30, 100, max(10, (bw - 52) * frac), 10, 5)}")
        nxt = state.get("next")
        if nxt and nxt.get("start"):
            ev.append(rf"{{\an7\pos({W - bw - 30},118)\fnManrope\fs24\1c&HE0E0E0&\bord0\shad0}}Ensuite {_hm(nxt['start'])}  {_esc(nxt.get('title') or '')[:34]}")
    # ---- barre du bas : des bulles en verre liquide, au milieu
    if state.get("controls"):
        hits += _bubbles(ev, state, W, H)
    # ---- messages (volume, erreur, reconnexion)
    msg = state.get("message")
    if msg:
        ev.append(rf"{{\an7\pos(0,0){glass}\p1}}{_rr(W / 2 - 360, H / 2 - 50, 720, 100, 50)}")
        ev.append(rf"{{\an5\pos({W // 2},{H // 2})\fnManrope\b1\fs34\1c&HFFFFFF&\bord0\shad0}}{_esc(msg)}")
    return "\n".join(ev), hits


# ------------------------------------------------------------------ bulles (verre liquide)
LANGS = {"fre": "Français", "fra": "Français", "fr": "Français", "eng": "Anglais", "en": "Anglais", "ara": "Arabe",
         "ar": "Arabe", "spa": "Espagnol", "es": "Espagnol", "ger": "Allemand", "deu": "Allemand", "de": "Allemand",
         "ita": "Italien", "it": "Italien", "por": "Portugais", "pt": "Portugais", "tur": "Turc", "tr": "Turc",
         "dut": "Néerlandais", "nld": "Néerlandais", "nl": "Néerlandais", "pol": "Polonais", "rus": "Russe", "jpn": "Japonais"}


def track_label(t: dict, n: int) -> str:
    name = LANGS.get((t.get("lang") or "").lower(), (t.get("lang") or "").upper())
    title = (t.get("title") or "").strip()
    if title and title.lower() not in name.lower():
        name = f"{name} · {title}" if name else title
    return _esc(name or f"Piste {n}")[:40]


def _circle(cx: float, cy: float, r: float) -> str:
    return _rr(cx - r, cy - r, 2 * r, 2 * r, r)


def _glass(ev: list[str], cx: float, cy: float, r: float, hot: bool = False, solid: bool = False) -> None:
    """Une bulle de verre : fond sombre translucide, voile blanc, reflet en haut et liseré clair."""
    if solid:
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HF4F2F0&\1a&H00&\p1}}{_circle(cx, cy, r)}")
        return
    ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&H120E0C&\1a&H{'50' if hot else '78'}&\p1}}{_circle(cx, cy, r)}")
    ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&H{'B8' if hot else 'D8'}&\p1}}{_circle(cx, cy, r)}")
    ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&HD0&\p1}}{_rr(cx - r * 0.62, cy - r * 0.86, r * 1.24, r * 0.62, r * 0.31)}")
    ev.append(rf"{{\an7\pos(0,0)\bord2\shad0\3c&HFFFFFF&\3a&H{'60' if hot else '98'}&\1a&HFF&\p1}}{_circle(cx, cy, r)}")


def _icon(ev: list[str], kind: str, cx: float, cy: float, dark: bool = False, state: dict | None = None) -> None:
    col = r"\1c&H100D0B&" if dark else r"\1c&HFFFFFF&"
    pre = rf"{{\an7\pos(0,0)\bord0\shad0{col}\1a&H00&\p1}}"

    def shape(d: str) -> None:
        ev.append(pre + d)

    def text(t: str, size: int = 26) -> None:
        ev.append(rf"{{\an5\pos({cx:.0f},{cy:.0f})\fnManrope\b1\fs{size}{col}\bord0\shad0}}{t}")

    x, y = cx, cy
    if kind == "play":
        shape(f"m {x - 9:.0f} {y - 15:.0f} l {x + 16:.0f} {y:.0f} l {x - 9:.0f} {y + 15:.0f}")
    elif kind == "pause":
        shape(_rr(x - 12, y - 14, 8, 28, 3))
        shape(_rr(x + 4, y - 14, 8, 28, 3))
    elif kind in ("back", "fwd"):
        text("−10" if kind == "back" else "+10", 24)
    elif kind == "subs":
        ev.append(rf"{{\an7\pos(0,0)\bord2\shad0\3c&HFFFFFF&\1a&HFF&\p1}}{_rr(x - 19, y - 13, 38, 26, 6)}")
        ev.append(rf"{{\an5\pos({x:.0f},{y + 1:.0f})\fnManrope\b1\fs17{col}\bord0\shad0}}CC")
    elif kind == "audio":
        for k, h in enumerate((10, 22, 30, 18, 8)):
            shape(_rr(x - 17 + k * 7.5, y - h / 2, 4.5, h, 2.2))
    elif kind in ("up", "down"):
        d = -1 if kind == "up" else 1
        shape(f"m {x - 13:.0f} {y - 6 * d:.0f} l {x + 13:.0f} {y - 6 * d:.0f} l {x:.0f} {y + 8 * d:.0f}")
    elif kind == "mute":
        shape(f"m {x - 16:.0f} {y - 6:.0f} l {x - 8:.0f} {y - 6:.0f} l {x + 2:.0f} {y - 15:.0f} l {x + 2:.0f} {y + 15:.0f} "
              f"l {x - 8:.0f} {y + 6:.0f} l {x - 16:.0f} {y + 6:.0f}")
        if (state or {}).get("muted"):
            ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&H5A5AF0&\1a&H00&\p1}}m {x + 6:.0f} {y - 9:.0f} l {x + 9:.0f} {y - 12:.0f} "
                      f"l {x + 19:.0f} {y - 2:.0f} l {x + 16:.0f} {y + 1:.0f}")
        else:
            shape(_rr(x + 7, y - 5, 4, 10, 2))
            shape(_rr(x + 14, y - 10, 4, 20, 2))
    elif kind in ("fs_on", "fs_off"):
        # quatre coins : vers l'extérieur pour entrer en plein écran, vers le centre pour en sortir
        out = kind == "fs_on"
        a, b, arm, t = (15, 12, 9, 4) if out else (6, 5, 9, 4)
        for sx in (-1, 1):
            for sy in (-1, 1):
                px, py = x + sx * a, y + sy * b
                dx, dy = (-sx, -sy) if out else (sx, sy)  # sens des branches depuis le coin
                shape(_rr(min(px, px + dx * arm) - t / 2, py - t / 2, arm + t, t, 1.5))
                shape(_rr(px - t / 2, min(py, py + dy * arm) - t / 2, t, arm + t, 1.5))

def _bubbles(ev: list[str], state: dict, W: int, H: int) -> list[dict]:
    """Bulles en bas au milieu : −10 s, lecture / pause, +10 s · langue audio, sous-titres · chaîne − / + (direct) ·
    son · plein écran. Le nom de la bulle survolée s'affiche au-dessus ; audio et sous-titres ouvrent une petite liste."""
    hits: list[dict] = []
    live = state.get("kind") == "live"
    paused = bool(state.get("paused"))
    full = bool(state.get("fullscreen"))
    hover = state.get("hover")
    vol = "Son coupé" if state.get("muted") else f"Son {int(state.get('volume') or 0)} %"
    groups = [
        [("back", "back", "Reculer de 10 s", 34)] if not live else [],
        [("pause", "play" if paused else "pause", "Lecture" if paused else "Pause", 44)],
        [("fwd", "fwd", "Avancer de 10 s", 34)] if not live else [],
        [("audio", "audio", "Langue audio", 34), ("subs", "subs", "Sous-titres", 34)],
        [("zap_up", "up", "Chaîne précédente", 34), ("zap_down", "down", "Chaîne suivante", 34)] if live else [],
        [("mute", "mute", vol, 34)],
        [("exit_fs" if full else "fs", "fs_off" if full else "fs_on", "Quitter le plein écran (Échap)" if full else "Plein écran (F)", 34)],
    ]
    groups = [g for g in groups if g]
    gap, sep = 16, 34
    width = sum(sum(2 * r for *_x, r in g) + gap * (len(g) - 1) for g in groups) + sep * (len(groups) - 1)
    cy = H - 96
    x = (W - width) / 2
    # la piste : progression (films, séries, replay) ou « En direct », juste au-dessus des bulles
    dur, pos = state.get("duration") or 0, state.get("position") or 0
    tw, ty = min(1100, max(width + 120, 760)), cy - 84
    tx = (W - tw) / 2
    if dur > 0 and not live:
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&HB8&\p1}}{_rr(tx, ty, tw, 8, 4)}")
        cache = state.get("cached") or 0
        if cache > pos:
            ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&H98&\p1}}{_rr(tx, ty, max(8, tw * min(1, cache / dur)), 8, 4)}")
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&H372EEE&\1a&H00&\p1}}{_rr(tx, ty, max(8, tw * min(1, pos / dur)), 8, 4)}")
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&H00&\p1}}{_circle(tx + tw * min(1, pos / dur), ty + 4, 9)}")
        ev.append(rf"{{\an6\pos({tx - 18:.0f},{ty + 4:.0f})\fnManrope\b1\fs24\1c&HFFFFFF&\bord0\shad1\4a&H80&}}{_clock(pos)}")
        ev.append(rf"{{\an4\pos({tx + tw + 18:.0f},{ty + 4:.0f})\fnManrope\b1\fs24\1c&HFFFFFF&\bord0\shad1\4a&H80&}}{_clock(dur)}")
        hits.append({"id": "seek", "x": tx, "y": ty - 16, "w": tw, "h": 40})
    else:
        txt = "Mise en mémoire…" if state.get("buffering") else f"En direct · {time.strftime('%H:%M')}"
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&H372EEE&\1a&H10&\p1}}{_rr(W / 2 - 110, ty - 16, 220, 38, 19)}")
        ev.append(rf"{{\an5\pos({W // 2},{ty + 3:.0f})\fnManrope\b1\fs22\1c&HFFFFFF&\bord0\shad0}}{txt}")
    label = None
    for g in groups:
        for hid, icon, name, r in g:
            cx = x + r
            hot = bool(hover and (hover[0] - cx) ** 2 + (hover[1] - cy) ** 2 <= r * r)
            primary = hid == "pause"
            _glass(ev, cx, cy, r, hot, solid=primary)
            _icon(ev, icon, cx, cy, dark=primary, state=state)
            hits.append({"id": hid, "x": cx - r, "y": cy - r, "w": 2 * r, "h": 2 * r})
            if hot:  # au-dessus de la piste (progression / « En direct »), pour ne rien cacher
                label = (cx, ty - 22, name)
            x += 2 * r + gap
        x += sep - gap
    # le nom de la bulle survolée, dans une petite pastille de verre
    if label and not state.get("menu"):
        lx, ly, name = label
        lw = 30 + 14 * len(name)
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&H120E0C&\1a&H40&\p1}}{_rr(lx - lw / 2, ly - 46, lw, 44, 22)}")
        ev.append(rf"{{\an5\pos({lx:.0f},{ly - 24:.0f})\fnManrope\b1\fs24\1c&HFFFFFF&\bord0\shad0}}{_esc(name)}")
    # liste des pistes (audio ou sous-titres) au-dessus de sa bulle
    menu = state.get("menu")
    if menu in ("aid", "sid"):
        kind = "audio" if menu == "aid" else "sub"
        tracks = [t for t in state.get("tracks") or [] if t.get("type") == kind]
        rows = ([{"id": "no", "label": "Désactivés", "selected": not any(t.get("selected") for t in tracks)}] if menu == "sid" else []) + [
            {"id": str(t.get("id")), "label": track_label(t, n + 1), "selected": bool(t.get("selected"))} for n, t in enumerate(tracks)]
        if not rows:
            rows = [{"id": "", "label": "Aucune piste", "selected": False}]
        anchor = next(h for h in hits if h["id"] == ("audio" if menu == "aid" else "subs"))
        mw, rh = 380, 56
        mh = 20 + rh * len(rows) + 44
        mx, my = anchor["x"] + anchor["w"] / 2 - mw / 2, anchor["y"] - 24 - mh
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&H120E0C&\1a&H30&\p1}}{_rr(mx, my, mw, mh, 26)}")
        ev.append(rf"{{\an7\pos(0,0)\bord2\shad0\3c&HFFFFFF&\3a&HA8&\1a&HFF&\p1}}{_rr(mx, my, mw, mh, 26)}")
        ev.append(rf"{{\an7\pos({mx + 24:.0f},{my + 16:.0f})\fnManrope\b1\fs22\1c&HB8B8B8&\bord0\shad0}}{'Langue audio' if menu == 'aid' else 'Sous-titres'}")
        for k, row in enumerate(rows):
            ry = my + 52 + k * rh
            hot = bool(hover and mx <= hover[0] <= mx + mw and ry <= hover[1] <= ry + rh)
            if row["selected"] or hot:
                ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&H{'D0' if not row['selected'] else 'B0'}&\p1}}{_rr(mx + 10, ry + 4, mw - 20, rh - 8, 20)}")
            ev.append(rf"{{\an4\pos({mx + 30:.0f},{ry + rh / 2:.0f})\fnManrope\b{1 if row['selected'] else 0}\fs26\1c&HFFFFFF&\bord0\shad0}}{row['label']}")
            if row["selected"]:
                ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&H372EEE&\1a&H00&\p1}}{_circle(mx + mw - 36, ry + rh / 2, 7)}")
            if row["id"]:
                hits.insert(0, {"id": f"track:{menu}:{row['id']}", "x": mx, "y": ry, "w": mw, "h": rh})
        # après les pistes (elles passent d'abord), avant les bulles : un clic dans la liste hors piste ne fait rien
        hits.insert(sum(1 for h in hits if h["id"].startswith("track:")), {"id": "menu_area", "x": mx, "y": my, "w": mw, "h": mh})
    return hits


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
        self.menu: str | None = None  # liste ouverte au-dessus des bulles : "aid" (audio) ou "sid" (sous-titres)
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
                    # fluidité : chaînes entrelacées (sport en 1080i) désentrelacées, images calées sur l'écran
                    deinterlace="auto", video_sync="display-resample", interpolation="yes", tscale="oversample",
                    alang=s.get("audio_lang", "fr,fre,fra,en,eng"), slang=s.get("sub_lang", "fr,fre,fra"))
        if fonts.is_dir():
            opts.update(osd_fonts_dir=str(fonts), sub_fonts_dir=str(fonts))
        if os.environ.get("SE7EN_MPV_LOG"):  # diagnostic : journal complet de mpv dans ce fichier
            opts["log_file"] = os.environ["SE7EN_MPV_LOG"]
        m = mpv.MPV(log_handler=self._mpv_log, loglevel="warn", **opts)
        self._bind_inputs(m)
        m.observe_property("eof-reached", lambda _n, v: v and m is self.mpv and self._ended())
        m.event_callback("end-file")(lambda ev: m is self.mpv and self._end_file(ev))
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
        self.item, self.url, self.retries, self.stopped_by_user = dict(item), url, 0, False
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
                elif not pressed and 0x01 in down:
                    down.discard(0x01)
                    if inside:
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
    def play(self, url: str, item: dict, start: float = 0.0, user_agent: str = "") -> None:
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
            self.retries = 0
            self.stopped_by_user = False
            m.user_agent = user_agent or ""
            opts = {}
            if start > 0:
                opts["start"] = str(int(start))
            log.info("Play %s %s", item.get("kind"), redact(url))
            m.loadfile(url, "replace", **opts)
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

    def seek(self, seconds: float, absolute: bool = False) -> None:
        if self.mpv is None or not self.item or self.item.get("kind") == "live":
            return
        try:
            self.mpv.seek(seconds, "absolute" if absolute else "relative")
        except Exception:  # noqa: BLE001 - flux non navigable
            return
        self.show_osd(3)

    def volume(self, value: float | None = None, delta: float = 0) -> None:
        if self.mpv is None:
            return
        v = float(self.mpv.volume or 100) + delta if value is None else value
        self.mpv.volume = max(0, min(150, v))
        self.flash(f"Volume {int(self.mpv.volume)} %")

    def toggle_mute(self) -> None:
        if self.mpv is not None:
            self.mpv.mute = not self.mpv.mute
            self.flash("Son coupé" if self.mpv.mute else "Son rétabli")

    def set(self, prop: str, value: Any) -> None:
        if self.mpv is None:
            return
        allowed = {"aid", "sid", "speed", "video-aspect-override", "panscan", "video-unscaled", "keepaspect", "sub-delay",
                   "audio-delay", "af", "hwdec", "volume", "mute", "ontop"}
        if prop not in allowed:
            raise MediaLabError("invalid", "Réglage du lecteur inconnu.")
        self.mpv[prop] = value

    def add_subtitle(self, path: str) -> None:
        if self.mpv is not None:
            self.mpv.command("sub-add", path, "select")

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
                except Exception:  # noqa: BLE001
                    pass
                self.menu = None
            elif hid == "menu_area":
                pass
            elif hid in ("audio", "subs"):
                want = "aid" if hid == "audio" else "sid"
                self.menu = None if self.menu == want else want
            else:
                self.menu = None
                if hid == "pause":
                    self.toggle_pause()
                elif hid == "mute":
                    self.toggle_mute()
                elif hid in ("back", "fwd"):
                    self.seek(-10 if hid == "back" else 10)
                elif hid == "seek" and self.mpv.duration:
                    self.seek(max(0.0, min(1.0, (x - h["x"]) / h["w"])) * self.mpv.duration, absolute=True)
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
                visible = bool(self.item) and (time.monotonic() < self.osd_until or st.get("paused") or st.get("buffering")
                                               or self.menu is not None)
                if self.message and time.monotonic() > self.message[1]:
                    self.message = None
                if visible:
                    data, self.hits = osd({**(self.item or {}), **st, "controls": True, "fullscreen": self.fullscreen,
                                           "hover": self.hover, "menu": self.menu,
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
            try:
                v = m[name]
                return default if v is None else v
            except Exception:  # noqa: BLE001
                return default
        st = {"playing": True, "paused": bool(g("pause", False)), "position": g("time-pos", 0), "duration": g("duration", 0),
              "buffering": bool(g("paused-for-cache", False)), "cached": (g("time-pos", 0) or 0) + (g("demuxer-cache-duration", 0) or 0),
              "volume": g("volume", 100), "muted": bool(g("mute", False))}
        if light:
            return st
        vp = g("video-params", {}) or {}
        st.update({"item": self.item, "width": vp.get("w"), "height": vp.get("h"), "fps": g("estimated-vf-fps"),
                   "video_codec": g("video-codec"), "audio_codec": g("audio-codec-name"), "hwdec": g("hwdec-current"),
                   "video_bitrate": g("video-bitrate"), "audio_bitrate": g("audio-bitrate"),
                   "dropped": (g("frame-drop-count", 0) or 0) + (g("decoder-frame-drop-count", 0) or 0),
                   "cache_s": g("demuxer-cache-duration", 0), "speed": g("speed", 1),
                   "aspect": g("video-aspect-override", "-1"), "tracks": [
                       {"id": t.get("id"), "type": t.get("type"), "lang": t.get("lang"), "title": t.get("title"),
                        "codec": t.get("codec"), "selected": bool(t.get("selected")), "external": bool(t.get("external"))}
                       for t in (g("track-list", []) or []) if t.get("type") in ("audio", "sub")]})
        return st

    def on_progress(self, cb: Callable[[dict, float, float], None]) -> None:
        self._progress_cb = cb

    def _save_progress(self) -> None:
        if not self.item or self.item.get("kind") not in ("movie", "episode") or self._progress_cb is None or self.mpv is None:
            return
        try:
            pos, dur = float(self.mpv.time_pos or 0), float(self.mpv.duration or 0)
        except Exception:  # noqa: BLE001
            return
        if dur > 0:
            self._progress_cb(self.item, pos, dur)

    # ---------------------------------------------------------------- fin, erreurs, reconnexion
    def _ended(self) -> None:
        self._save_progress()
        if self.item and self.item.get("kind") in ("movie", "episode"):
            bus.publish("player", {"event": "ended", "item": self.item})

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
            if self.retries < 6:
                self.retries += 1
                delay = min(8, 1.5 * self.retries)
                self.flash(f"Connexion perdue · nouvelle tentative ({self.retries}/6)", delay + 1)
                log.warning("Stream ended (%s), retry %d in %.1fs: %s", name, self.retries, delay, redact(self.url))

                def retry(url=self.url, item_id=(self.item or {}).get("id")):
                    time.sleep(delay)
                    if self.url == url and (self.item or {}).get("id") == item_id and not self.stopped_by_user:
                        self.mpv.loadfile(url, "replace")
                threading.Thread(target=retry, daemon=True).start()
            else:
                self.flash("Le flux ne répond plus.", 8)
                bus.publish("player", {"event": "error", "message": "Le flux ne répond plus : le serveur est peut-être saturé."})

    def shutdown(self) -> None:
        try:
            self._save_progress()
            if self.mpv is not None:
                self.mpv.terminate()
            if self._spare is not None:
                self._spare["mpv"].terminate()
        except Exception:  # noqa: BLE001
            pass
