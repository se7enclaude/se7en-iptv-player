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
    _u32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
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


class Host:
    """Fenêtre où mpv dessine : enfant de la fenêtre de l'appli, ou fenêtre à part (mode navigateur / développement)."""

    def __init__(self, parent_title: str) -> None:
        self.parent_title = parent_title
        self.hwnd = None
        self.parent = None
        self.child = False
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)
        threading.Thread(target=self._loop, name="player-host", daemon=True).start()
        self._ready.wait(5)

    def _wndproc(self, h, m, w, l):
        if m == 0x0021:  # WM_MOUSEACTIVATE : la vidéo ne vole pas le focus clavier à l'interface
            return MA_NOACTIVATE if self.child else _u32.DefWindowProcW(h, m, w, l)
        if m == WM_CLOSE and not self.child:
            _u32.ShowWindow(h, 0)
            bus.publish("player", {"event": "closed"})
            return 0
        return _u32.DefWindowProcW(h, m, w, l)

    def _loop(self) -> None:
        wc = _WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.hInstance = _k32.GetModuleHandleW(None)
        wc.lpszClassName = "se7enIPTVPlayerVideo"
        wc.hbrBackground = _g32.GetStockObject(4)  # noir
        wc.hCursor = _u32.LoadCursorW(None, 32512)
        _u32.RegisterClassW(ctypes.byref(wc))
        self.parent = _u32.FindWindowW(None, self.parent_title)
        if self.parent:
            self.child = True
            style = 0x40000000 | 0x04000000 | 0x02000000  # CHILD | CLIPSIBLINGS | CLIPCHILDREN (caché au départ)
            self.hwnd = _u32.CreateWindowExW(0, "se7enIPTVPlayerVideo", "video", style, 0, 0, 16, 9, self.parent, None,
                                             wc.hInstance, None)
        else:  # pas de fenêtre d'appli (navigateur, tests) : une fenêtre à part
            style = 0x00CF0000  # WS_OVERLAPPEDWINDOW
            self.hwnd = _u32.CreateWindowExW(0, "se7enIPTVPlayerVideo", "se7en IPTV Player", style, 120, 120, 960, 540, None, None,
                                             wc.hInstance, None)
        self._ready.set()
        msg = wintypes.MSG()
        while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _u32.TranslateMessage(ctypes.byref(msg))
            _u32.DispatchMessageW(ctypes.byref(msg))

    def place(self, x: int, y: int, w: int, h: int, visible: bool, radius: int = 0) -> None:
        if not self.hwnd:
            return
        if not self.child:
            _u32.ShowWindow(self.hwnd, 5 if visible else 0)
            return
        flags = 0x0010 | (0x0040 if visible and w > 1 and h > 1 else 0x0080)  # NOACTIVATE | SHOW / HIDE
        _u32.SetWindowPos(self.hwnd, wintypes.HWND(0), x, y, max(1, w), max(1, h), flags)
        if radius and visible:
            _u32.SetWindowRgn(self.hwnd, _g32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius * 2, radius * 2), True)
        elif visible:
            _u32.SetWindowRgn(self.hwnd, None, True)


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
    # ---- barre du bas (pause, progression, temps, volume)
    if state.get("controls"):
        bx, by, bw, bh = 48, H - 160, W - 96, 112
        ev.append(rf"{{\an7\pos(0,0){glass}\p1}}{_rr(bx, by, bw, bh, 56)}")
        ev.append(rf"{{\an7\pos(0,0){edge}\p1}}{_rr(bx, by, bw, bh, 56)}")
        paused = state.get("paused")
        ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&H00&\p1}}{_rr(bx + 24, by + 26, 160, 60, 30)}")
        ev.append(rf"{{\an5\pos({bx + 104},{by + 56})\fnManrope\b1\fs30\1c&H0E0D0B&\bord0\shad0}}{'Lecture' if paused else 'Pause'}")
        hits.append({"id": "pause", "x": bx + 24, "y": by + 26, "w": 160, "h": 60})
        dur, pos = state.get("duration") or 0, state.get("position") or 0
        px, pw = bx + 214, bw - 214 - 300
        if dur > 0 and not live:
            ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&HB0&\p1}}{_rr(px, by + 50, pw, 12, 6)}")
            cache = state.get("cached") or 0
            if cache > pos:
                ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HFFFFFF&\1a&H90&\p1}}{_rr(px, by + 50, max(12, pw * min(1, cache / dur)), 12, 6)}")
            ev.append(rf"{{\an7\pos(0,0)\bord0\shad0\1c&HB2B9F1&\1a&H00&\p1}}{_rr(px, by + 50, max(12, pw * min(1, pos / dur)), 12, 6)}")
            hits.append({"id": "seek", "x": px, "y": by + 30, "w": pw, "h": 52})
            label = f"{_clock(pos)} / {_clock(dur)}"
        else:
            ev.append(rf"{{\an4\pos({px},{by + 56})\fnManrope\b1\fs28\1c&HFFFFFF&\bord0\shad0}}{'Mise en mémoire…' if state.get('buffering') else 'En direct'}")
            label = time.strftime("%H:%M")
        ev.append(rf"{{\an6\pos({bx + bw - 140},{by + 56})\fnManrope\b1\fs30\1c&HFFFFFF&\bord0\shad0}}{label}")
        vol = "Muet" if state.get("muted") else f"{int(state.get('volume') or 0)} %"
        ev.append(rf"{{\an6\pos({bx + bw - 34},{by + 56})\fnManrope\fs26\1c&HDDDDDD&\bord0\shad0}}{vol}")
        hits.append({"id": "mute", "x": bx + bw - 130, "y": by + 26, "w": 110, "h": 60})
    # ---- messages (volume, erreur, reconnexion)
    msg = state.get("message")
    if msg:
        ev.append(rf"{{\an7\pos(0,0){glass}\p1}}{_rr(W / 2 - 360, H / 2 - 50, 720, 100, 50)}")
        ev.append(rf"{{\an5\pos({W // 2},{H // 2})\fnManrope\b1\fs34\1c&HFFFFFF&\bord0\shad0}}{_esc(msg)}")
    return "\n".join(ev), hits


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

    # ---------------------------------------------------------------- création
    def _ensure(self):
        if self.mpv is not None:
            return self.mpv
        mpv = _mpv()
        self.host = Host(self.app_title)
        s = self.settings()
        fonts = Path(__file__).resolve().parent / "fonts"
        # load_scripts / ytdl : pas de scripts Lua intégrés (inutiles ici, et ytdl_hook lancerait yt-dlp sur les flux)
        opts = dict(wid=str(int(self.host.hwnd)), hwdec=s.get("hwdec", "auto-safe"), vo="gpu-next", keep_open="yes",
                    load_scripts="no", ytdl="no", config="no", terminal="no", input_media_keys="no",
                    idle="yes", force_window="yes", osc="no", osd_level="0", input_default_bindings="no",
                    input_vo_keyboard="yes", cursor_autohide="1500", cache="yes",
                    demuxer_max_bytes=f"{int(s.get('buffer_mb', 150))}MiB",
                    demuxer_readahead_secs=str(int(s.get('buffer_s', 10))), network_timeout="15",
                    stream_lavf_o="reconnect=1,reconnect_streamed=1,reconnect_delay_max=5,reconnect_on_network_error=1",
                    sub_auto="fuzzy", screenshot_directory=str(Path.home() / "Pictures" / "se7en IPTV Player"),
                    screenshot_format="png", volume=str(int(s.get("volume", 100))), volume_max="150",
                    alang=s.get("audio_lang", "fr,fre,fra,en,eng"), slang=s.get("sub_lang", "fr,fre,fra"))
        if fonts.is_dir():
            opts.update(osd_fonts_dir=str(fonts), sub_fonts_dir=str(fonts))
        if os.environ.get("SE7EN_MPV_LOG"):  # diagnostic : journal complet de mpv dans ce fichier
            opts["log_file"] = os.environ["SE7EN_MPV_LOG"]
        self.mpv = mpv.MPV(log_handler=self._mpv_log, loglevel="warn", **opts)
        self._bind_inputs()
        self.mpv.observe_property("eof-reached", lambda _n, v: v and self._ended())
        self.mpv.event_callback("end-file")(self._end_file)
        self._osd_thread = threading.Thread(target=self._osd_loop, name="player-osd", daemon=True)
        self._osd_thread.start()
        self._apply_rect()
        log.info("Player ready (libmpv in %s, %s)", mpv_dir(), "child window" if self.host.child else "own window")
        return self.mpv

    def _mpv_log(self, level, prefix, text) -> None:
        if level in ("error", "fatal"):
            log.warning("mpv %s: %s", prefix, redact(text.strip()))

    def _bind_inputs(self) -> None:
        m = self.mpv

        def on(key: str, fn: Callable[[], None]) -> None:
            m.register_key_binding(key, lambda state, name, char: state[0] in "dp" and fn())

        on("MOUSE_MOVE", self.show_osd)
        on("MBTN_LEFT", self._click)
        on("MBTN_LEFT_DBL", lambda: self.on_action("fullscreen", {}))
        on("WHEEL_UP", lambda: self.volume(delta=5))
        on("WHEEL_DOWN", lambda: self.volume(delta=-5))
        on("SPACE", self.toggle_pause)
        on("f", lambda: self.on_action("fullscreen", {}))
        on("ESC", lambda: self.on_action("exit_fullscreen", {}))
        on("m", self.toggle_mute)
        on("UP", lambda: self.on_action("zap", {"delta": -1}))
        on("DOWN", lambda: self.on_action("zap", {"delta": 1}))
        on("PGUP", lambda: self.on_action("zap", {"delta": -1}))
        on("PGDWN", lambda: self.on_action("zap", {"delta": 1}))
        on("LEFT", lambda: self.seek(-10))
        on("RIGHT", lambda: self.seek(10))
        on("BS", lambda: self.on_action("previous_channel", {}))
        on("i", lambda: self.show_osd(6))
        for d in "0123456789":
            on(d, lambda d=d: self.on_action("digit", {"digit": d}))

    # ---------------------------------------------------------------- lecture
    def play(self, url: str, item: dict, start: float = 0.0, user_agent: str = "") -> None:
        with self.lock:
            m = self._ensure()
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

    def stop(self) -> None:
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

    def _click(self) -> None:
        self.show_osd()
        try:
            mx, my = self.mpv.mouse_pos["x"], self.mpv.mouse_pos["y"]
            ow, oh = self.mpv.osd_width or 1, self.mpv.osd_height or 1
        except Exception:  # noqa: BLE001
            return
        x, y = mx * 1920 / ow, my * 1080 / oh
        for h in self.hits:
            if h["x"] <= x <= h["x"] + h["w"] and h["y"] <= y <= h["y"] + h["h"]:
                if h["id"] == "pause":
                    self.toggle_pause()
                elif h["id"] == "mute":
                    self.toggle_mute()
                elif h["id"] == "seek" and self.mpv.duration:
                    self.seek((x - h["x"]) / h["w"] * self.mpv.duration, absolute=True)
                return
        self.on_action("click", {})

    def _osd_loop(self) -> None:
        last = None
        tick = 0
        while True:
            time.sleep(0.25)
            tick += 1
            m = self.mpv
            if m is None:
                continue
            try:
                st = self.snapshot(light=True)
                visible = bool(self.item) and (time.monotonic() < self.osd_until or st.get("paused") or st.get("buffering"))
                if self.message and time.monotonic() > self.message[1]:
                    self.message = None
                if visible:
                    data, self.hits = osd({**(self.item or {}), **st, "controls": True,
                                           "message": self.message[0] if self.message else None})
                else:
                    data, self.hits = "", []
                if data != last:
                    m.command("osd-overlay", 1, "ass-events", data or " ", 1920, 1080, 0, "no", "no")
                    last = data
                if tick % 40 == 0 and self.item:  # toutes les 10 s : position de reprise
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
        except Exception:  # noqa: BLE001
            pass
