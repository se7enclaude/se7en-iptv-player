"""OS integration: open files/folders, native dialogs, clipboard, taskbar flash."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from pathlib import Path

from .errors import MediaLabError
from .logging_setup import get_logger

log = get_logger("app")

# Set by the desktop shell when a pywebview window exists.
window = None
quit_requested = threading.Event()
_quit_callbacks: list = []


def on_quit(cb) -> None:
    _quit_callbacks.append(cb)


_quit_lock = threading.Lock()
_quit_done = False


def run_quit_callbacks(timeout: float | None = None) -> None:
    """Run the on_quit callbacks (recorder, player, remote, relay…) — once only, whatever closes se7en.

    With ``timeout``, they run in a background thread and we wait at most that long (closing never hangs)."""
    global _quit_done
    with _quit_lock:
        if _quit_done:
            return
        _quit_done = True

    def run() -> None:
        for cb in list(_quit_callbacks):
            try:
                cb()
            except Exception as e:  # noqa: BLE001
                log.debug("quit callback failed: %s", e)
    if timeout is None:
        run()
        return
    t = threading.Thread(target=run, name="quit-callbacks", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        log.warning("Quit callbacks still running after %.0fs; closing anyway", timeout)


def request_quit(delay: float = 0.0) -> None:
    """Close se7en (window or browser mode) — used after starting an update."""
    def go() -> None:
        quit_requested.set()
        run_quit_callbacks()
    threading.Timer(delay, go).start() if delay else go()


_job = None
_job_lock = threading.Lock()


def _kill_job():
    """A Windows Job Object that kills its processes when se7en ends (even on a crash or a forced close)."""
    global _job
    with _job_lock:
        if _job is not None or sys.platform != "win32":
            return _job or None
        try:
            from ctypes import wintypes

            class _Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _Io(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                           "OtherOperationCount", "ReadTransferCount",
                                                           "WriteTransferCount", "OtherTransferCount")]

            class _Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            h = k32.CreateJobObjectW(None, None)
            if not h:
                raise OSError(ctypes.get_last_error(), "CreateJobObject")
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):  # Extended limits
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
            _job = (k32, h)  # the handle stays open until se7en ends: then Windows closes it and kills the job
        except Exception as e:  # noqa: BLE001 - never blocking
            log.debug("Job object unavailable: %s", e)
            _job = False
        return _job or None


def kill_with_app(proc) -> None:
    """Make a child process (e.g. FFmpeg recording a channel) end with se7en, even if se7en is killed.

    Only the given process (and the ones it starts) joins the job: programs opened for the user (a file opened with
    its app, the installer of an update) must outlive se7en, so se7en itself is not put in the job."""
    job = _kill_job()
    if not job:
        return
    k32, h = job
    try:
        if not k32.AssignProcessToJobObject(h, int(proc._handle)):
            log.debug("AssignProcessToJobObject failed: %s", ctypes.get_last_error())
    except Exception as e:  # noqa: BLE001
        log.debug("kill_with_app failed: %s", e)


def open_path(path: str, reveal: bool = False) -> None:
    p = Path(path)
    if not p.exists():
        raise MediaLabError("file_missing", "This file or folder no longer exists.",
                            "It may have been moved or deleted.")
    if sys.platform == "win32":
        if reveal and p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(p))  # noqa: S606 — user-initiated
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R" if reveal else "", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent if reveal else p)])


def pick_folder(initial: str = "") -> str | None:
    if window is not None:
        import webview
        res = window.create_file_dialog(webview.FileDialog.FOLDER, directory=initial or "")
        return res[0] if res else None
    return _tk_dialog("folder", initial)


def _pattern(exts) -> str:
    return ";".join(f"*{e}" for e in sorted(exts))


def file_types(kind: str) -> tuple[str, ...]:
    from .media.ffmpeg import AUDIO_EXTS, IMAGE_EXTS, SUB_EXTS, VIDEO_EXTS
    groups = {
        "media": ("Images and videos", IMAGE_EXTS | VIDEO_EXTS),
        "video": ("Videos", VIDEO_EXTS),
        "audio": ("Audio", AUDIO_EXTS),
        "image": ("Images", IMAGE_EXTS),
        "av": ("Videos and audio", VIDEO_EXTS | AUDIO_EXTS),
        "any": ("Media files", IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | SUB_EXTS),
        "playable": ("Media files", IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS),
    }
    if kind in groups:
        label, exts = groups[kind]
        return (f"{label} ({_pattern(exts)})", "All files (*.*)")
    return {
        "cookies": ("Cookies (*.txt)", "All files (*.*)"),
        "exe": ("Programs (*.exe)", "All files (*.*)"),
        "m3u": ("Listes M3U (*.m3u;*.m3u8;*.txt)", "All files (*.*)"),
        "backup": ("Sauvegardes se7en IPTV Player (*.s7s)", "All files (*.*)"),
        "subs": ("Sous-titres (*.srt;*.ass;*.ssa;*.vtt;*.sub)", "All files (*.*)"),
    }.get(kind, ("All files (*.*)",))


def pick_files(kind: str = "media", initial: str = "", multiple: bool = True) -> list[str]:
    types = file_types(kind)
    multiple = multiple and kind not in ("cookies", "exe", "m3u", "backup", "subs")
    if window is not None:
        import webview
        try:  # pywebview only accepts "Words (*.ext;*.ext)" filters: never let a bad label kill the dialog
            from webview.util import parse_file_type
            for t in types:
                parse_file_type(t)
        except (ImportError, ValueError):
            types = ("All files (*.*)",)
        res = window.create_file_dialog(webview.FileDialog.OPEN, directory=initial or "",
                                        allow_multiple=multiple, file_types=types)
        return list(res or [])
    r = _tk_dialog("files", initial)
    r = list(r or [])
    return r if multiple else r[:1]


def _tk_dialog(mode: str, initial: str):
    """Fallback for browser dev mode (no pywebview window)."""
    result: list = []

    def run() -> None:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if mode == "folder":
            result.append(filedialog.askdirectory(initialdir=initial or None) or None)
        else:
            result.append(filedialog.askopenfilenames(initialdir=initial or None) or [])
        root.destroy()

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return result[0] if result else None


def clipboard_text() -> str:
    if sys.platform != "win32":
        return ""
    CF_UNICODETEXT = 13
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u32.OpenClipboard(None):
        return ""
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        ptr = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(ptr)[:20000] if ptr else ""
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def flash_taskbar(title: str = "se7en") -> None:
    """Flash the taskbar button when a task finishes while the app is in the background."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd and ctypes.windll.user32.GetForegroundWindow() != hwnd:
            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p), ("dwFlags", ctypes.c_uint),
                            ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 3, 0)  # ALL | TIMERNOFG
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception as e:
        log.debug("flash failed: %s", e)
