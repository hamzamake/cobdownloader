#!/usr/bin/env python3
"""
Cob Downloader - a friendly yt-dlp GUI.

Pick a file type, resolution and frame rate, decide whether you want the single
video you linked or the whole playlist, and hit Download.

Requires: yt-dlp  (pip install -U yt-dlp)  and ffmpeg on PATH.
"""

# single source of truth for the version - make-installer.bat reads this line
APP_VERSION = "1.0.0"

import importlib.abc
import importlib.machinery
import json
import os
import queue
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import zipfile
from datetime import date
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

FROZEN  = getattr(sys, "frozen", False)
MAC     = sys.platform == "darwin"
APP_DIR = Path(__file__).resolve().parent
# PyInstaller unpacks bundled data here; running from source it is just the
# folder the script lives in.  Inside a macOS .app it is Contents/Frameworks
BUNDLE  = Path(getattr(sys, "_MEIPASS", APP_DIR))
if MAC:
    APPDATA = Path.home() / "Library" / "Application Support" / "CobDownloader"
else:
    APPDATA = Path(os.environ.get("APPDATA") or Path.home()) / "CobDownloader"
# a frozen app may sit somewhere read-only (a mounted .dmg, or a translocated
# .app on macOS), so settings go to AppData / Application Support
SETTINGS_FILE = (APPDATA if FROZEN else APP_DIR) / "settings.json"
# self-updated copies of yt-dlp and ffmpeg live here, always somewhere
# writable, so the packaged build can update itself without being reinstalled
RUNTIME_DIR = APPDATA / "runtime"
RUNTIME_BIN = RUNTIME_DIR / "bin"


class _RuntimeFirst(importlib.abc.MetaPathFinder):
    """Resolves one top-level package from RUNTIME_DIR instead of the bundle.

    PyInstaller puts its own importer at the front of sys.meta_path, so a
    frozen yt_dlp would always win over anything on sys.path.  Sitting in
    front of it is the only way a packaged build can run an updated yt-dlp.
    Submodules need no special handling - once the package itself is loaded
    from RUNTIME_DIR its __path__ points there, so the rest follows.
    """

    def __init__(self, root, name):
        self.root, self.name = str(root), name

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.name:
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, [self.root])


def _prefer_runtime(name):
    """Put a downloaded copy of `name` ahead of the bundled one, if present."""
    if (RUNTIME_DIR / name / "__init__.py").exists():
        sys.meta_path.insert(0, _RuntimeFirst(RUNTIME_DIR, name))


_prefer_runtime("yt_dlp")

try:
    import yt_dlp
except ImportError:
    print("yt-dlp is not installed.  Run:  pip install -U yt-dlp")
    sys.exit(1)


# --------------------------------------------------------------------------- #
#  Theme
# --------------------------------------------------------------------------- #
BG         = "#0f1116"
CARD       = "#181b22"
CARD_HI    = "#1f232c"
INPUT      = "#22262f"
BORDER     = "#2b303b"
TEXT       = "#e8ebf1"
MUTED      = "#8b93a7"
ACCENT     = "#e5484d"
ACCENT_HI  = "#f05a5f"
GREEN      = "#3dd68c"
BLUE       = "#5b8cff"

if MAC:
    # Segoe UI does not exist there, and Tk on macOS draws one point as one
    # pixel (72 dpi) where Windows uses 96, so the same sizes come out a
    # quarter smaller.  Not scaled all the way up: a 13" MacBook has ~200 px
    # less height than a 1080p screen and this window is height-constrained
    FONT       = ("Helvetica Neue", 12)
    FONT_B     = ("Helvetica Neue", 12, "bold")
    FONT_S     = ("Helvetica Neue", 11)
    FONT_TITLE = ("Helvetica Neue", 23, "bold")
    FONT_BTN   = ("Helvetica Neue", 13, "bold")
    FONT_URL   = ("Helvetica Neue", 13)
    FONT_MONO  = ("Menlo", 10)
else:
    FONT       = ("Segoe UI", 10)
    FONT_B     = ("Segoe UI Semibold", 10)
    FONT_S     = ("Segoe UI", 9)
    FONT_TITLE = ("Segoe UI Semibold", 19)
    FONT_BTN   = ("Segoe UI Semibold", 11)
    FONT_URL   = ("Segoe UI", 11)
    FONT_MONO  = ("Consolas", 9)

VIDEO_TYPES = ["MP4", "MKV", "WEBM"]
AUDIO_TYPES = ["MP3", "M4A", "OPUS", "FLAC", "WAV"]
RESOLUTIONS = ["Best available", "4320p (8K)", "2160p (4K)", "1440p (2K)",
               "1080p (Full HD)", "720p (HD)", "480p", "360p", "240p", "144p"]
FPS_CHOICES = ["Any", "120", "60", "50", "30", "25", "24"]
AUDIO_QUALITY = ["Best", "320 kbps", "256 kbps", "192 kbps", "128 kbps", "96 kbps"]
# YouTube increasingly wants a logged-in session; borrowing cookies fixes most
# "HTTP Error 403" and "sign in to confirm you're not a bot" failures
BROWSERS = ["None", "Chrome", "Edge", "Firefox", "Brave", "Opera", "Vivaldi",
            "Chromium"] + (["Safari"] if MAC else [])


class Cancelled(yt_dlp.utils.DownloadCancelled):
    """Raised inside the progress hook to abort a download.

    Subclassing yt-dlp's own cancel error means it is not swallowed by
    ignoreerrors halfway through a playlist.
    """


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def human_size(n):
    if not n:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def human_time(s):
    if s is None:
        return "-"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


FFMPEG_EXE = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
FFPROBE_EXE = "ffprobe.exe" if os.name == "nt" else "ffprobe"


def ffmpeg_dir():
    """A self-updated ffmpeg first, then the bundled one, then PATH."""
    for folder in (RUNTIME_BIN, BUNDLE):
        if (folder / FFMPEG_EXE).exists():
            return str(folder)
    found = shutil.which("ffmpeg")
    return str(Path(found).parent) if found else None


QJS_EXE = "qjs.exe" if os.name == "nt" else "qjs"
# yt-dlp fetches its challenge solver script on demand; without it a runtime is
# present but cannot actually solve anything, which is worse than having no
# runtime at all - YouTube then hands back URLs that 403
REMOTE_COMPONENTS = ["ejs:github"]


def qjs_path():
    """The bundled QuickJS, else one the user happens to have."""
    for folder in (RUNTIME_BIN, BUNDLE, APP_DIR / "vendor"):
        if (folder / QJS_EXE).exists():
            return str(folder / QJS_EXE)
    return shutil.which("qjs")


def js_runtimes():
    """Every JS runtime yt-dlp can use here.  YouTube needs one.

    QuickJS ships with the app because it is 2 MB, where deno and node are
    80-110 MB; that is what lets a fresh machine work with nothing installed.
    Anything the user already has is offered too and yt-dlp picks between them.
    """
    found = {}
    qjs = qjs_path()
    if qjs:
        found["quickjs"] = {"path": qjs}
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            found[name] = {"path": None}
    return found


def ytdlp_age_days():
    """How stale the bundled yt-dlp is, or None if the version is unreadable.

    YouTube breaks old yt-dlp releases regularly - a stale copy still reads
    titles and formats fine but then fails the actual download with
    "HTTP Error 403: Forbidden", which looks like a bug in this app.
    """
    try:
        y, m, d = (int(part) for part in
                   yt_dlp.version.__version__.split(".")[:3])
        return (date.today() - date(y, m, d)).days
    except (ValueError, TypeError):
        return None


def strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


# --------------------------------------------------------------------------- #
#  Self update
#
#  yt-dlp goes stale fast and a stale copy fails downloads with a bare
#  "403 Forbidden", so it updates itself in the background.  ffmpeg barely
#  ever matters for whether a download works and its only source with a
#  working certificate ships ~170 MB zips, so that one waits to be asked.
# --------------------------------------------------------------------------- #
PYPI_YTDLP = "https://pypi.org/pypi/yt-dlp/json"
# gyan.dev would be the obvious ffmpeg source but its TLS certificate has
# expired; BtbN publishes static win64 builds with a usable release API
FFMPEG_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
USER_AGENT = "CobDownloader (+https://github.com/yt-dlp/yt-dlp)"

try:
    import certifi
except ImportError:
    certifi = None

_SSL_CTX = None
_SSL_SOURCE = "not built yet"


def ssl_context():
    """Verify against certifi's roots rather than the operating system's.

    A packaged build can land on a machine whose Windows trust store has not
    been filled in yet - a fresh VM or Windows Sandbox ships a seed set and
    pulls the rest on demand.  The store is then missing the roots pypi.org
    and github.com chain to, so every update check dies with
    CERTIFICATE_VERIFY_FAILED while a browser on the same machine is fine.
    certifi travels with the app, so it does not depend on the host at all.
    This is what yt-dlp does for its own requests.
    """
    global _SSL_CTX, _SSL_SOURCE
    if _SSL_CTX is None:
        try:
            bundle = certifi.where()
            _SSL_CTX = ssl.create_default_context(cafile=bundle)
            _SSL_SOURCE = f"certifi - {bundle}"
        except Exception as e:  # no certifi, or the bundle is not readable
            _SSL_CTX = ssl.create_default_context()
            _SSL_SOURCE = f"operating system trust store ({e})"
    return _SSL_CTX
CHECK_EVERY = 24 * 3600          # seconds; a launch inside this window skips
STAMP_FILE = RUNTIME_DIR / "update-check.json"


def version_tuple(text):
    """'2026.8.19' or 'n9.0-134-gabc' -> (2026, 8, 19) / (9, 0) for comparing."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def _open(url, timeout=30):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
        timeout=timeout, context=ssl_context())


def fetch_json(url, timeout=30):
    with _open(url, timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def download_file(url, dest, on_progress=None, cancel=None):
    """Stream a URL to `dest`, reporting 0-100 through `on_progress`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with _open(url, timeout=60) as r, open(tmp, "wb") as fh:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled("update cancelled")
                chunk = r.read(262144)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if on_progress:
                    on_progress(got * 100 / total if total else 0, got, total)
        tmp.replace(dest)
    except BaseException:
        # a half-written ffmpeg zip is ~160 MB of dead weight; do not keep it
        tmp.unlink(missing_ok=True)
        raise
    return dest


def ffmpeg_version():
    """The version string of whichever ffmpeg the app would actually use."""
    folder = ffmpeg_dir()
    if not folder:
        return None
    try:
        out = subprocess.run(
            [str(Path(folder) / FFMPEG_EXE), "-version"],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        m = re.search(r"ffmpeg version n?(\S+)", out.stdout)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def ytdlp_latest():
    """(version, wheel url) of the newest yt-dlp on PyPI."""
    data = fetch_json(PYPI_YTDLP)
    version = data["info"]["version"]
    wheel = next((u["url"] for u in data["urls"]
                  if u["packagetype"] == "bdist_wheel"), None)
    if not wheel:
        raise RuntimeError("PyPI listed no yt-dlp wheel")
    return version, wheel


def ffmpeg_latest():
    """(version, asset url, size) of the newest static win64 ffmpeg build."""
    if os.name != "nt":          # the BtbN builds this uses are Windows-only
        return None
    assets = fetch_json(FFMPEG_API).get("assets") or []
    best = None
    for a in assets:
        # the plain (non "-shared") lgpl build is one self-contained exe set
        m = re.fullmatch(r"ffmpeg-n(\d+\.\d+)-latest-win64-lgpl-[\d.]+\.zip",
                         a["name"])
        if m and (best is None or version_tuple(m.group(1)) > best[0]):
            best = (version_tuple(m.group(1)), m.group(1),
                    a["browser_download_url"], a["size"])
    return best[1:] if best else None


def install_ytdlp(wheel_url, on_progress=None, cancel=None):
    """Unpack a yt-dlp wheel into RUNTIME_DIR so the next launch picks it up."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    whl = RUNTIME_DIR / "yt-dlp.whl"
    staging = RUNTIME_DIR / "yt_dlp.new"
    try:
        download_file(wheel_url, whl, on_progress, cancel)
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        with zipfile.ZipFile(whl) as z:
            members = [n for n in z.namelist() if n.startswith("yt_dlp/")]
            if not any(n == "yt_dlp/__init__.py" for n in members):
                raise RuntimeError("wheel did not contain a yt_dlp package")
            z.extractall(staging, members)
        live, old = RUNTIME_DIR / "yt_dlp", RUNTIME_DIR / "yt_dlp.old"
        shutil.rmtree(old, ignore_errors=True)
        if live.exists():
            live.rename(old)
        (staging / "yt_dlp").rename(live)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        whl.unlink(missing_ok=True)


def install_ffmpeg(asset_url, on_progress=None, cancel=None):
    """Pull ffmpeg.exe and ffprobe.exe out of a BtbN zip into RUNTIME_BIN."""
    RUNTIME_BIN.mkdir(parents=True, exist_ok=True)
    zp = RUNTIME_DIR / "ffmpeg.zip"
    try:
        download_file(asset_url, zp, on_progress, cancel)
        wanted = {FFMPEG_EXE, FFPROBE_EXE}
        with zipfile.ZipFile(zp) as z:
            picked = {Path(n).name: n for n in z.namelist()
                      if Path(n).name in wanted and "/bin/" in n}
            missing = wanted - set(picked)
            if missing:
                raise RuntimeError(f"zip was missing {', '.join(sorted(missing))}")
            for name, member in picked.items():
                tmp = RUNTIME_BIN / (name + ".new")
                with z.open(member) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                tmp.replace(RUNTIME_BIN / name)
    finally:
        zp.unlink(missing_ok=True)


def ytdlp_source():
    """Whether the running yt-dlp is the self-updated copy or the shipped one."""
    try:
        if Path(yt_dlp.__file__).is_relative_to(RUNTIME_DIR):
            return "self-updated copy"
    except (AttributeError, TypeError, ValueError):
        pass
    return "bundled / installed"


def ffmpeg_source():
    return ("self-updated copy" if ffmpeg_dir() == str(RUNTIME_BIN)
            else "bundled / PATH")


def read_stamp():
    try:
        return json.loads(STAMP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_stamp(data):
    try:
        STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
        STAMP_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
#  Custom widgets
# --------------------------------------------------------------------------- #
class RoundButton(tk.Canvas):
    """A flat, rounded, hover-aware button drawn on a canvas."""

    def __init__(self, master, text, command=None, width=150, height=42, radius=11,
                 fill=ACCENT, hover=ACCENT_HI, fg="#ffffff", font=FONT_BTN,
                 parent_bg=CARD):
        super().__init__(master, width=width, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self.command = command
        self._fill, self._hover, self._fg = fill, hover, fg
        self._enabled = True
        self._shape = self._round_rect(1, 1, width - 1, height - 1, radius, fill=fill)
        self._text = self.create_text(width / 2, height / 2 + 1, text=text,
                                      fill=fg, font=font)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(cursor="hand2")

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _on_enter(self, _=None):
        if self._enabled:
            self.itemconfig(self._shape, fill=self._hover)

    def _on_leave(self, _=None):
        if self._enabled:
            self.itemconfig(self._shape, fill=self._fill)

    def _on_press(self, _=None):
        if self._enabled:
            self.move(self._text, 0, 1)

    def _on_release(self, _=None):
        if self._enabled:
            self.move(self._text, 0, -1)
            if self.command:
                self.command()

    def set_text(self, text):
        self.itemconfig(self._text, text=text)

    def set_colors(self, fill, hover):
        self._fill, self._hover = fill, hover
        self.itemconfig(self._shape, fill=fill)

    def set_enabled(self, on):
        self._enabled = on
        self.itemconfig(self._shape, fill=self._fill if on else BORDER)
        self.itemconfig(self._text, fill=self._fg if on else MUTED)
        self.configure(cursor="hand2" if on else "arrow")


class Segmented(tk.Canvas):
    """A two-or-more option pill selector."""

    def __init__(self, master, options, command=None, width=320, height=38,
                 parent_bg=CARD, accent=ACCENT):
        super().__init__(master, width=width, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self.options, self.command, self.accent = options, command, accent
        self._cw, self._ch = width, height   # _w is taken by tkinter
        self.index = 0
        self.bind("<Button-1>", self._click)
        self.configure(cursor="hand2")
        self._draw()

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        self.delete("all")
        w, h, n = self._cw, self._ch, len(self.options)
        self._round_rect(0, 0, w, h, h / 2, fill=INPUT, outline="")
        seg = w / n
        x1 = self.index * seg
        self._round_rect(x1 + 3, 3, x1 + seg - 3, h - 3, (h - 6) / 2,
                         fill=self.accent, outline="")
        for i, opt in enumerate(self.options):
            self.create_text(seg * i + seg / 2, h / 2 + 1, text=opt,
                             fill="#ffffff" if i == self.index else MUTED,
                             font=FONT_B if i == self.index else FONT)

    def _click(self, event):
        idx = min(int(event.x / (self._cw / len(self.options))),
                  len(self.options) - 1)
        if idx != self.index:
            self.set(idx)
            if self.command:
                self.command(idx)

    def set(self, idx):
        self.index = max(0, min(idx, len(self.options) - 1))
        self._draw()

    def get(self):
        return self.index


class Check(tk.Canvas):
    """A checkbox drawn to match the rest of the window."""

    def __init__(self, master, text, value=False, parent_bg=CARD):
        tw = tkfont.Font(font=FONT_S).measure(text)
        super().__init__(master, width=24 + tw, height=22, bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._on = bool(value)
        self._box = self.create_rectangle(2, 3, 18, 19, width=1)
        self._tick = self.create_line(6, 11, 9, 14, 15, 7, fill="#ffffff", width=2,
                                      capstyle="round", joinstyle="round")
        self.create_text(24, 12, text=text, fill=TEXT, font=FONT_S, anchor="w")
        self.bind("<Button-1>", lambda e: self.set(not self._on))
        self.configure(cursor="hand2")
        self._paint()

    def _paint(self):
        self.itemconfig(self._box, fill=ACCENT if self._on else INPUT,
                        outline=ACCENT if self._on else BORDER)
        self.itemconfig(self._tick, state="normal" if self._on else "hidden")

    def get(self):
        return self._on

    def set(self, value):
        self._on = bool(value)
        self._paint()


def card(parent, **kw):
    return tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                    highlightcolor=BORDER, highlightthickness=1, bd=0, **kw)


def label(parent, text, fg=MUTED, font=FONT_S, bg=CARD, **kw):
    return tk.Label(parent, text=text, fg=fg, bg=bg, font=font, anchor="w", **kw)


# --------------------------------------------------------------------------- #
#  yt-dlp logger bridge
# --------------------------------------------------------------------------- #
class QueueLogger:
    def __init__(self, q):
        self.q = q

    def debug(self, msg):
        if str(msg).startswith("[debug] "):
            return
        self._put(msg)

    def info(self, msg):
        self._put(msg)

    def warning(self, msg):
        self._put("! " + str(msg))

    def error(self, msg):
        self._put("x " + str(msg))

    def _put(self, msg):
        msg = strip_ansi(str(msg)).rstrip()
        if msg:
            self.q.put(("log", msg))


# --------------------------------------------------------------------------- #
#  Main application
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cob Downloader")
        self.configure(bg=BG)
        ico = BUNDLE / "icon.ico"
        if ico.exists():
            try:
                self.iconbitmap(default=str(ico))
            except tk.TclError:
                pass
        self._center(1020, 1010)
        self.minsize(960, 740)

        self.q = queue.Queue()
        self.worker = None
        self.cancel_flag = threading.Event()
        self.info = None
        self.playlist_count = 0
        self.files_done = 0
        self.link_i = self.link_n = 0
        self.failed = []
        self.cookie_hint = ""
        self.last_dir = None
        self.upd_busy = False
        self.ffmpeg_url = None

        self._init_style()
        self._build_ui()
        self._load_settings()
        self._sync_mode()

        self.url.focus_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if MAC:
            # Cmd-Q otherwise exits outright, skipping the settings save and
            # leaving a download half-written
            self.createcommand("::tk::mac::Quit", self._on_close)
        self._pump_job = self.after(80, self._pump)
        # after the window is up, so a slow network never delays the UI
        self._upd_job = self.after(400, self._check_updates)

    def _center(self, w, h):
        """Fit the window on screen and put it in the middle."""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        h = min(h, sh - 90)          # leave room for the taskbar
        w = min(w, sw - 40)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")

    # ---------------------------------------------------------------- style --
    def _init_style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("Dark.TCombobox", fieldbackground=INPUT, background=INPUT,
                     foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, arrowsize=14,
                     padding=(10, 7), relief="flat")
        st.map("Dark.TCombobox",
               fieldbackground=[("readonly", INPUT), ("disabled", CARD_HI)],
               foreground=[("disabled", MUTED)],
               bordercolor=[("focus", ACCENT)],
               arrowcolor=[("disabled", BORDER)],
               selectbackground=[("readonly", INPUT)],
               selectforeground=[("readonly", TEXT)])
        self.option_add("*TCombobox*Listbox.background", INPUT)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.font", FONT)

        st.configure("Bar.Horizontal.TProgressbar", troughcolor=INPUT,
                     background=ACCENT, bordercolor=INPUT, lightcolor=ACCENT,
                     darkcolor=ACCENT, thickness=9)
        st.configure("Dark.TCheckbutton", background=CARD, foreground=TEXT,
                     font=FONT_S, focuscolor=CARD, indicatorbackground=INPUT,
                     indicatorforeground=ACCENT, padding=2)
        st.map("Dark.TCheckbutton",
               background=[("active", CARD)],
               indicatorbackground=[("selected", ACCENT), ("active", CARD_HI)],
               indicatorforeground=[("selected", "#ffffff")])
        st.configure("Dark.Vertical.TScrollbar", background=BORDER, troughcolor=CARD,
                     bordercolor=CARD, arrowcolor=MUTED, relief="flat")

    # ------------------------------------------------------------------- ui --
    def _build_ui(self):
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True, padx=22, pady=18)

        # ---- header
        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", pady=(0, 14))
        tk.Label(head, text="Cob Downloader", bg=BG, fg=TEXT,
                 font=FONT_TITLE).pack(side="left")
        tk.Label(head, text=f"  yt-dlp {yt_dlp.version.__version__}", bg=BG,
                 fg=MUTED, font=FONT_S).pack(side="left", pady=(8, 0))
        missing = []
        if not ffmpeg_dir():
            missing.append("ffmpeg (needed to merge & convert)")
        if not js_runtimes():
            missing.append(f"a JS runtime ({QJS_EXE} is missing from this build)")
        tk.Label(head, bg=BG, fg=ACCENT if missing else MUTED, font=FONT_S,
                 text="missing: " + ", ".join(missing) if missing
                 else "ffmpeg ready").pack(side="right", pady=(8, 0))
        self.stale_days = ytdlp_age_days()

        # ---- url card
        c1 = card(root)
        c1.pack(fill="x")
        inner = tk.Frame(c1, bg=CARD)
        inner.pack(fill="x", padx=16, pady=14)
        label(inner, "VIDEO OR PLAYLIST LINKS   -   one per line").pack(
            anchor="w", pady=(0, 6))

        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x")
        ebox = tk.Frame(row, bg=INPUT, highlightbackground=BORDER,
                        highlightthickness=1, bd=0)
        ebox.pack(side="left", fill="both", expand=True)
        self.url = tk.Text(ebox, bg=INPUT, fg=TEXT, font=FONT_URL,
                           relief="flat", insertbackground=ACCENT, height=4,
                           highlightthickness=0, bd=0, wrap="none", padx=10, pady=7)
        self.url.pack(side="left", fill="both", expand=True)
        usb = ttk.Scrollbar(ebox, command=self.url.yview,
                            style="Dark.Vertical.TScrollbar")
        usb.pack(side="right", fill="y")
        self.url.configure(yscrollcommand=usb.set)
        # Command on macOS; there Control-a is the Emacs-style line start
        mod = "Command" if MAC else "Control"
        self.url.bind(f"<{mod}-a>", self._select_all_links)
        self.url.bind(f"<{mod}-Return>", lambda e: (self.start(), "break")[1])

        btns = tk.Frame(row, bg=CARD)
        btns.pack(side="left", padx=(10, 0))
        for text, cmd in (("Fetch info", self.fetch), ("Paste", self._paste),
                          ("Clear", self._clear_links)):
            RoundButton(btns, text, cmd, width=118, height=32, radius=9,
                        fill=CARD_HI, hover=BORDER, fg=TEXT,
                        font=FONT_B).pack(pady=(0, 6))

        self.meta = label(inner, "Paste as many links as you like, one per line - they "
                                 "download one after another with these settings.")
        self.meta.pack(anchor="w", pady=(8, 0))

        # ---- options card
        c2 = card(root)
        c2.pack(fill="x", pady=(14, 0))
        opt = tk.Frame(c2, bg=CARD)
        opt.pack(fill="x", padx=16, pady=16)
        for i in range(4):
            opt.columnconfigure(i, weight=1, uniform="opt")

        label(opt, "I WANT").grid(row=0, column=0, sticky="w")
        self.mode = Segmented(opt, ["Video", "Audio only"], self._on_mode,
                              width=214, height=38)
        self.mode.grid(row=1, column=0, sticky="w", pady=(6, 0))

        label(opt, "DOWNLOAD").grid(row=0, column=1, sticky="w", padx=(14, 0))
        self.scope = Segmented(opt, ["This video", "Whole playlist"], None,
                               width=214, height=38, accent=BLUE)
        self.scope.grid(row=1, column=1, sticky="w", padx=(14, 0), pady=(6, 0))

        label(opt, "FILE TYPE").grid(row=2, column=0, sticky="w", pady=(18, 0))
        self.ftype = ttk.Combobox(opt, values=VIDEO_TYPES, state="readonly",
                                  style="Dark.TCombobox", font=FONT)
        self.ftype.set("MP4")
        self.ftype.grid(row=3, column=0, sticky="ew", padx=(0, 7), pady=(6, 0))

        label(opt, "RESOLUTION").grid(row=2, column=1, sticky="w", pady=(18, 0),
                                      padx=(7, 0))
        self.res = ttk.Combobox(opt, values=RESOLUTIONS, state="readonly",
                                style="Dark.TCombobox", font=FONT)
        self.res.set("1080p (Full HD)")
        self.res.grid(row=3, column=1, sticky="ew", padx=7, pady=(6, 0))

        label(opt, "FRAME RATE").grid(row=2, column=2, sticky="w", pady=(18, 0),
                                      padx=(7, 0))
        self.fps = ttk.Combobox(opt, values=FPS_CHOICES, state="readonly",
                                style="Dark.TCombobox", font=FONT)
        self.fps.set("Any")
        self.fps.grid(row=3, column=2, sticky="ew", padx=7, pady=(6, 0))

        label(opt, "AUDIO QUALITY").grid(row=2, column=3, sticky="w", pady=(18, 0),
                                         padx=(7, 0))
        self.aq = ttk.Combobox(opt, values=AUDIO_QUALITY, state="readonly",
                               style="Dark.TCombobox", font=FONT)
        self.aq.set("Best")
        self.aq.grid(row=3, column=3, sticky="ew", padx=(7, 0), pady=(6, 0))

        extras = tk.Frame(opt, bg=CARD)
        extras.grid(row=4, column=0, columnspan=3, rowspan=2, sticky="nw",
                    pady=(18, 0))
        self.v_thumb = Check(extras, "Embed thumbnail", True)
        self.v_meta = Check(extras, "Embed metadata & chapters", True)
        self.v_subs = Check(extras, "English subtitles", False)
        self.v_arch = Check(extras, "Skip files already downloaded", False)
        for c in (self.v_thumb, self.v_meta, self.v_subs, self.v_arch):
            c.pack(side="left", padx=(0, 24))

        label(opt, "BROWSER COOKIES").grid(row=4, column=3, sticky="w",
                                           pady=(18, 0), padx=(7, 0))
        self.cookies = ttk.Combobox(opt, values=BROWSERS, state="readonly",
                                    style="Dark.TCombobox", font=FONT)
        self.cookies.set("None")
        self.cookies.grid(row=5, column=3, sticky="ew", padx=(7, 0), pady=(6, 0))

        # ---- output card
        c3 = card(root)
        c3.pack(fill="x", pady=(14, 0))
        o = tk.Frame(c3, bg=CARD)
        o.pack(fill="x", padx=16, pady=14)
        label(o, "SAVE TO").pack(anchor="w", pady=(0, 6))
        orow = tk.Frame(o, bg=CARD)
        orow.pack(fill="x")
        obox = tk.Frame(orow, bg=INPUT, highlightbackground=BORDER,
                        highlightthickness=1, bd=0)
        obox.pack(side="left", fill="x", expand=True, ipady=5)
        self.outdir = tk.Entry(obox, bg=INPUT, fg=TEXT, font=FONT, relief="flat",
                               insertbackground=ACCENT, highlightthickness=0, bd=0)
        self.outdir.insert(0, str(Path.home() / "Downloads"))
        self.outdir.pack(fill="x", padx=12, pady=4)
        RoundButton(orow, "Browse", self._browse, width=92, height=36, fill=CARD_HI,
                    hover=BORDER, fg=TEXT, font=FONT_B).pack(side="left", padx=(10, 0))
        RoundButton(orow, "Open", self._open_folder, width=80, height=36, fill=CARD_HI,
                    hover=BORDER, fg=TEXT, font=FONT_B).pack(side="left", padx=(8, 0))

        # ---- components / self update, kept to one row to save height
        urow = tk.Frame(o, bg=CARD)
        urow.pack(fill="x", pady=(14, 0))
        label(urow, "COMPONENTS").pack(side="left", padx=(0, 12))
        self.upd_status = label(urow, "checking for updates...", fg=MUTED)
        self.upd_status.pack(side="left", fill="x", expand=True)
        self.upd_go = RoundButton(urow, "Check now", lambda: self._check_updates(True),
                                  width=104, height=30, radius=9, fill=CARD_HI,
                                  hover=BORDER, fg=TEXT, font=FONT_B)
        self.upd_go.pack(side="right")
        self.v_autoupd = Check(urow, "auto-update yt-dlp", True)
        self.v_autoupd.pack(side="right", padx=(12, 12))
        # only shown once a newer ffmpeg is actually found
        self.upd_ff = RoundButton(urow, "Update ffmpeg", self._update_ffmpeg,
                                  width=130, height=30, radius=9, fill=CARD_HI,
                                  hover=BORDER, fg=TEXT, font=FONT_B)

        # ---- progress + action
        c4 = card(root)
        c4.pack(fill="x", pady=(14, 0))
        p = tk.Frame(c4, bg=CARD)
        p.pack(fill="x", padx=16, pady=14)

        self.go = RoundButton(p, "Download", self.start, width=170, height=52,
                              radius=13)
        self.go.pack(side="right", padx=(18, 0))

        left = tk.Frame(p, bg=CARD)
        left.pack(side="left", fill="x", expand=True)
        self.status = tk.Label(left, text="Ready", bg=CARD, fg=TEXT, font=FONT_B,
                               anchor="w")
        self.status.pack(fill="x")
        self.bar = ttk.Progressbar(left, style="Bar.Horizontal.TProgressbar",
                                   maximum=100, value=0)
        self.bar.pack(fill="x", pady=(9, 7))
        self.detail = tk.Label(left, text="", bg=CARD, fg=MUTED, font=FONT_S,
                               anchor="w")
        self.detail.pack(fill="x")

        # ---- log
        c5 = card(root)
        c5.pack(fill="both", expand=True, pady=(14, 0))
        lhead = tk.Frame(c5, bg=CARD)
        lhead.pack(fill="x", padx=16, pady=(12, 0))
        label(lhead, "LOG").pack(side="left")
        clear = tk.Label(lhead, text="clear", bg=CARD, fg=MUTED, font=FONT_S,
                         cursor="hand2")
        clear.pack(side="right")
        clear.bind("<Button-1>", lambda e: self._clear_log())

        lwrap = tk.Frame(c5, bg=CARD)
        lwrap.pack(fill="both", expand=True, padx=16, pady=(8, 14))
        self.log = tk.Text(lwrap, bg=BG, fg=MUTED, font=FONT_MONO, relief="flat",
                           height=6, wrap="word", highlightthickness=1,
                           highlightbackground=BORDER, insertbackground=TEXT,
                           padx=10, pady=8)
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lwrap, command=self.log.yview,
                           style="Dark.Vertical.TScrollbar")
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set, state="disabled")
        self.log.tag_configure("err", foreground=ACCENT)
        self.log.tag_configure("ok", foreground=GREEN)

        if self.stale_days is not None and self.stale_days > 90:
            self._log(f"! this yt-dlp is {self.stale_days} days old - YouTube "
                      f"downloads may fail with 403 Forbidden. "
                      f"Update with:  pip install -U yt-dlp", "err")

    # -------------------------------------------------------------- helpers --
    def _urls(self):
        """Every link in the box, in order, without duplicates."""
        raw = self.url.get("1.0", "end")
        out, seen = [], set()
        for token in raw.split():
            token = token.strip("\"'<>,;")
            if "://" not in token and not token.startswith("www."):
                continue
            if token.startswith("www."):
                token = "https://" + token
            if token not in seen:
                seen.add(token)
                out.append(token)
        if not out:                      # not link-shaped, let yt-dlp judge it
            out = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return out

    def _select_all_links(self, _=None):
        self.url.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _clear_links(self):
        self.url.delete("1.0", "end")
        self.meta.configure(text="Paste as many links as you like, one per line - they "
                                 "download one after another with these settings.",
                            fg=MUTED)

    def _paste(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            return
        if not text:
            return
        current = self.url.get("1.0", "end").strip()
        self.url.insert("end", ("\n" if current else "") + text)
        self.url.see("end")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.outdir.get() or str(Path.home()))
        if d:
            self.outdir.delete(0, "end")
            self.outdir.insert(0, d)

    def _open_folder(self):
        d = self.last_dir or self.outdir.get()
        if d and os.path.isdir(d):
            if os.name == "nt":
                os.startfile(d)
            elif MAC:
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        else:
            messagebox.showinfo("Cob Downloader", "That folder does not exist yet.")

    def _log(self, msg, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _on_mode(self, idx):
        self._sync_mode()

    def _sync_mode(self):
        audio = self.mode.get() == 1
        self.ftype.configure(values=AUDIO_TYPES if audio else VIDEO_TYPES)
        if audio and self.ftype.get() not in AUDIO_TYPES:
            self.ftype.set("MP3")
        if not audio and self.ftype.get() not in VIDEO_TYPES:
            self.ftype.set("MP4")
        state = "disabled" if audio else "readonly"
        self.res.configure(state=state)
        self.fps.configure(state=state)
        self.aq.configure(state="readonly" if audio else "disabled")

    # -------------------------------------------------------- self updating --
    def _check_updates(self, manual=False):
        """Look for newer yt-dlp / ffmpeg, off the UI thread."""
        if self.upd_busy:
            return
        self.upd_busy = True
        self.upd_go.set_enabled(False)
        self.upd_status.configure(text="checking for updates...", fg=MUTED)
        threading.Thread(target=self._upd_worker, args=(manual,),
                         daemon=True).start()

    def _upd_worker(self, manual):
        state = read_stamp()
        try:
            fresh = time.time() - float(state.get("checked") or 0) < CHECK_EVERY
            if not manual and fresh:
                # a recent check already told us everything; stay offline
                self.q.put(("upd", state))
                return

            state = {"checked": time.time()}
            try:
                latest, wheel = ytdlp_latest()
                state["ytdlp_latest"] = latest
                current = yt_dlp.version.__version__
                if version_tuple(latest) > version_tuple(current):
                    if self.v_autoupd.get():
                        self.q.put(("updlog",
                                    (None, f"yt-dlp {current} -> {latest}, "
                                           f"updating...")))
                        install_ytdlp(wheel, self._upd_progress("yt-dlp"))
                        state["ytdlp_restart"] = latest
                        self.q.put(("updlog",
                                    ("ok", f"yt-dlp updated to {latest} - restart "
                                           f"Cob Downloader to start using it")))
                    else:
                        self.q.put(("updlog",
                                    (None, f"yt-dlp {latest} is available - tick "
                                           f"the box below to install it")))
            except Exception as e:
                state["ytdlp_error"] = str(e)
                self.q.put(("updlog", ("err", f"x yt-dlp update check failed: {e}")))

            try:
                found = ffmpeg_latest()
                if found:
                    version, url, size = found
                    state["ffmpeg_latest"] = version
                    state["ffmpeg_url"] = url
                    state["ffmpeg_size"] = size
            except Exception as e:
                state["ffmpeg_error"] = str(e)
                self.q.put(("updlog", ("err", f"x ffmpeg update check failed: {e}")))

            write_stamp(state)
            self.q.put(("upd", state))
        except Exception as e:                      # never let a check crash the app
            self.q.put(("updlog", ("err", f"x update check failed: {e}")))
            self.q.put(("upd", {"checked": time.time(), "ytdlp_error": str(e)}))

    def _upd_progress(self, what):
        """A download callback that reports into the components row."""
        last = [0.0]

        def report(pct, got, total):
            if pct - last[0] >= 2 or pct >= 100:
                last[0] = pct
                self.q.put(("updstatus",
                            f"downloading {what}  {pct:.0f}%  "
                            f"({human_size(got)} / {human_size(total)})"))
        return report

    def _apply_updates(self, state):
        """Turn a finished check into the components row's text and buttons."""
        self.upd_busy = False
        self.upd_go.set_enabled(True)
        installed = yt_dlp.version.__version__
        bits = [f"yt-dlp {installed}"]
        if state.get("ytdlp_restart"):
            bits[0] += f" (updated to {state['ytdlp_restart']}, restart to use)"
        elif (state.get("ytdlp_latest")
              and version_tuple(state["ytdlp_latest"]) > version_tuple(installed)):
            bits[0] += f" - {state['ytdlp_latest']} available"
        elif state.get("ytdlp_error"):
            bits[0] += " - check failed (see log)"

        have = ffmpeg_version()
        bits.append(f"ffmpeg {have.split('-')[0] if have else 'missing'}")
        if state.get("ffmpeg_error"):
            bits[-1] += " - check failed"
        newer = (state.get("ffmpeg_latest") and have
                 and version_tuple(state["ffmpeg_latest"]) > version_tuple(have))
        if newer:
            size = state.get("ffmpeg_size") or 0
            bits[-1] += f" - {state['ffmpeg_latest']} available ({human_size(size)})"
            self.ffmpeg_url = state.get("ffmpeg_url")
            self.upd_ff.pack(side="right", padx=(0, 4))
        else:
            self.ffmpeg_url = None
            self.upd_ff.pack_forget()
        self.upd_status.configure(text="   -   ".join(bits),
                                  fg=ACCENT if newer else MUTED)

    def _update_ffmpeg(self):
        """Download ffmpeg on demand - these builds are ~170 MB, so we ask."""
        if self.upd_busy or not self.ffmpeg_url:
            return
        state = read_stamp()
        size = human_size(state.get("ffmpeg_size") or 0)
        if not messagebox.askyesno(
                "Cob Downloader",
                f"Download ffmpeg {state.get('ffmpeg_latest', '')} now?"
                f"\n\n"
                f"That is about {size}.  Downloading happens in the background "
                f"and the new ffmpeg is used as soon as it finishes."):
            return
        self.upd_busy = True
        self.upd_go.set_enabled(False)
        self.upd_ff.set_enabled(False)
        threading.Thread(target=self._ffmpeg_worker,
                         args=(self.ffmpeg_url,), daemon=True).start()

    def _ffmpeg_worker(self, url):
        try:
            install_ffmpeg(url, self._upd_progress("ffmpeg"))
            self.q.put(("updlog", ("ok", f"ffmpeg updated - now using "
                                         f"{RUNTIME_BIN}")))
            state = read_stamp()
            state.pop("ffmpeg_url", None)
            write_stamp(state)
            self.q.put(("upd", state))
        except Exception as e:
            self.q.put(("updlog", ("err", f"x ffmpeg update failed: {e}")))
            self.q.put(("upd", read_stamp()))

    # ------------------------------------------------------------- fetching --
    def fetch(self):
        urls = self._urls()
        if not urls:
            messagebox.showinfo("Cob Downloader", "Paste a link first.")
            return
        self.meta.configure(fg=MUTED, text="Fetching info..." if len(urls) == 1
                            else f"Fetching info for the first of {len(urls)} links...")
        threading.Thread(target=self._fetch_worker, args=(urls[0], len(urls)),
                         daemon=True).start()

    def _fetch_worker(self, url, queued=1):
        try:
            flat = {"quiet": True, "no_warnings": True, "skip_download": True,
                    "extract_flat": "in_playlist", "noplaylist": False,
                    "js_runtimes": js_runtimes(), "ffmpeg_location": ffmpeg_dir(),
                    "remote_components": REMOTE_COMPONENTS,
                    "logger": QueueLogger(self.q)}
            with yt_dlp.YoutubeDL(flat) as ydl:
                info = ydl.extract_info(url, download=False)

            playlist, count, first = None, 0, url
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                playlist = info.get("title") or "Playlist"
                count = info.get("playlist_count") or len(entries)
                if entries:
                    first = (entries[0].get("url")
                             or entries[0].get("webpage_url") or url)

            deep = {"quiet": True, "no_warnings": True, "skip_download": True,
                    "noplaylist": True, "js_runtimes": js_runtimes(),
                    "ffmpeg_location": ffmpeg_dir(),
                    "remote_components": REMOTE_COMPONENTS,
                    "logger": QueueLogger(self.q)}
            with yt_dlp.YoutubeDL(deep) as ydl:
                vinfo = ydl.extract_info(first, download=False)

            heights, fpses = set(), set()
            for f in vinfo.get("formats") or []:
                if f.get("vcodec") not in (None, "none") and f.get("height"):
                    heights.add(int(f["height"]))
                    if f.get("fps"):
                        fpses.add(int(round(f["fps"])))
            self.q.put(("info", {
                "title": vinfo.get("title") or "?",
                "uploader": vinfo.get("uploader") or vinfo.get("channel") or "",
                "duration": vinfo.get("duration"),
                "playlist": playlist,
                "count": count,
                "heights": sorted(heights, reverse=True),
                "fps": sorted(fpses, reverse=True),
                "queued": queued,
            }))
        except Exception as e:
            self.q.put(("infoerr", str(e)))

    def _apply_info(self, d):
        self.info = d
        self.playlist_count = d["count"]
        bits = []
        if d["queued"] > 1:
            bits.append(f"{d['queued']} links queued, first is")
        bits.append(d["title"])
        if d["uploader"]:
            bits.append(d["uploader"])
        if d["duration"]:
            bits.append(human_time(d["duration"]))
        if d["playlist"]:
            bits.append(f"playlist: {d['playlist']} ({d['count']} videos)")
            self.scope.set(1)
        else:
            self.scope.set(0)
        self.meta.configure(text="   -   ".join(bits), fg=TEXT)

        if d["heights"]:
            vals = ["Best available"] + [f"{h}p" for h in d["heights"]]
            cur = self.res.get()
            self.res.configure(values=vals)
            self.res.set(cur if cur in vals else vals[0])
            self._log("available resolutions: "
                      + ", ".join(f"{h}p" for h in d["heights"]))
        if d["fps"]:
            vals = ["Any"] + [str(f) for f in d["fps"]]
            cur = self.fps.get()
            self.fps.configure(values=vals)
            self.fps.set(cur if cur in vals else "Any")
            self._log("available frame rates: "
                      + ", ".join(f"{f} fps" for f in d["fps"]))

    # ---------------------------------------------------------- downloading --
    def _height(self):
        m = re.match(r"(\d+)p", self.res.get())
        return int(m.group(1)) if m else None

    def _fps_cap(self):
        return int(self.fps.get()) if self.fps.get().isdigit() else None

    def _build_format(self):
        if self.mode.get() == 1:
            return "bestaudio/best"
        h, f = self._height(), self._fps_cap()
        filt = ""
        if h:
            filt += f"[height<={h}]"
        if f:
            filt += f"[fps<={f}]"
        ext = self.ftype.get().lower()
        chain = []
        if ext == "mp4":
            chain += [f"bv*{filt}[ext=mp4]+ba[ext=m4a]", f"bv*{filt}[ext=mp4]+ba"]
        elif ext == "webm":
            chain += [f"bv*{filt}[ext=webm]+ba[ext=webm]", f"bv*{filt}[ext=webm]+ba"]
        chain += [f"bv*{filt}+ba", f"b{filt}"]
        if filt:            # last resort, so a strict filter never fails outright
            chain += ["bv*+ba", "b"]
        return "/".join(chain)

    def _build_opts(self, outdir, playlist):
        audio = self.mode.get() == 1
        ext = self.ftype.get().lower()

        if playlist:
            tmpl = "%(playlist_title).80B/%(playlist_index)03d - %(title).120B.%(ext)s"
        else:
            tmpl = "%(title).150B.%(ext)s"

        # ffmpeg cannot store a cover image in these containers
        thumb_ok = self.v_thumb.get() and ext not in ("webm", "wav")

        pps = []
        if not audio and ext in ("mp4", "mkv"):
            # merging usually lands in the right container already; this catches
            # the single-file case (webm is left alone, it cannot hold h264)
            pps.append({"key": "FFmpegVideoRemuxer", "preferedformat": ext})
        if audio:
            pp = {"key": "FFmpegExtractAudio", "preferredcodec": ext}
            q = self.aq.get()
            if q != "Best" and ext in ("mp3", "m4a", "opus"):
                pp["preferredquality"] = q.split()[0]
            pps.append(pp)
        if self.v_meta.get():
            pps.append({"key": "FFmpegMetadata", "add_chapters": True,
                        "add_metadata": True})
        if thumb_ok:
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        if self.v_subs.get():
            if audio:
                pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
            else:
                pps.append({"key": "FFmpegEmbedSubtitle",
                            "already_have_subtitle": False})

        opts = {
            "format": self._build_format(),
            "outtmpl": {"default": tmpl},
            "paths": {"home": outdir},
            "noplaylist": not playlist,
            "ignoreerrors": "only_download" if playlist else False,
            "postprocessors": pps,
            "progress_hooks": [self._hook],
            "postprocessor_hooks": [self._pp_hook],
            "logger": QueueLogger(self.q),
            "quiet": True,
            "noprogress": True,
            "consoletitle": False,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": os.name == "nt",
            "js_runtimes": js_runtimes(),
            "remote_components": REMOTE_COMPONENTS,
            "ffmpeg_location": ffmpeg_dir(),
            "writethumbnail": thumb_ok,
            "continuedl": True,
        }
        if not audio:
            # merge_output_format falls back to mkv by itself when the picked
            # streams do not fit the container, so webm is left to it
            opts["merge_output_format"] = ext
        if self.v_subs.get():
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            # these are regexes: "en.*" would also match YouTube's
            # auto-translated tracks ("en-de", "en-fr", ...), which pulls dozens
            # of files and trips HTTP 429.  Variants that do not exist on a
            # given video are dropped silently, so listing them is safe.
            opts["subtitleslangs"] = ["en", "en-US", "en-GB", "en-orig"]
        if self.cookies.get() != "None":
            opts["cookiesfrombrowser"] = (self.cookies.get().lower(), None, None, None)
        if self.v_arch.get():
            opts["download_archive"] = str(Path(outdir) / ".cob-archive.txt")
        return opts

    def start(self):
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()
            self.status.configure(text="Cancelling...", fg=MUTED)
            return

        urls = self._urls()
        if not urls:
            messagebox.showinfo("Cob Downloader", "Paste at least one link first.")
            return
        outdir = self.outdir.get().strip() or str(Path.home() / "Downloads")
        try:
            Path(outdir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Cob Downloader", f"Cannot use that folder:\n{e}")
            return

        playlist = self.scope.get() == 1
        opts = self._build_opts(outdir, playlist)
        self.last_dir = outdir
        self.files_done = 0
        self.failed = []
        self.cookie_hint = ""
        self.link_i, self.link_n = 0, len(urls)
        self.cancel_flag.clear()
        self.bar.configure(value=0)
        self.go.set_text("Cancel")
        self.go.set_colors(CARD_HI, BORDER)
        self.status.configure(text="Starting...", fg=TEXT)
        self.detail.configure(text="")
        self._log("-" * 62)
        self._log(f"{len(urls)} link(s) queued")
        self._log(f"format: {opts['format']}")
        self._log(f"saving to: {outdir}")
        self._save_settings()

        self.worker = threading.Thread(target=self._dl_worker, args=(urls, opts),
                                       daemon=True)
        self.worker.start()

    def _dl_worker(self, urls, opts):
        """Work through the links one at a time; a bad one does not stop the rest."""
        failed = []
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                for i, url in enumerate(urls, 1):
                    self.q.put(("link", (i, len(urls), url)))
                    try:
                        ydl.download([url])
                    except Cancelled:
                        raise
                    except Exception as e:
                        failed.append(url)
                        if "cookie" in str(e).lower():
                            self.q.put(("cookiefail", None))
                        if "403" in str(e):
                            self.q.put(("stalefail", None))
                        self.q.put(("log", f"x link {i} failed: {e}"))
            self.q.put(("done", failed))
        except Cancelled:
            self.q.put(("cancelled", None))
        except Exception as e:
            self.q.put(("error", str(e)))

    def _hook(self, d):
        if self.cancel_flag.is_set():
            raise Cancelled()
        self.q.put(("progress", d))

    def _pp_hook(self, d):
        if self.cancel_flag.is_set():
            raise Cancelled()
        self.q.put(("pp", d))

    # -------------------------------------------------------------- ui pump --
    def _pump(self):
        try:
            while True:
                try:
                    kind, payload = self.q.get_nowait()
                except queue.Empty:
                    break
                if kind == "progress":
                    self._on_progress(payload)
                elif kind == "pp":
                    self._on_pp(payload)
                elif kind == "cookiefail":
                    who = self.cookies.get()
                    self.cookie_hint = (
                        f"Could not read {who} cookies - close {who} completely and "
                        f"try again, or set Browser cookies to Firefox or None.")
                    self._log("! " + self.cookie_hint)
                elif kind == "upd":
                    self._apply_updates(payload)
                elif kind == "updstatus":
                    self.upd_status.configure(text=payload, fg=MUTED)
                elif kind == "updlog":
                    tag, text = payload
                    self._log(text, tag)
                elif kind == "stalefail":
                    if not self.cookie_hint:
                        self.cookie_hint = (
                            "403 Forbidden usually means yt-dlp is out of date - "
                            "update it with:  pip install -U yt-dlp")
                        self._log("! " + self.cookie_hint)
                elif kind == "link":
                    self.link_i, self.link_n, url = payload
                    if self.link_n > 1:
                        self.status.configure(
                            text=f"Link {self.link_i} of {self.link_n}...", fg=TEXT)
                    self._log(f"[{self.link_i}/{self.link_n}] {url}")
                elif kind == "log":
                    self._log(payload, "err" if payload.startswith("x ") else None)
                elif kind == "info":
                    self._apply_info(payload)
                elif kind == "infoerr":
                    self.meta.configure(text="Could not read that link.", fg=ACCENT)
                    self._log("x " + payload, "err")
                elif kind in ("done", "cancelled", "error"):
                    self._finish(kind, payload)
            self._pump_job = self.after(80, self._pump)
        except tk.TclError:
            pass          # the window was closed while an update was in flight

    def _on_progress(self, d):
        st = d.get("status")
        name = Path(d.get("filename") or "").name
        if st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            got = d.get("downloaded_bytes") or 0
            pct = (got / total * 100) if total else 0
            self.bar.configure(value=pct)
            idx = (d.get("info_dict") or {}).get("playlist_index")
            n = (d.get("info_dict") or {}).get("n_entries") or self.playlist_count
            head = "Downloading"
            if self.link_n > 1:
                head += f" link {self.link_i}/{self.link_n}"
            if idx and n:
                head += f" - video {idx}/{n}"
            self.status.configure(text=f"{head}: {name[:64]}", fg=TEXT)
            speed = d.get("speed")
            self.detail.configure(text="    ".join([
                f"{pct:.1f}%" if total else "size unknown",
                f"{human_size(got)} / {human_size(total)}",
                f"{human_size(speed)}/s" if speed else "-",
                f"ETA {human_time(d.get('eta'))}",
            ]))
        elif st == "finished":
            self.files_done += 1
            self.bar.configure(value=100)
            self.status.configure(text="Processing...", fg=TEXT)
            self.detail.configure(text=f"downloaded {name[:80]}")
        elif st == "error":
            self._log(f"x download error on {name}", "err")

    def _on_pp(self, d):
        if d.get("status") != "started":
            return
        nice = {"Merger": "Merging video + audio...",
                "FFmpegVideoRemuxer": "Remuxing container...",
                "FFmpegExtractAudio": "Converting audio...",
                "EmbedThumbnail": "Embedding thumbnail...",
                "FFmpegMetadata": "Writing metadata...",
                "FFmpegEmbedSubtitle": "Embedding subtitles..."
                }.get(d.get("postprocessor", ""))
        if nice:
            self.status.configure(text=nice, fg=TEXT)

    def _finish(self, kind, payload):
        self.go.set_text("Download")
        self.go.set_colors(ACCENT, ACCENT_HI)
        if kind == "done":
            n, failed = self.files_done, payload or []
            self.bar.configure(value=100)
            text = (f"Done - {n} file{'s' if n != 1 else ''} saved" if n
                    else "Nothing new to download")
            if failed:
                text += f", {len(failed)} link{'s' if len(failed) != 1 else ''} failed"
            self.status.configure(text=text, fg=ACCENT if failed else GREEN)
            self.detail.configure(text=self.cookie_hint or self.last_dir or "",
                                  fg=ACCENT if self.cookie_hint else MUTED)
            self._log(f"finished - {n} file(s) in {self.last_dir}", "ok")
            for url in failed:
                self._log("x did not download: " + url, "err")
        elif kind == "cancelled":
            self.bar.configure(value=0)
            self.status.configure(text="Cancelled", fg=MUTED)
            self.detail.configure(text="")
            self._log("cancelled by user")
        else:
            self.bar.configure(value=0)
            self.status.configure(text="Failed - see the log below", fg=ACCENT)
            self.detail.configure(text=str(payload)[:160])
            self._log("x " + str(payload), "err")

    # ---------------------------------------------------------------- state --
    def _save_settings(self):
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps({
                "outdir": self.outdir.get(), "mode": self.mode.get(),
                "scope": self.scope.get(), "ftype": self.ftype.get(),
                "res": self.res.get(), "fps": self.fps.get(), "aq": self.aq.get(),
                "cookies": self.cookies.get(),
                "thumb": self.v_thumb.get(), "meta": self.v_meta.get(),
                "subs": self.v_subs.get(), "arch": self.v_arch.get(),
                "autoupd": self.v_autoupd.get(),
            }, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _load_settings(self):
        try:
            s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.mode.set(s.get("mode", 0))
        self.scope.set(s.get("scope", 0))
        self._sync_mode()
        for widget, key in ((self.ftype, "ftype"), (self.res, "res"),
                            (self.fps, "fps"), (self.aq, "aq"),
                            (self.cookies, "cookies")):
            if s.get(key) in widget.cget("values"):
                widget.set(s[key])
        if s.get("outdir"):
            self.outdir.delete(0, "end")
            self.outdir.insert(0, s["outdir"])
        self.v_thumb.set(s.get("thumb", True))
        self.v_meta.set(s.get("meta", True))
        self.v_subs.set(s.get("subs", False))
        self.v_arch.set(s.get("arch", False))
        self.v_autoupd.set(s.get("autoupd", True))

    def destroy(self):
        # drop pending timers first, or Tcl complains about a dead callback
        for attr in ("_pump_job", "_upd_job"):
            job = getattr(self, attr, None)
            setattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        super().destroy()

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Cob Downloader",
                                       "A download is running. Quit anyway?"):
                return
            self.cancel_flag.set()
        self._save_settings()
        self.destroy()


def self_check():
    """`Cob Downloader.exe --check` - writes a short report and exits.

    Add --quiet to skip the popup and only write the file.

    Handy on someone else's machine when a download misbehaves.
    """
    report = Path(SETTINGS_FILE).parent / "check-report.txt"
    lines = [f"Cob Downloader {APP_VERSION} self check",
             f"packaged build : {FROZEN}",
             f"bundle folder  : {BUNDLE}",
             f"settings file  : {SETTINGS_FILE}",
             f"yt-dlp         : {yt_dlp.version.__version__}"
             + (f"  ({age} days old - UPDATE IT)" if (age := ytdlp_age_days())
                and age > 90 else ""),
             f"ffmpeg folder  : {ffmpeg_dir() or 'NOT FOUND'}",
             f"js runtimes    : {js_runtimes() or 'none (YouTube may need deno)'}",
             f"runtime folder : {RUNTIME_DIR}",
             f"tls roots      : {len(ssl_context().get_ca_certs())} from {_SSL_SOURCE}",
             f"yt-dlp source  : {ytdlp_source()}",
             f"ffmpeg source  : {ffmpeg_source()}"]
    try:
        latest, _ = ytdlp_latest()
        newer = version_tuple(latest) > version_tuple(yt_dlp.version.__version__)
        lines.append(f"yt-dlp latest  : {latest}"
                     + ("  <- UPDATE AVAILABLE" if newer else "  (up to date)"))
    except Exception as e:
        lines.append(f"yt-dlp latest  : check failed - {e}")
    try:
        found = ffmpeg_latest()
        if found:
            have = ffmpeg_version()
            newer = have and version_tuple(found[0]) > version_tuple(have)
            lines.append(f"ffmpeg latest  : {found[0]} ({human_size(found[2])})"
                         + ("  <- UPDATE AVAILABLE" if newer else "  (up to date)"))
    except Exception as e:
        lines.append(f"ffmpeg latest  : check failed - {e}")
    ff = ffmpeg_dir()
    if ff:
        try:
            out = subprocess.run([str(Path(ff) / "ffmpeg"), "-version"],
                                 capture_output=True, text=True, timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines.append("ffmpeg runs    : " + out.stdout.splitlines()[0])
        except Exception as e:
            lines.append(f"ffmpeg runs    : FAILED - {e}")
    try:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "noplaylist": True, "js_runtimes": js_runtimes(),
                "remote_components": REMOTE_COMPONENTS, "ffmpeg_location": ff}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("https://www.youtube.com/watch?v=aqz-KE-bpKQ",
                                    download=False)
        heights = sorted({f["height"] for f in info.get("formats") or []
                          if f.get("height")}, reverse=True)
        lines.append(f"extractor      : ok - read '{info.get('title')}'")
        lines.append(f"resolutions    : {heights[:8]}")
    except Exception as e:
        lines.append(f"extractor      : FAILED - {e}")
    text = "\n".join(lines)
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text, encoding="utf-8")
        text += f"\n\nSaved to {report}"
    except OSError:
        pass
    print(text)
    if FROZEN and "--quiet" not in sys.argv[1:]:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Cob Downloader - self check", text)
        root.destroy()


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    if "--check" in sys.argv[1:]:
        self_check()
        return
    App().mainloop()


if __name__ == "__main__":
    main()
