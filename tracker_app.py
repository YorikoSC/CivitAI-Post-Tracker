from __future__ import annotations

import argparse
import ctypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import date, datetime
from pathlib import Path


def _short_windows_path(path: Path) -> str:
    if not sys.platform.startswith("win"):
        return str(path)
    try:
        raw_path = str(path)
        buffer_size = ctypes.windll.kernel32.GetShortPathNameW(raw_path, None, 0)
        if buffer_size <= 0:
            return raw_path
        buffer = ctypes.create_unicode_buffer(buffer_size)
        result = ctypes.windll.kernel32.GetShortPathNameW(raw_path, buffer, buffer_size)
        if result <= 0:
            return raw_path
        return buffer.value
    except Exception:
        return str(path)


def _prepare_frozen_tcl_tk() -> None:
    if not getattr(sys, "frozen", False):
        return
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent / "_internal"))
    tcl_dir = bundle_root / "_tcl_data"
    tk_dir = bundle_root / "_tk_data"
    if sys.platform.startswith("win"):
        runtime_roots = []
        explicit_runtime = os.environ.get("CIVITAI_TCLTK_DIR", "").strip()
        if explicit_runtime:
            runtime_roots.append(Path(explicit_runtime))
        runtime_roots.extend([
            Path("C:\\Users\\Public") / "CivitAITracker" / "tcltk",
            Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "CivitAITracker" / "tcltk",
        ])
        for runtime_root in runtime_roots:
            try:
                runtime_root.mkdir(parents=True, exist_ok=True)
                runtime_tcl = runtime_root / "_tcl_data"
                runtime_tk = runtime_root / "_tk_data"
                if tcl_dir.exists() and not (runtime_tcl / "init.tcl").exists():
                    shutil.copytree(tcl_dir, runtime_tcl, dirs_exist_ok=True)
                if tk_dir.exists() and not (runtime_tk / "tk.tcl").exists():
                    shutil.copytree(tk_dir, runtime_tk, dirs_exist_ok=True)
                if (runtime_tcl / "init.tcl").exists() and (runtime_tk / "tk.tcl").exists():
                    tcl_dir = runtime_tcl
                    tk_dir = runtime_tk
                    break
            except Exception:
                continue
    if tcl_dir.exists():
        os.environ["TCL_LIBRARY"] = _short_windows_path(tcl_dir)
    if tk_dir.exists():
        os.environ["TK_LIBRARY"] = _short_windows_path(tk_dir)


_prepare_frozen_tcl_tk()

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

try:
    import customtkinter as ctk
except Exception:  # pragma: no cover
    ctk = None

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    pystray = None
    Image = None
    ImageDraw = None

from app_info import APP_NAME, APP_TITLE, APP_VERSION, GITHUB_RELEASES_PAGE
from config_utils import (
    TIMEZONE_EXAMPLES,
    autostart_enabled,
    default_config,
    deep_get,
    ensure_example_copied_if_missing,
    format_startup_self_check,
    get_app_base_dir,
    get_execution_mode,
    get_runtime_data_dir,
    is_valid_timezone_name,
    load_json_config,
    materialize_api_key,
    run_startup_self_check,
    save_json_config,
    set_windows_autostart,
    startup_check_summary,
    timezone_error_message,
    validate_config,
)
from tracker_runner import TrackerRunner
from update_manager import (
    ReleaseAsset,
    UpdateInfo,
    choose_download_asset,
    download_asset,
    fetch_latest_release,
    format_bytes,
    launch_update_applier,
    validate_portable_update_package,
)


APP_BG = "#0b1020"
CARD_BG = "#121a2f"
CARD_ALT_BG = "#10182b"
INPUT_BG = "#0d1528"
FOOTER_BG = "#090e1b"
HEADER_FG = "#ffffff"
SUBTEXT_FG = "#9baacf"
BORDER_FG = "#263353"
ACCENT_BG = "#2f6fb3"
ACCENT_HOVER_BG = "#3d82cf"
STATUS_OK = "#7ee787"
STATUS_RUN = "#f2cc60"
STATUS_ERR = "#ff9b9b"
STATUS_IDLE = "#9baacf"
CUSTOM_TK_AVAILABLE = ctk is not None
AppRoot = ctk.CTk if CUSTOM_TK_AVAILABLE else tk.Tk
DialogWindow = ctk.CTkToplevel if CUSTOM_TK_AVAILABLE else tk.Toplevel
FALLBACK_FONT_FAMILY = "Segoe UI"
BODY_FONT_CANDIDATES = ("Exo 2", FALLBACK_FONT_FAMILY)
DISPLAY_FONT_CANDIDATES = ("Russo One", "Exo 2", FALLBACK_FONT_FAMILY)
UI_FONT_FAMILY = FALLBACK_FONT_FAMILY
DISPLAY_FONT_FAMILY = FALLBACK_FONT_FAMILY
APP_FONT_DIR = "assets/fonts"
_FONT_SYSTEM_READY = False
_LOADED_FONT_PATHS: set[Path] = set()

if CUSTOM_TK_AVAILABLE:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def ui_font(size: int, weight: str = "normal", *, display: bool = False) -> tuple:
    family = DISPLAY_FONT_FAMILY if display else UI_FONT_FAMILY
    return (family, size) if weight == "normal" else (family, size, weight)


def _font_roots(base_dir: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if base_dir is not None:
        roots.append(base_dir / APP_FONT_DIR)
        roots.append(base_dir / "fonts")
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        roots.append(bundle_root / APP_FONT_DIR)
        roots.append(Path(sys.executable).resolve().parent / APP_FONT_DIR)
    roots.append(Path(__file__).resolve().parent / APP_FONT_DIR)
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def load_app_fonts(base_dir: Path | None = None) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        add_font = ctypes.windll.gdi32.AddFontResourceExW
    except Exception:
        return
    for root in _font_roots(base_dir):
        if not root.exists():
            continue
        for font_path in sorted(root.glob("*")):
            if font_path.suffix.lower() not in {".ttf", ".otf"} or font_path in _LOADED_FONT_PATHS:
                continue
            try:
                if add_font(str(font_path), 0x10, 0):
                    _LOADED_FONT_PATHS.add(font_path)
            except Exception:
                continue


def configure_font_system(widget: tk.Misc, base_dir: Path | None = None) -> None:
    global UI_FONT_FAMILY, DISPLAY_FONT_FAMILY, _FONT_SYSTEM_READY
    if _FONT_SYSTEM_READY:
        return
    load_app_fonts(base_dir)
    try:
        available = set(tkfont.families(widget))
    except Exception:
        available = set()
    UI_FONT_FAMILY = next((name for name in BODY_FONT_CANDIDATES if name in available), FALLBACK_FONT_FAMILY)
    DISPLAY_FONT_FAMILY = next((name for name in DISPLAY_FONT_CANDIDATES if name in available), UI_FONT_FAMILY)
    _FONT_SYSTEM_READY = True


def _local_display_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone()


def _matching_now(value: datetime, now: datetime | None = None) -> datetime:
    if now is not None:
        if value.tzinfo is not None and now.tzinfo is None:
            return now.astimezone()
        if value.tzinfo is None and now.tzinfo is not None:
            return now.replace(tzinfo=None)
        return now
    return datetime.now(value.tzinfo) if value.tzinfo is not None else datetime.now()


def _relative_past_label(seconds: int) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {minutes % 60:02d}m ago"
    days = hours // 24
    return f"{days}d ago"


def format_elapsed_time(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "Never"
    local_value = _local_display_datetime(value)
    current = _matching_now(local_value, now)
    seconds = int((current - local_value).total_seconds())
    if seconds < -30:
        return f"in {abs(seconds) // 60 + 1} min · {local_value.strftime('%Y-%m-%d %H:%M:%S')}"
    return f"{_relative_past_label(seconds)} · {local_value.strftime('%Y-%m-%d %H:%M:%S')}"


def format_next_run_time(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "Not scheduled"
    local_value = _local_display_datetime(value)
    current = _matching_now(local_value, now)
    seconds = int((local_value - current).total_seconds())
    if seconds <= 0:
        prefix = "due now"
    else:
        minutes, rem_seconds = divmod(seconds, 60)
        hours, rem_minutes = divmod(minutes, 60)
        if hours:
            prefix = f"in {hours}h {rem_minutes:02d}m"
        else:
            prefix = f"in {rem_minutes}m {rem_seconds:02d}s"
    return f"{prefix} · {local_value.strftime('%Y-%m-%d %H:%M:%S')}"


def apply_desktop_theme(widget: tk.Misc) -> None:
    configure_font_system(widget)
    try:
        widget.option_add("*Font", ui_font(11))
    except tk.TclError:
        pass
    if CUSTOM_TK_AVAILABLE:
        ctk.set_appearance_mode("dark")
    style = ttk.Style(widget)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", background=APP_BG, foreground=HEADER_FG, font=ui_font(11))
    style.configure("TFrame", background=APP_BG, borderwidth=0)
    style.configure("Card.TFrame", background=CARD_BG, borderwidth=0)
    style.configure("TLabel", background=APP_BG, foreground=HEADER_FG)
    style.configure("Muted.TLabel", background=APP_BG, foreground=SUBTEXT_FG)
    style.configure("Card.TLabel", background=CARD_BG, foreground=HEADER_FG)
    style.configure("CardMuted.TLabel", background=CARD_BG, foreground=SUBTEXT_FG)
    style.configure(
        "TEntry",
        fieldbackground=INPUT_BG,
        foreground=HEADER_FG,
        insertcolor=HEADER_FG,
        bordercolor=BORDER_FG,
        lightcolor=BORDER_FG,
        darkcolor=BORDER_FG,
        padding=6,
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", "#111827"), ("!disabled", INPUT_BG)],
        foreground=[("disabled", "#6f7c93"), ("!disabled", HEADER_FG)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=INPUT_BG,
        background=INPUT_BG,
        foreground=HEADER_FG,
        arrowcolor=SUBTEXT_FG,
        bordercolor=BORDER_FG,
        lightcolor=BORDER_FG,
        darkcolor=BORDER_FG,
        padding=5,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", INPUT_BG), ("disabled", "#111827")],
        foreground=[("disabled", "#6f7c93"), ("!disabled", HEADER_FG)],
    )
    style.configure("TCheckbutton", background=APP_BG, foreground=HEADER_FG, focuscolor=APP_BG)
    style.configure("TRadiobutton", background=APP_BG, foreground=HEADER_FG, focuscolor=APP_BG)
    style.configure("Card.TCheckbutton", background=CARD_BG, foreground=HEADER_FG, focuscolor=CARD_BG)
    style.configure("Card.TRadiobutton", background=CARD_BG, foreground=HEADER_FG, focuscolor=CARD_BG)
    style.map(
        "TCheckbutton",
        background=[("active", APP_BG), ("!active", APP_BG)],
        foreground=[("disabled", "#6f7c93"), ("!disabled", HEADER_FG)],
    )
    style.map(
        "TRadiobutton",
        background=[("active", APP_BG), ("!active", APP_BG)],
        foreground=[("disabled", "#6f7c93"), ("!disabled", HEADER_FG)],
    )
    style.map(
        "Card.TCheckbutton",
        background=[("active", CARD_BG), ("!active", CARD_BG)],
        foreground=[("disabled", "#6f7c93"), ("!disabled", HEADER_FG)],
    )
    style.map(
        "Card.TRadiobutton",
        background=[("active", CARD_BG), ("!active", CARD_BG)],
        foreground=[("disabled", "#6f7c93"), ("!disabled", HEADER_FG)],
    )
    style.configure("TNotebook", background=APP_BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure(
        "TNotebook.Tab",
        background=CARD_ALT_BG,
        foreground=SUBTEXT_FG,
        padding=(12, 8),
        bordercolor=BORDER_FG,
        lightcolor=BORDER_FG,
        darkcolor=BORDER_FG,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", CARD_BG), ("active", "#17233f")],
        foreground=[("selected", HEADER_FG), ("active", HEADER_FG), ("!selected", SUBTEXT_FG)],
    )
    style.configure("Primary.TButton", padding=(16, 10), font=ui_font(13, "bold"))
    style.configure("Secondary.TButton", padding=(14, 10), font=ui_font(12))
    style.map(
        "Primary.TButton",
        foreground=[("disabled", "#7d8795"), ("!disabled", "#ffffff")],
        background=[("disabled", "#202838"), ("active", ACCENT_HOVER_BG), ("!disabled", ACCENT_BG)],
    )
    style.map(
        "Secondary.TButton",
        foreground=[("disabled", "#7d8795"), ("!disabled", "#f4f6f8")],
        background=[("disabled", "#202838"), ("active", "#1b2947"), ("!disabled", "#17233f")],
    )
    style.configure(
        "Horizontal.TProgressbar",
        background=ACCENT_BG,
        troughcolor=INPUT_BG,
        bordercolor=BORDER_FG,
        lightcolor=ACCENT_BG,
        darkcolor=ACCENT_BG,
    )


def set_surface_color(widget: tk.Misc, color: str) -> None:
    if CUSTOM_TK_AVAILABLE and isinstance(widget, (ctk.CTk, ctk.CTkToplevel)):
        widget.configure(fg_color=color)
    else:
        widget.configure(bg=color)


def make_button(parent: tk.Misc, text: str, command, *, kind: str = "secondary", state: str = "normal"):
    if CUSTOM_TK_AVAILABLE:
        is_primary = kind == "primary"
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            state=state,
            width=120,
            height=42,
            corner_radius=8,
            border_width=0 if is_primary else 1,
            border_color=BORDER_FG,
            fg_color=ACCENT_BG if is_primary else CARD_ALT_BG,
            hover_color=ACCENT_HOVER_BG if is_primary else "#1b2947",
            text_color="#ffffff" if is_primary else "#f4f6f8",
            font=ui_font(13 if is_primary else 12, "bold" if is_primary else "normal"),
        )
    style = "Primary.TButton" if kind == "primary" else "Secondary.TButton"
    return ttk.Button(parent, text=text, command=command, state=state, style=style)


def make_entry(parent: tk.Misc, variable: tk.StringVar, *, width: int | None = None, show: str | None = None, justify: str | None = None):
    if CUSTOM_TK_AVAILABLE:
        pixel_width = max(48, int(width * 9)) if width else 160
        return ctk.CTkEntry(
            parent,
            textvariable=variable,
            width=pixel_width,
            height=40,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_FG,
            fg_color=INPUT_BG,
            text_color=HEADER_FG,
            placeholder_text_color=SUBTEXT_FG,
            show=show,
            justify=justify or "left",
            font=ui_font(12),
        )
    options = {"textvariable": variable}
    if width is not None:
        options["width"] = width
    if show is not None:
        options["show"] = show
    if justify is not None:
        options["justify"] = justify
    return ttk.Entry(parent, **options)


def make_combobox(parent: tk.Misc, variable: tk.StringVar, values: list[str], *, width: int = 14):
    if CUSTOM_TK_AVAILABLE:
        return ctk.CTkComboBox(
            parent,
            variable=variable,
            values=values,
            state="readonly",
            width=max(120, width * 9),
            height=40,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_FG,
            fg_color=INPUT_BG,
            button_color=CARD_ALT_BG,
            button_hover_color="#1b2947",
            dropdown_fg_color=INPUT_BG,
            dropdown_hover_color="#1b2947",
            dropdown_text_color=HEADER_FG,
            text_color=HEADER_FG,
            font=ui_font(12),
            dropdown_font=ui_font(12),
        )
    return ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=width)


def make_checkbox(parent: tk.Misc, text: str, variable: tk.BooleanVar):
    if CUSTOM_TK_AVAILABLE:
        return ctk.CTkCheckBox(
            parent,
            text=text,
            variable=variable,
            corner_radius=5,
            border_width=2,
            fg_color=ACCENT_BG,
            hover_color=ACCENT_HOVER_BG,
            border_color=BORDER_FG,
            text_color=HEADER_FG,
            font=ui_font(12),
        )
    return ttk.Checkbutton(parent, text=text, variable=variable, style="Card.TCheckbutton")


def make_radio(parent: tk.Misc, text: str, variable: tk.StringVar, value: str, command=None):
    if CUSTOM_TK_AVAILABLE:
        return ctk.CTkRadioButton(
            parent,
            text=text,
            variable=variable,
            value=value,
            command=command,
            radiobutton_width=17,
            radiobutton_height=17,
            border_width_checked=5,
            border_width_unchecked=2,
            fg_color=ACCENT_BG,
            hover_color=ACCENT_HOVER_BG,
            border_color=BORDER_FG,
            text_color=HEADER_FG,
            font=ui_font(12),
        )
    return ttk.Radiobutton(parent, text=text, variable=variable, value=value, command=command, style="Card.TRadiobutton")


def make_text_area(parent: tk.Misc, *, height: int = 160, wrap: str = "word"):
    if CUSTOM_TK_AVAILABLE:
        return ctk.CTkTextbox(
            parent,
            height=height,
            wrap=wrap,
            fg_color=INPUT_BG,
            text_color=HEADER_FG,
            border_width=0,
            corner_radius=8,
            scrollbar_button_color=CARD_ALT_BG,
            scrollbar_button_hover_color=ACCENT_HOVER_BG,
            font=ui_font(12),
        )
    return ScrolledText(
        parent,
        height=max(6, height // 22),
        wrap=wrap,
        bg=INPUT_BG,
        fg=HEADER_FG,
        insertbackground=HEADER_FG,
        font=ui_font(12),
        relief="flat",
        borderwidth=0,
    )


def make_dialog_header(parent: tk.Misc, title: str, subtitle: str):
    if CUSTOM_TK_AVAILABLE:
        header = ctk.CTkFrame(parent, fg_color=APP_BG, corner_radius=0)
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text=title,
            text_color=HEADER_FG,
            font=ui_font(24, "bold", display=True),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text=f"v{APP_VERSION} / {get_execution_mode()}",
            fg_color=CARD_ALT_BG,
            text_color=SUBTEXT_FG,
            font=ui_font(11, "bold"),
            corner_radius=8,
            padx=10,
            pady=4,
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            header,
            text=subtitle,
            text_color=SUBTEXT_FG,
            font=ui_font(12),
            anchor="w",
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        return header

    header = tk.Frame(parent, bg=APP_BG, padx=4, pady=4)
    header.grid_columnconfigure(0, weight=1)
    tk.Label(header, text=title, bg=APP_BG, fg=HEADER_FG, font=ui_font(24, "bold", display=True)).grid(row=0, column=0, sticky="w")
    tk.Label(
        header,
        text=f"v{APP_VERSION} / {get_execution_mode()}",
        bg=CARD_ALT_BG,
        fg=SUBTEXT_FG,
        font=ui_font(11, "bold"),
        padx=10,
        pady=4,
    ).grid(row=0, column=1, sticky="e")
    tk.Label(
        header,
        text=subtitle,
        bg=APP_BG,
        fg=SUBTEXT_FG,
        font=ui_font(12),
        justify="left",
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
    return header


def make_panel(parent: tk.Misc, title: str | None = None, *, padx: int = 16, pady: int = 16):
    if CUSTOM_TK_AVAILABLE:
        panel = ctk.CTkFrame(
            parent,
            fg_color=CARD_BG,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_FG,
        )
        if title:
            ctk.CTkLabel(
                panel,
                text=title,
                text_color=SUBTEXT_FG,
                font=ui_font(11, "bold"),
                anchor="w",
            ).pack(anchor="w", padx=padx, pady=(pady, 0))
        return panel

    panel = tk.Frame(parent, bg=CARD_BG, padx=padx, pady=pady, highlightthickness=1, highlightbackground=BORDER_FG)
    if title:
        tk.Label(panel, text=title, bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(11, "bold")).pack(anchor="w")
    return panel


def make_metric_tile(parent: tk.Misc, label: str, var: tk.StringVar, row: int, column: int, *, wraplength: int = 220):
    if CUSTOM_TK_AVAILABLE:
        tile = ctk.CTkFrame(
            parent,
            fg_color=CARD_ALT_BG,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_FG,
        )
        tile.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(
            tile,
            text=label,
            text_color=SUBTEXT_FG,
            font=ui_font(10, "bold"),
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(
            tile,
            textvariable=var,
            text_color=HEADER_FG,
            font=ui_font(12),
            wraplength=wraplength,
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(4, 10))
        return tile

    tile = tk.Frame(parent, bg=CARD_ALT_BG, padx=12, pady=10, highlightthickness=1, highlightbackground=BORDER_FG)
    tile.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
    tk.Label(tile, text=label, bg=CARD_ALT_BG, fg=SUBTEXT_FG, font=ui_font(10, "bold")).pack(anchor="w")
    tk.Label(
        tile,
        textvariable=var,
        bg=CARD_ALT_BG,
        fg=HEADER_FG,
        font=ui_font(12),
        wraplength=wraplength,
        justify="left",
    ).pack(anchor="w", pady=(4, 0))
    return tile


def extract_post_id(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    for pattern in (r"/posts/(\d+)", r"[?&]postId=(\d+)"):
        match = re.search(pattern, value)
        if match:
            return int(match.group(1))
    return None


class SingleInstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            if sys.platform.startswith("win"):
                import msvcrt

                self.handle.seek(0)
                if not self.handle.read(1):
                    self.handle.write("0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(str(os.getpid()))
            self.handle.flush()
            return True
        except OSError:
            self.release()
            return False

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if sys.platform.startswith("win"):
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self.handle.close()
        finally:
            self.handle = None


def show_already_running_message(runtime_dir: Path) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "CivitAI Tracker",
        "CivitAI Tracker is already running for this folder.\n\n"
        f"{runtime_dir}\n\n"
        "Open it from the tray icon, or use a separate folder for another account.",
        parent=root,
    )
    root.destroy()


class SettingsDialog(DialogWindow):
    def __init__(self, master: tk.Misc, base_dir: Path, config: dict, on_save):
        super().__init__(master)
        self.title("CivitAI Tracker Settings")
        self.geometry("940x820")
        self.minsize(900, 760)
        self.base_dir = base_dir
        self.on_save = on_save

        self.username_var = tk.StringVar(value=deep_get(config, "profile.username", ""))
        self.display_name_var = tk.StringVar(value=deep_get(config, "profile.display_name", ""))
        self.timezone_var = tk.StringVar(value=deep_get(config, "profile.timezone", "UTC"))

        auth_inline = bool(deep_get(config, "auth.api_key", ""))
        self.api_storage_var = tk.StringVar(value="inline" if auth_inline else "file")
        self.api_key_var = tk.StringVar(value=deep_get(config, "auth.api_key", ""))
        self.api_key_file_var = tk.StringVar(value=deep_get(config, "auth.api_key_file", "api_key.txt"))

        self.start_mode_var = tk.StringVar(value=deep_get(config, "tracking.start_mode", "post_id"))
        self.start_post_var = tk.StringVar(value=str(deep_get(config, "tracking.start_post_id", "") or ""))
        iso_date = str(deep_get(config, "tracking.start_date", "") or "")
        self.start_day_var = tk.StringVar()
        self.start_month_var = tk.StringVar()
        self.start_year_var = tk.StringVar()
        if iso_date:
            try:
                y, m, d = iso_date.split("-")
                self.start_day_var.set(d)
                self.start_month_var.set(m)
                self.start_year_var.set(y)
            except Exception:
                pass
        self.poll_minutes_var = tk.StringVar(value=str(deep_get(config, "tracking.poll_minutes", 15)))

        self.api_mode_var = tk.StringVar(value=deep_get(config, "api.mode", "red"))
        self.view_host_var = tk.StringVar(value=deep_get(config, "api.view_host", "https://civitai.red"))
        self.nsfw_var = tk.StringVar(value=deep_get(config, "api.nsfw_level", "X"))
        self.allow_rest_var = tk.BooleanVar(value=bool(deep_get(config, "options.allow_rest_fallback", False)))
        self.launch_with_windows_var = tk.BooleanVar(value=bool(deep_get(config, "options.launch_with_windows", False) or autostart_enabled()))
        self.start_minimized_var = tk.BooleanVar(value=bool(deep_get(config, "options.start_minimized", False)))
        self.start_auto_polling_on_launch_var = tk.BooleanVar(value=bool(deep_get(config, "options.start_auto_polling_on_launch", False)))
        self.check_updates_on_launch_var = tk.BooleanVar(value=bool(deep_get(config, "options.check_updates_on_launch", True)))

        self.db_var = tk.StringVar(value=deep_get(config, "paths.db", "civitai_tracker.db"))
        self.csv_var = tk.StringVar(value=deep_get(config, "paths.csv_dir", "csv"))
        self.html_var = tk.StringVar(value=deep_get(config, "paths.html", "dashboard.html"))

        self.status_var = tk.StringVar(value="Review settings, then save changes.")

        self._build()
        self._toggle_auth_mode()
        self._toggle_start_mode()
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self):
        set_surface_color(self, APP_BG)
        apply_desktop_theme(self)
        container = tk.Frame(self, bg=APP_BG, padx=18, pady=18)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        header = make_dialog_header(
            container,
            "Settings",
            "Configure profile, access, tracking, output, and desktop behavior.",
        )
        header.grid(row=0, column=0, sticky="ew")

        if CUSTOM_TK_AVAILABLE:
            notebook = ctk.CTkTabview(
                container,
                fg_color=CARD_BG,
                segmented_button_fg_color=CARD_ALT_BG,
                segmented_button_selected_color=ACCENT_BG,
                segmented_button_selected_hover_color=ACCENT_HOVER_BG,
                segmented_button_unselected_color=CARD_ALT_BG,
                segmented_button_unselected_hover_color="#1b2947",
                text_color=HEADER_FG,
                corner_radius=12,
                border_width=1,
                border_color=BORDER_FG,
            )
        else:
            notebook = ttk.Notebook(container)
        notebook.grid(row=1, column=0, sticky="nsew", pady=(16, 0))

        self.profile_tab = self._make_tab(notebook, "Profile")
        self.auth_tab = self._make_tab(notebook, "Access")
        self.tracking_tab = self._make_tab(notebook, "Tracking")
        self.api_tab = self._make_tab(notebook, "CivitAI")
        self.output_tab = self._make_tab(notebook, "Files")
        self.app_tab = self._make_tab(notebook, "App")
        if CUSTOM_TK_AVAILABLE:
            try:
                notebook._segmented_button.configure(font=ui_font(12))
            except Exception:
                pass

        self._build_profile_tab()
        self._build_auth_tab()
        self._build_tracking_tab()
        self._build_api_tab()
        self._build_output_tab()
        self._build_app_tab()

        footer = tk.Frame(container, bg=APP_BG)
        footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        tk.Label(footer, textvariable=self.status_var, bg=APP_BG, fg=SUBTEXT_FG, font=ui_font(12), wraplength=620, justify="left").grid(row=0, column=0, sticky="w")
        buttons = tk.Frame(footer, bg=APP_BG)
        buttons.grid(row=0, column=1, sticky="e")
        make_button(buttons, "Cancel", self.destroy).pack(side="left", padx=(0, 8))
        make_button(buttons, "Save", self._save, kind="primary").pack(side="left")

    def _make_tab(self, notebook, title: str) -> tk.Frame:
        if CUSTOM_TK_AVAILABLE:
            tab = notebook.add(title)
            tab.configure(fg_color=CARD_BG)
            frame = ctk.CTkScrollableFrame(
                tab,
                fg_color=CARD_BG,
                corner_radius=0,
                scrollbar_button_color=CARD_ALT_BG,
                scrollbar_button_hover_color="#1b2947",
            )
            frame.pack(fill="both", expand=True, padx=18, pady=18)
        else:
            frame = tk.Frame(notebook, bg=CARD_BG, padx=18, pady=18, highlightthickness=1, highlightbackground=BORDER_FG)
            notebook.add(frame, text=title)
        frame.columnconfigure(1, weight=1)
        return frame

    def _add_section_intro(self, parent: tk.Frame, row: int, title: str, text: str) -> int:
        tk.Label(parent, text=title, bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(11, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        tk.Label(parent, text=text, bg=CARD_BG, fg=HEADER_FG, font=ui_font(16, "bold"), justify="left", wraplength=680).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 16))
        return row + 1

    def _add_help(self, parent: tk.Frame, row: int, text: str) -> int:
        tk.Label(parent, text=text, bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12), wraplength=560, justify="left").grid(row=row, column=1, sticky="w", pady=(0, 14))
        return row + 1

    def _add_entry_row(self, parent: tk.Frame, row: int, label: str, variable: tk.StringVar, *, width: int = 40, help_text: str | None = None):
        tk.Label(parent, text=label, bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        entry = make_entry(parent, variable, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=(0, 5))
        row += 1
        if help_text:
            row = self._add_help(parent, row, help_text)
        return entry, row

    def _build_profile_tab(self):
        row = 0
        row = self._add_section_intro(self.profile_tab, row, "PROFILE", "Creator identity and local timezone used for dashboard timestamps.")
        _, row = self._add_entry_row(self.profile_tab, row, "Username", self.username_var, help_text="Your public CivitAI username.")
        _, row = self._add_entry_row(self.profile_tab, row, "Display name", self.display_name_var, help_text="Optional friendly name used inside the app.")

        tk.Label(self.profile_tab, text="Timezone", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        tz_row = tk.Frame(self.profile_tab, bg=CARD_BG)
        tz_row.grid(row=row, column=1, sticky="ew", pady=(0, 5))
        tz_row.columnconfigure(0, weight=1)
        self.timezone_entry = make_entry(tz_row, self.timezone_var)
        self.timezone_entry.grid(row=0, column=0, sticky="ew")
        make_button(tz_row, "Examples", self._show_timezone_examples).grid(row=0, column=1, padx=(8, 0))
        row += 1
        self._add_help(self.profile_tab, row, "Use IANA timezone format, for example Europe/Moscow or America/New_York.")

    def _build_auth_tab(self):
        row = 0
        row = self._add_section_intro(self.auth_tab, row, "ACCESS", "A CivitAI key is optional. Without it, the tracker stays in limited public mode.")
        tk.Label(self.auth_tab, text="Key storage", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        mode_row = tk.Frame(self.auth_tab, bg=CARD_BG)
        mode_row.grid(row=row, column=1, sticky="w", pady=(0, 5))
        make_radio(mode_row, "Store inside config", self.api_storage_var, "inline", self._toggle_auth_mode).pack(side="left")
        make_radio(mode_row, "Store in file", self.api_storage_var, "file", self._toggle_auth_mode).pack(side="left", padx=(14, 0))
        row += 1
        row = self._add_help(self.auth_tab, row, "File mode is safer when you share or back up configs. The app will create and update the key file automatically.")

        tk.Label(self.auth_tab, text="API key", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        self.api_key_entry = make_entry(self.auth_tab, self.api_key_var, show="•")
        self.api_key_entry.grid(row=row, column=1, sticky="ew", pady=(0, 5))
        row += 1

        tk.Label(self.auth_tab, text="Key file", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        file_row = tk.Frame(self.auth_tab, bg=CARD_BG)
        file_row.grid(row=row, column=1, sticky="ew", pady=(0, 5))
        file_row.columnconfigure(0, weight=1)
        self.api_key_file_entry = make_entry(file_row, self.api_key_file_var)
        self.api_key_file_entry.grid(row=0, column=0, sticky="ew")
        make_button(file_row, "Browse", self._browse_key_file).grid(row=0, column=1, padx=(8, 0))
        row += 1

        self.auth_help_label = tk.Label(self.auth_tab, text="", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12), wraplength=560, justify="left")
        self.auth_help_label.grid(row=row, column=1, sticky="w", pady=(0, 10))

    def _build_tracking_tab(self):
        row = 0
        row = self._add_section_intro(self.tracking_tab, row, "TRACKING WINDOW", "Choose where history starts and how often auto polling checks for new data.")
        tk.Label(self.tracking_tab, text="Start mode", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        mode_row = tk.Frame(self.tracking_tab, bg=CARD_BG)
        mode_row.grid(row=row, column=1, sticky="w", pady=(0, 5))
        make_radio(mode_row, "Post ID or URL", self.start_mode_var, "post_id", self._toggle_start_mode).pack(side="left")
        make_radio(mode_row, "Date", self.start_mode_var, "date", self._toggle_start_mode).pack(side="left", padx=(14, 0))
        row += 1

        tk.Label(self.tracking_tab, text="Start post", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        self.start_post_entry = make_entry(self.tracking_tab, self.start_post_var)
        self.start_post_entry.grid(row=row, column=1, sticky="ew", pady=(0, 5))
        row += 1
        row = self._add_help(self.tracking_tab, row, "Paste a post ID or a full post URL.")

        tk.Label(self.tracking_tab, text="Start date", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        date_row = tk.Frame(self.tracking_tab, bg=CARD_BG)
        date_row.grid(row=row, column=1, sticky="w", pady=(0, 5))
        self.start_day_entry = make_entry(date_row, self.start_day_var, width=4, justify="center")
        self.start_day_entry.pack(side="left")
        tk.Label(date_row, text="/", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12)).pack(side="left", padx=5)
        self.start_month_entry = make_entry(date_row, self.start_month_var, width=4, justify="center")
        self.start_month_entry.pack(side="left")
        tk.Label(date_row, text="/", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12)).pack(side="left", padx=5)
        self.start_year_entry = make_entry(date_row, self.start_year_var, width=6, justify="center")
        self.start_year_entry.pack(side="left")
        tk.Label(date_row, text="DD / MM / YYYY", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12)).pack(side="left", padx=(10, 0))
        row += 1
        row = self._add_help(self.tracking_tab, row, "Stored internally as YYYY-MM-DD.")

        _, row = self._add_entry_row(
            self.tracking_tab,
            row,
            "Poll interval",
            self.poll_minutes_var,
            width=8,
            help_text="How often the app checks CivitAI while auto polling is enabled.",
        )

    def _build_api_tab(self):
        row = 0
        row = self._add_section_intro(self.api_tab, row, "CIVITAI", "Choose which CivitAI host and visibility level the tracker should use.")
        tk.Label(self.api_tab, text="Content host", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        make_combobox(self.api_tab, self.api_mode_var, ["red", "auto", "com"], width=14).grid(row=row, column=1, sticky="w", pady=(0, 5))
        row += 1
        row = self._add_help(self.api_tab, row, "Use 'red' for full visibility, including content above PG-13.")
        _, row = self._add_entry_row(self.api_tab, row, "Open links on", self.view_host_var, help_text="Used for links opened from the app and dashboard.")

        tk.Label(self.api_tab, text="Visibility", bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 6), padx=(0, 14))
        make_combobox(self.api_tab, self.nsfw_var, ["None", "Soft", "Mature", "X"], width=14).grid(row=row, column=1, sticky="w", pady=(0, 5))
        row += 1
        make_checkbox(self.api_tab, "Try alternate image lookup when previews are missing", self.allow_rest_var).grid(row=row, column=1, sticky="w", pady=(6, 0))

    def _build_output_tab(self):
        row = 0
        row = self._add_section_intro(self.output_tab, row, "LOCAL FILES", "Local files generated and maintained by the tracker.")
        _, row = self._add_entry_row(self.output_tab, row, "History database", self.db_var, help_text="SQLite file used to store snapshots and history.")
        _, row = self._add_entry_row(self.output_tab, row, "CSV export folder", self.csv_var, help_text="Folder where CSV exports are generated.")
        _, row = self._add_entry_row(self.output_tab, row, "Dashboard file", self.html_var, help_text="The local HTML dashboard generated by the tracker.")

    def _build_app_tab(self):
        row = 0
        row = self._add_section_intro(self.app_tab, row, "DESKTOP BEHAVIOR", "Startup, tray, and update-check behavior for this local copy.")
        make_checkbox(self.app_tab, "Launch with Windows", self.launch_with_windows_var).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        row = self._add_help(self.app_tab, row, "Start the app automatically when Windows starts.")
        make_checkbox(self.app_tab, "Start minimized to tray", self.start_minimized_var).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        row = self._add_help(self.app_tab, row, "Launch hidden and stay in the system tray until opened.")
        make_checkbox(self.app_tab, "Start auto polling on launch", self.start_auto_polling_on_launch_var).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        row = self._add_help(self.app_tab, row, "Automatically start background polling when the app launches. Recommended for startup and tray-only use.")
        make_checkbox(self.app_tab, "Check for updates on launch", self.check_updates_on_launch_var).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        row = self._add_help(self.app_tab, row, "Check GitHub releases in the background when the app starts.")
        self._add_help(self.app_tab, row, "Closing the window sends the app to the tray. Use Exit app or the tray menu to exit fully.")

    def _show_timezone_examples(self):
        messagebox.showinfo(
            "Timezone examples",
            "Use an IANA timezone name, for example\n\n" + "\n".join(TIMEZONE_EXAMPLES),
            parent=self,
        )

    def _browse_key_file(self):
        path = filedialog.asksaveasfilename(
            title="Select API key file",
            defaultextension=".txt",
            initialfile=self.api_key_file_var.get() or "api_key.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            try:
                path = str(Path(path).resolve().relative_to(self.base_dir.resolve()))
            except Exception:
                path = str(Path(path).resolve())
            self.api_key_file_var.set(path)

    def _toggle_auth_mode(self):
        use_inline = self.api_storage_var.get() == "inline"
        self.api_key_entry.configure(state=("normal" if use_inline else "normal"))
        self.api_key_file_entry.configure(state=("disabled" if use_inline else "normal"))
        if use_inline:
            self.auth_help_label.configure(text="The key will be stored inside config.json on this machine.")
        else:
            self.auth_help_label.configure(text="The app will create and update the external key file automatically when you save.")

    def _toggle_start_mode(self):
        use_date = self.start_mode_var.get() == "date"
        self.start_post_entry.configure(state=("disabled" if use_date else "normal"))
        state = ("normal" if use_date else "disabled")
        self.start_day_entry.configure(state=state)
        self.start_month_entry.configure(state=state)
        self.start_year_entry.configure(state=state)

    def _validate_timezone(self) -> bool:
        timezone_name = self.timezone_var.get().strip() or "UTC"
        if is_valid_timezone_name(timezone_name):
            return True
        messagebox.showerror("Invalid timezone", timezone_error_message(), parent=self)
        return False

    def _save(self):
        if not self._validate_timezone():
            return

        cfg = default_config()
        cfg["profile"]["username"] = self.username_var.get().strip()
        cfg["profile"]["display_name"] = self.display_name_var.get().strip() or cfg["profile"]["username"]
        cfg["profile"]["timezone"] = self.timezone_var.get().strip() or "UTC"

        storage_mode = self.api_storage_var.get().strip()
        entered_key = self.api_key_var.get().strip()
        if storage_mode == "inline":
            cfg["auth"]["api_key"] = entered_key
            cfg["auth"]["api_key_file"] = ""
        else:
            cfg["auth"]["api_key"] = ""
            cfg["auth"]["api_key_file"] = self.api_key_file_var.get().strip() or "api_key.txt"

        cfg["tracking"]["start_mode"] = self.start_mode_var.get().strip()
        if cfg["tracking"]["start_mode"] == "post_id":
            post_value = self.start_post_var.get().strip()
            post_id = extract_post_id(post_value)
            cfg["tracking"]["start_post_id"] = post_id
            cfg["tracking"]["start_date"] = None
        else:
            cfg["tracking"]["start_post_id"] = None
            day = self.start_day_var.get().strip()
            month = self.start_month_var.get().strip()
            year = self.start_year_var.get().strip()
            if not (day and month and year):
                messagebox.showerror("Invalid date", "Please enter a valid start date using DD / MM / YYYY.", parent=self)
                return
            try:
                parsed = date(int(year), int(month), int(day))
            except ValueError:
                messagebox.showerror("Invalid date", "Please enter a valid calendar date.", parent=self)
                return
            cfg["tracking"]["start_date"] = parsed.strftime("%Y-%m-%d")

        try:
            cfg["tracking"]["poll_minutes"] = int(self.poll_minutes_var.get().strip())
        except Exception:
            cfg["tracking"]["poll_minutes"] = 15

        cfg["api"]["mode"] = self.api_mode_var.get().strip() or "red"
        cfg["api"]["view_host"] = self.view_host_var.get().strip() or "https://civitai.red"
        cfg["api"]["nsfw_level"] = self.nsfw_var.get().strip() or "X"

        cfg["paths"]["db"] = self.db_var.get().strip() or "civitai_tracker.db"
        cfg["paths"]["csv_dir"] = self.csv_var.get().strip() or "csv"
        cfg["paths"]["html"] = self.html_var.get().strip() or "dashboard.html"

        cfg["options"]["allow_rest_fallback"] = bool(self.allow_rest_var.get())
        cfg["options"]["launch_with_windows"] = bool(self.launch_with_windows_var.get())
        cfg["options"]["start_minimized"] = bool(self.start_minimized_var.get())
        cfg["options"]["start_auto_polling_on_launch"] = bool(self.start_auto_polling_on_launch_var.get())
        cfg["options"]["check_updates_on_launch"] = bool(self.check_updates_on_launch_var.get())

        errors = validate_config(cfg)
        if errors:
            messagebox.showerror("Invalid configuration", "\n".join(errors), parent=self)
            return

        save_json_config(cfg, self.base_dir / "config.json")
        materialize_api_key(cfg, entered_key if storage_mode == "file" else None, self.base_dir)
        set_windows_autostart(
            bool(self.launch_with_windows_var.get()),
            self.base_dir,
            start_minimized=bool(self.start_minimized_var.get()),
        )
        self.status_var.set("Settings saved successfully.")
        self.on_save(cfg)
        self.destroy()

class DiagnosticsDialog(DialogWindow):
    def __init__(self, master: tk.Misc, report: dict):
        super().__init__(master)
        self.title("Diagnostics")
        self.geometry("940x900")
        self.minsize(900, 840)
        self.report = report

        self._build()
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self):
        set_surface_color(self, APP_BG)
        apply_desktop_theme(self)
        wrapper = tk.Frame(self, bg=APP_BG, padx=18, pady=18)
        wrapper.pack(fill="both", expand=True)
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(3, weight=1, minsize=240)
        make_dialog_header(
            wrapper,
            "Diagnostics",
            "Startup checks, paths, configuration, and write access.",
        ).grid(row=0, column=0, sticky="ew")

        overview = make_panel(wrapper, "OVERVIEW")
        overview.grid(row=1, column=0, sticky="ew", pady=(16, 10))
        tk.Label(
            overview,
            text=startup_check_summary(self.report),
            bg=CARD_BG,
            fg=HEADER_FG,
            font=ui_font(18, "bold"),
            justify="left",
        ).pack(anchor="w", padx=16, pady=(6, 0))
        tk.Label(
            overview,
            text="Use the tiles below for a quick health check. The full technical report is kept in Details.",
            bg=CARD_BG,
            fg=SUBTEXT_FG,
            font=ui_font(12),
            justify="left",
            wraplength=760,
        ).pack(anchor="w", padx=16, pady=(4, 10))

        checks = make_panel(wrapper, "CHECKS")
        checks.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        grid = tk.Frame(checks, bg=CARD_BG)
        grid.pack(fill="x", padx=11, pady=(6, 10))
        grid.columnconfigure((0, 1, 2, 3), weight=1)
        self._build_check_tiles(grid)

        details = make_panel(wrapper, "DETAILS")
        details.grid(row=3, column=0, sticky="nsew")
        self.text = make_text_area(details, height=190)
        self.text.pack(fill="both", expand=True, padx=16, pady=(10, 16))
        self.text.insert("1.0", format_startup_self_check(self.report))
        self.text.configure(state="disabled")

        buttons = tk.Frame(wrapper, bg=APP_BG)
        buttons.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        make_button(buttons, "Copy to clipboard", self._copy).pack(side="left")
        make_button(buttons, "Close", self.destroy, kind="primary").pack(side="right")

    def _build_check_tiles(self, parent: tk.Frame):
        details = self.report.get("details", {})
        critical = int(self.report.get("critical_count", 0))
        warnings = int(self.report.get("warning_count", 0))
        if critical:
            startup_state = ("Critical", STATUS_ERR, f"{critical} critical / {warnings} warning")
        elif warnings:
            startup_state = ("Warning", STATUS_RUN, f"{warnings} warning(s)")
        else:
            startup_state = ("Passed", STATUS_OK, "No startup issues found")

        config_exists = details.get("config_exists")
        username_ready = details.get("username_configured")
        config_ok = bool(config_exists and username_ready)
        config_detail = "Saved config and username found" if config_ok else "Open Settings and save the missing profile data"

        rows = [
            ("Startup", *startup_state),
            ("Config", "Ready" if config_ok else "Needs setup", STATUS_OK if config_ok else STATUS_RUN, config_detail),
            self._bool_tile("Runtime folder", details.get("runtime_dir_writable"), "Writable", "Not writable", "App data folder"),
            self._bool_tile("Logs", details.get("logs_dir_writable"), "Writable", "Needs attention", "Log files folder"),
            self._bool_tile("Database", details.get("db_parent_writable"), "Writable", "Blocked", "Database folder"),
            self._bool_tile("Dashboard", details.get("dashboard_parent_writable"), "Writable", "Blocked", "Dashboard folder"),
            (
                "API key",
                "Available" if details.get("api_key_available") else "Limited mode",
                STATUS_OK if details.get("api_key_available") else STATUS_RUN,
                "Authenticated fetches enabled" if details.get("api_key_available") else "Limited public data",
            ),
        ]
        for idx, (label, value, color, detail) in enumerate(rows):
            self._make_check_tile(parent, label, value, color, detail, idx // 4, idx % 4)

    def _bool_tile(self, label: str, value, ok_label: str, fail_label: str, detail: str):
        if value is None:
            return (label, "Unknown", STATUS_IDLE, detail)
        if bool(value):
            return (label, ok_label, STATUS_OK, detail)
        return (label, fail_label, STATUS_ERR, detail)

    def _make_check_tile(self, parent: tk.Frame, label: str, value: str, color: str, detail: str, row: int, column: int):
        tile = tk.Frame(parent, bg=CARD_ALT_BG, padx=10, pady=7, highlightthickness=1, highlightbackground=BORDER_FG)
        tile.grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
        tk.Label(tile, text=label, bg=CARD_ALT_BG, fg=SUBTEXT_FG, font=ui_font(10, "bold")).pack(anchor="w")
        tk.Label(tile, text=value, bg=CARD_ALT_BG, fg=color, font=ui_font(15, "bold"), justify="left").pack(anchor="w", pady=(2, 0))
        tk.Label(tile, text=detail or "Not available", bg=CARD_ALT_BG, fg=SUBTEXT_FG, font=ui_font(10), justify="left", wraplength=190).pack(anchor="w", pady=(2, 0))

    def _copy(self):
        text = format_startup_self_check(self.report)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()


class UpdateDialog(DialogWindow):
    def __init__(self, master: tk.Misc, runtime_dir: Path, execution_mode: str, open_path):
        super().__init__(master)
        self.title("Updates")
        self.geometry("940x840")
        self.minsize(900, 780)
        self.runtime_dir = runtime_dir
        self.execution_mode = execution_mode
        self.open_path = open_path
        self.info: UpdateInfo | None = None
        self.asset: ReleaseAsset | None = None
        self.downloaded_path: Path | None = None
        self.downloaded_package_ready = False
        self.is_busy = False
        self._after_ids: set[str] = set()
        self.check_after_id = None

        self.status_var = tk.StringVar(value="Ready to check.")
        self.current_var = tk.StringVar(value=f"v{APP_VERSION}")
        self.latest_var = tk.StringVar(value="-")
        self.asset_var = tk.StringVar(value="-")
        self.source_var = tk.StringVar(value="Not checked")
        self.path_var = tk.StringVar(value="Check for updates to see the available path.")
        self.notes_hint_var = tk.StringVar(value="Release notes will appear after the check.")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="")

        self._build()
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.check_after_id = self._schedule(150, self.check_now)

    def _schedule(self, delay_ms: int, callback, *args):
        after_id = ""

        def dispatch():
            self._after_ids.discard(after_id)
            try:
                if self.winfo_exists():
                    callback(*args)
            except tk.TclError:
                pass

        after_id = self.after(delay_ms, dispatch)
        self._after_ids.add(after_id)
        return after_id

    def _cancel_scheduled_callbacks(self):
        for after_id in list(self._after_ids):
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
            self._after_ids.discard(after_id)

    def destroy(self):
        if hasattr(self, "_after_ids"):
            self._cancel_scheduled_callbacks()
        super().destroy()

    def _build(self):
        set_surface_color(self, APP_BG)
        apply_desktop_theme(self)
        shell = tk.Frame(self, bg=APP_BG)
        shell.pack(fill="both", expand=True)

        footer = tk.Frame(shell, bg=APP_BG, padx=18)
        footer.pack(side="bottom", fill="x", pady=(0, 18))

        wrapper = tk.Frame(shell, bg=APP_BG, padx=18, pady=18)
        wrapper.pack(side="top", fill="both", expand=True)
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(3, weight=1, minsize=240)

        header = make_dialog_header(
            wrapper,
            "Updates",
            "Check releases, download a portable package, and apply EXE updates.",
        )
        header.grid(row=0, column=0, sticky="ew")

        summary = tk.Frame(wrapper, bg=APP_BG)
        summary.grid(row=1, column=0, sticky="ew", pady=(16, 8))
        summary.columnconfigure((0, 1, 2, 3), weight=1)
        rows = [
            ("Current version", self.current_var),
            ("Latest release", self.latest_var),
            ("Package", self.asset_var),
            ("Status", self.status_var),
        ]
        for idx, (label, var) in enumerate(rows):
            make_metric_tile(summary, label, var, 0, idx, wraplength=170)

        path_panel = make_panel(wrapper, "UPDATE PATH")
        path_panel.grid(row=2, column=0, sticky="ew", pady=(4, 10))
        path_grid = tk.Frame(path_panel, bg=CARD_BG)
        path_grid.pack(fill="x", padx=11, pady=(10, 16))
        path_grid.columnconfigure((0, 1), weight=1)
        make_metric_tile(path_grid, "Package source", self.source_var, 0, 0, wraplength=300)
        make_metric_tile(path_grid, "Next action", self.path_var, 0, 1, wraplength=300)

        notes = make_panel(wrapper, "WHAT CHANGED")
        notes.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        notes.columnconfigure(0, weight=1)
        notes.rowconfigure(1, weight=1)
        tk.Label(notes, textvariable=self.notes_hint_var, bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12), justify="left", wraplength=720).pack(anchor="w", padx=16, pady=(10, 8))
        self.notes_text = make_text_area(notes, height=180)
        self.notes_text.pack(fill="both", expand=True, padx=16, pady=(10, 16))
        self._set_notes("Release notes will appear here after the check.")

        footer.columnconfigure(0, weight=1)
        progress_row = tk.Frame(footer, bg=APP_BG)
        progress_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        progress_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_row, variable=self.progress_var, maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        tk.Label(progress_row, textvariable=self.progress_text_var, bg=APP_BG, fg=SUBTEXT_FG, font=ui_font(11), width=18, anchor="e").grid(row=0, column=1, sticky="e")

        buttons = tk.Frame(footer, bg=APP_BG)
        buttons.grid(row=1, column=0, sticky="ew")
        self.check_btn = make_button(buttons, "Check now", self.check_now, kind="primary")
        self.release_btn = make_button(buttons, "Open release", self.open_release, state="disabled")
        self.download_btn = make_button(buttons, "Download package", self.download_update, state="disabled")
        self.select_btn = make_button(buttons, "Select ZIP", self.select_local_package)
        self.apply_btn = make_button(buttons, "Apply update", self.apply_update, kind="primary", state="disabled")
        self.downloads_btn = make_button(buttons, "Open downloads", self.open_downloads)
        self.close_btn = make_button(buttons, "Close", self._close)
        buttons.columnconfigure((0, 1, 2, 3), weight=1, uniform="update_actions")
        self.check_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=3)
        self.release_btn.grid(row=0, column=1, sticky="ew", padx=6, pady=3)
        self.download_btn.grid(row=0, column=2, sticky="ew", padx=6, pady=3)
        self.select_btn.grid(row=0, column=3, sticky="ew", padx=(6, 0), pady=3)
        self.apply_btn.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(6, 0))
        self.downloads_btn.grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        self.close_btn.grid(row=1, column=3, sticky="ew", padx=(6, 0), pady=(6, 0))

    def _close(self):
        self.destroy()

    def _set_update_path(self, source: str, path: str, hint: str | None = None):
        self.source_var.set(source)
        self.path_var.set(path)
        if hint is not None:
            self.notes_hint_var.set(hint)

    def _set_notes(self, text: str):
        self.notes_text.configure(state="normal")
        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", text)
        self.notes_text.configure(state="disabled")

    def _run_on_ui(self, callback, *args):
        try:
            self._schedule(0, self._dispatch_on_ui, callback, *args)
        except tk.TclError:
            pass

    def _dispatch_on_ui(self, callback, *args):
        try:
            if self.winfo_exists():
                callback(*args)
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool):
        self.is_busy = busy
        self.check_btn.configure(state="disabled" if busy else "normal")
        self.release_btn.configure(state="disabled" if busy or self.info is None else "normal")
        can_download = bool(self.asset is not None and self.info is not None and self.info.update_available)
        can_apply = bool(
            not busy
            and self.downloaded_path is not None
            and self.downloaded_package_ready
            and self.execution_mode == "frozen"
        )
        self.download_btn.configure(state="disabled" if busy or not can_download else "normal")
        self.apply_btn.configure(state="normal" if can_apply else "disabled")

    def check_now(self):
        if self.is_busy:
            return
        self.info = None
        self.asset = None
        self.downloaded_path = None
        self.downloaded_package_ready = False
        self.latest_var.set("Checking...")
        self.asset_var.set("-")
        self.status_var.set("Checking for updates...")
        self._set_update_path(
            "Checking releases",
            "Contacting GitHub releases...",
            "The app is checking the latest published release.",
        )
        self.progress_var.set(0)
        self.progress_text_var.set("")
        self._set_notes("Checking GitHub releases...")
        self._set_busy(True)
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self):
        try:
            info = fetch_latest_release(current_version=APP_VERSION)
            self._run_on_ui(self._apply_update_info, info)
        except Exception as exc:
            self._run_on_ui(self._show_error, str(exc))

    def _apply_update_info(self, info: UpdateInfo):
        self.info = info
        self.asset = choose_download_asset(info, self.execution_mode)
        self.latest_var.set(info.latest_tag or info.latest_version)
        if self.asset:
            source_label = "mirror" if self.asset.source == "mirror" else "GitHub asset"
            size_label = f" ({format_bytes(self.asset.size)})" if self.asset.size else ""
            self.asset_var.set(f"{self.asset.name}{size_label} / {source_label}")
        elif self.execution_mode == "frozen" and any(asset.name.lower().endswith(".zip") for asset in info.assets):
            self.asset_var.set("No compatible EXE ZIP attached")
        else:
            self.asset_var.set("No ZIP package attached")

        self.status_var.set("Update available." if info.update_available else "You are up to date.")
        notes = info.release_notes or "No release notes were published for this release."
        if not info.update_available:
            self._set_update_path(
                "No package needed",
                "You are already running the latest version.",
                "No action is needed for this installation.",
            )
        elif self.execution_mode != "frozen":
            self._set_update_path(
                "Source mode",
                "Update this copy through Git.",
                "Automatic apply is available only in the packaged EXE build.",
            )
        elif self.asset is not None:
            source_label = "Mirror package" if self.asset.source == "mirror" else "GitHub release asset"
            self._set_update_path(
                source_label,
                "Download the package, then apply it from this window.",
                "The app will validate the portable ZIP before applying it.",
            )
        else:
            self._set_update_path(
                "No compatible package",
                "Open the release page and update manually.",
                "This release does not expose an auto-applicable portable EXE package.",
            )
        if info.update_available and self.asset is None:
            if self.execution_mode == "frozen":
                notes += "\n\nNo compatible portable EXE ZIP is attached to this release. Open the release page and update manually."
            else:
                notes += "\n\nNo downloadable ZIP package is attached to this release. Open the release page and update manually."
        if self.asset is not None and self.asset.source == "mirror":
            notes += "\n\nThis release provides a mirror package link. The app will use it because GitHub Release assets may be unavailable on some networks."
        if self.execution_mode != "frozen":
            notes += "\n\nSource mode is updated through Git. Automatic apply is available only in the packaged EXE build."
        self._set_notes(notes)
        self._set_busy(False)

    def _show_error(self, message: str):
        self.status_var.set("Update check failed.")
        self.latest_var.set("-")
        self.asset_var.set("-")
        self._set_update_path(
            "Check failed",
            "Try again, or open the release page manually.",
            "The update check did not complete.",
        )
        self._set_notes(message)
        self._set_busy(False)

    def open_release(self):
        url = self.info.release_url if self.info else GITHUB_RELEASES_PAGE
        webbrowser.open(url)

    def open_downloads(self):
        downloads_dir = self.runtime_dir / "updates"
        downloads_dir.mkdir(exist_ok=True)
        self.open_path(downloads_dir)

    def select_local_package(self):
        downloads_dir = self.runtime_dir / "updates"
        downloads_dir.mkdir(exist_ok=True)
        path = filedialog.askopenfilename(
            title="Select update ZIP",
            initialdir=str(downloads_dir),
            filetypes=[("ZIP packages", "*.zip"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        self._use_update_package(Path(path), action="Selected")

    def download_update(self):
        if self.is_busy or self.asset is None:
            return
        self.status_var.set("Downloading package...")
        source_label = "Mirror package" if self.asset.source == "mirror" else "GitHub release asset"
        self._set_update_path(
            source_label,
            "Downloading package...",
            "Keep this window open while the package downloads.",
        )
        self.progress_var.set(0)
        self.progress_text_var.set("")
        self._set_busy(True)
        threading.Thread(target=self._download_worker, args=(self.asset,), daemon=True).start()

    def _download_worker(self, asset: ReleaseAsset):
        try:
            target = download_asset(
                asset,
                self.runtime_dir / "updates",
                progress=lambda done, total: self._run_on_ui(self._set_progress, done, total),
            )
            self._run_on_ui(self._download_finished, target)
        except Exception as exc:
            self._run_on_ui(self._download_failed, str(exc))

    def _set_progress(self, done: int, total: int):
        if total > 0:
            percent = min(100, max(0, (done / total) * 100))
            self.progress_var.set(percent)
            self.progress_text_var.set(f"{percent:.0f}%")
        else:
            self.progress_text_var.set(format_bytes(done))

    def _download_finished(self, target: Path):
        self._use_update_package(target, action="Downloaded")
        messagebox.showinfo("Updates", "Update package downloaded.", parent=self)

    def _use_update_package(self, target: Path, *, action: str):
        self.downloaded_path = target
        self.downloaded_package_ready = False
        self.progress_var.set(100)
        self.progress_text_var.set("Ready")
        self.status_var.set(f"Package {action.lower()}.")
        if self.execution_mode == "frozen":
            try:
                payload_root = validate_portable_update_package(target)
                self.downloaded_package_ready = True
                self._set_update_path(
                    "Validated package",
                    "Ready to apply. The app will back up replaced files and restart.",
                    "The selected ZIP passed the portable package validation.",
                )
                self._set_notes(
                    f"{action}:\n{target}\n\nPackage check passed. Payload root: {payload_root}\n\nYou can apply this update automatically in EXE mode. The updater will close {APP_NAME}, keep local runtime data, back up replaced app files, and restart the app."
                )
            except Exception as exc:
                self.status_var.set(f"Package {action.lower()} but cannot be applied automatically.")
                self._set_update_path(
                    "Package rejected",
                    "Open the release page and update manually.",
                    "The selected ZIP is not compatible with automatic EXE updates.",
                )
                self._set_notes(
                    f"{action}:\n{target}\n\nThis ZIP cannot be applied automatically:\n{exc}\n\nOpen the release page and update manually."
                )
        else:
            self._set_update_path(
                "Source mode",
                "Use Git to update this copy.",
                "Downloaded packages are for EXE/manual inspection in source mode.",
            )
            self._set_notes(
                f"{action}:\n{target}\n\nSource mode is updated through Git. Use downloaded packages only for EXE/manual inspection."
            )
        self._set_busy(False)

    def _download_failed(self, message: str):
        self.status_var.set("Download failed.")
        self.downloaded_package_ready = False
        self._set_update_path(
            "Download failed",
            "Open the release page, download manually, then use Select ZIP.",
            "Network interruptions can be worked around with a manually selected package.",
        )
        self._set_notes(
            f"{message}\n\nIf GitHub keeps interrupting the connection, use Open release to download the ZIP in your browser, then choose Select ZIP here."
        )
        self._set_busy(False)

    def apply_update(self):
        if self.execution_mode != "frozen":
            messagebox.showinfo("Updates", "Automatic apply is available in EXE mode only.", parent=self)
            return
        if self.downloaded_path is None or not self.downloaded_path.exists():
            messagebox.showinfo("Updates", "Download an update package first.", parent=self)
            return
        try:
            validate_portable_update_package(self.downloaded_path)
        except Exception as exc:
            self.downloaded_package_ready = False
            self.status_var.set("Package cannot be applied automatically.")
            self._set_update_path(
                "Package rejected",
                "Select a compatible portable Windows package.",
                "The selected ZIP cannot be applied automatically.",
            )
            self._set_notes(f"This ZIP cannot be applied automatically:\n{exc}\n\nOpen the release page and update manually.")
            self._set_busy(False)
            messagebox.showerror("Updates", str(exc), parent=self)
            return
        confirmed = messagebox.askyesno(
            "Apply update",
            "The app will close, back up replaced app files, apply the downloaded package, and restart.\n\nContinue?",
            parent=self,
        )
        if not confirmed:
            return
        try:
            log_path = launch_update_applier(
                package_path=self.downloaded_path,
                app_dir=self.runtime_dir,
                restart_path=Path(sys.executable),
                pid_to_wait=os.getpid(),
            )
        except Exception as exc:
            messagebox.showerror("Updates", str(exc), parent=self)
            return

        self.status_var.set("Applying update...")
        self._set_update_path(
            "Applying package",
            "The updater is running and the app will restart.",
            "Local runtime data will be preserved while app files are replaced.",
        )
        self._set_notes(f"Updater started.\n\nLog:\n{log_path}\n\n{APP_NAME} will close now and restart after the update is applied.")
        messagebox.showinfo("Updates", "Updater started. The app will close now.", parent=self)
        master = self.master
        self.destroy()
        if hasattr(master, "exit_app"):
            if hasattr(master, "_schedule"):
                master._schedule(100, master.exit_app)
            else:
                master.after(100, master.exit_app)


class TrackerApp(AppRoot):
    def __init__(self, minimized: bool = False):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x930")
        self.minsize(1000, 860)
        set_surface_color(self, APP_BG)
        self.bundle_dir = get_app_base_dir(__file__)
        self.runtime_dir = get_runtime_data_dir(__file__)
        self.base_dir = self.runtime_dir
        configure_font_system(self, self.bundle_dir)
        ensure_example_copied_if_missing(self.runtime_dir, self.bundle_dir)
        self.config_path = self.runtime_dir / "config.json"
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.runner = TrackerRunner(self.runtime_dir, "config.json", log_callback=self._enqueue_log)
        self.last_diagnostics_report: dict | None = None
        self.tray_icon = None
        self._closing_to_tray = True
        self._after_ids: set[str] = set()
        self._build_ui()
        self._schedule(400, self._pump_logs)
        self._schedule(1000, self._refresh_status)

        self.config_data = load_json_config(self.config_path) if self.config_path.exists() else default_config()
        self.last_diagnostics_report = run_startup_self_check(self.runtime_dir, self.bundle_dir, self.config_path, self.config_data)
        if deep_get(self.config_data, "options.launch_with_windows", False):
            set_windows_autostart(
                True,
                self.bundle_dir,
                start_minimized=bool(deep_get(self.config_data, "options.start_minimized", False)),
            )

        if not self.config_path.exists():
            self._schedule(250, self.open_settings)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.runner.set_app_mode("window")
        self._enqueue_log(f"Running in {get_execution_mode()} mode.")
        self._apply_startup_diagnostics(bool(minimized or deep_get(self.config_data, "options.start_minimized", False)))

        launch_hidden = bool(minimized or deep_get(self.config_data, "options.start_minimized", False))
        if launch_hidden:
            self._schedule(350, self.hide_to_tray)
        else:
            self._schedule(350, self.show_from_tray)

        if deep_get(self.config_data, "options.start_auto_polling_on_launch", False):
            self._schedule(700 if launch_hidden else 450, self._start_auto_on_launch)
        if deep_get(self.config_data, "options.check_updates_on_launch", True):
            self._schedule(1800, self._check_updates_on_launch)

    def _schedule(self, delay_ms: int, callback, *args):
        after_id = ""

        def dispatch():
            self._after_ids.discard(after_id)
            try:
                if self.winfo_exists():
                    callback(*args)
            except tk.TclError:
                pass

        after_id = self.after(delay_ms, dispatch)
        self._after_ids.add(after_id)
        return after_id

    def _cancel_scheduled_callbacks(self):
        for after_id in list(self._after_ids):
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
            self._after_ids.discard(after_id)

    def destroy(self):
        if hasattr(self, "_after_ids"):
            self._cancel_scheduled_callbacks()
        super().destroy()

    def _build_ui(self):
        apply_desktop_theme(self)

        shell = tk.Frame(self, bg=APP_BG)
        shell.pack(fill="both", expand=True)
        self.status_line_var = tk.StringVar(value="Ready.")

        header = tk.Frame(shell, bg=APP_BG, padx=20, pady=12)
        header.pack(fill="x")
        header.grid_columnconfigure(0, weight=1)
        tk.Label(header, text=APP_NAME, bg=APP_BG, fg=HEADER_FG, font=ui_font(23, "bold", display=True)).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text=f"v{APP_VERSION} / {get_execution_mode()}",
            bg=CARD_ALT_BG,
            fg=SUBTEXT_FG,
            font=ui_font(11, "bold"),
            padx=10,
            pady=4,
        ).grid(row=0, column=1, sticky="e")
        tk.Label(
            header,
            text="Local creator analytics for posts, collections, and dashboard monitoring.",
            bg=APP_BG,
            fg=SUBTEXT_FG,
            font=ui_font(12),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self._build_footer(shell)

        body = tk.Frame(shell, bg=APP_BG, padx=16, pady=4)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(2, weight=1, minsize=190)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)

        self._build_status_card(body)
        self._build_actions_card(body)
        self._build_health_card(body)
        self._build_log_card(body)

    def _make_card(self, parent, title: str, row: int, column: int, *, columnspan: int = 1, weight: int = 0):
        frame = tk.Frame(parent, bg=CARD_BG, padx=14, pady=14, highlightthickness=1, highlightbackground=BORDER_FG)
        frame.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=6, pady=6)
        if weight:
            parent.grid_rowconfigure(row, weight=weight)
        tk.Label(frame, text=title, bg=CARD_BG, fg=SUBTEXT_FG, font=ui_font(12, "bold")).pack(anchor="w")
        return frame

    def _build_status_card(self, parent):
        card = self._make_card(parent, "CURRENT STATE", 0, 0)
        self.status_var = tk.StringVar(value="Idle")
        self.last_success_var = tk.StringVar(value="Never")
        self.next_run_var = tk.StringVar(value="Not scheduled")
        self.last_error_var = tk.StringVar(value="No recent errors")
        self.polling_var = tk.StringVar(value="Off")
        self.interval_var = tk.StringVar(value="15 minutes")
        self.version_var = tk.StringVar(value=f"v{APP_VERSION} / {get_execution_mode()}")
        self.status_summary_var = tk.StringVar(value="Ready for a manual run.")

        hero = tk.Frame(card, bg=CARD_BG)
        hero.pack(fill="x", pady=(8, 0))
        self.status_dot = tk.Label(hero, text="●", bg=CARD_BG, fg=STATUS_IDLE, font=ui_font(18, "bold"))
        self.status_dot.pack(side="left", anchor="n", padx=(0, 9))
        text_stack = tk.Frame(hero, bg=CARD_BG)
        text_stack.pack(side="left", fill="x", expand=True)
        tk.Label(text_stack, textvariable=self.status_var, bg=CARD_BG, fg=HEADER_FG, font=ui_font(20, "bold")).pack(anchor="w")
        tk.Label(
            text_stack,
            textvariable=self.status_summary_var,
            bg=CARD_BG,
            fg=SUBTEXT_FG,
            font=ui_font(12),
            justify="left",
            wraplength=520,
        ).pack(anchor="w", pady=(2, 0))

        quick = tk.Frame(card, bg=CARD_BG)
        quick.pack(fill="x", pady=(10, 0))
        quick.grid_columnconfigure((0, 1), weight=1)
        self._build_status_tile(quick, "Last successful run", self.last_success_var, 0, 0)
        self._build_status_tile(quick, "Next scheduled run", self.next_run_var, 0, 1)

    def _build_status_tile(self, parent, label: str, var: tk.StringVar, row: int, column: int):
        tile = tk.Frame(parent, bg=CARD_ALT_BG, padx=10, pady=8, highlightthickness=1, highlightbackground=BORDER_FG)
        tile.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0 if column == 1 else 6), pady=0)
        tk.Label(tile, text=label, bg=CARD_ALT_BG, fg=SUBTEXT_FG, font=ui_font(10, "bold")).pack(anchor="w")
        tk.Label(tile, textvariable=var, bg=CARD_ALT_BG, fg=HEADER_FG, font=ui_font(12), wraplength=260, justify="left").pack(anchor="w", pady=(3, 0))

    def _build_actions_card(self, parent):
        card = self._make_card(parent, "ACTIONS", 0, 1)
        actions = tk.Frame(card, bg=CARD_BG)
        actions.pack(fill="x", pady=(10, 0))
        actions.grid_columnconfigure((0, 1, 2), weight=1)

        self.run_now_btn = make_button(actions, "Run now", self.run_now, kind="primary")
        self.dashboard_btn = make_button(actions, "Open dashboard", self.open_dashboard, kind="primary")
        self.start_auto_btn = make_button(actions, "Start auto", self.start_auto)
        self.stop_auto_btn = make_button(actions, "Stop auto", self.stop_auto)
        self.settings_btn = make_button(actions, "Settings", self.open_settings)
        self.updates_btn = make_button(actions, "Updates", self.open_updates)
        self.diagnostics_btn = make_button(actions, "Diagnostics", self.open_diagnostics)
        self.data_btn = make_button(actions, "Data folder", self.open_data_folder)
        self.logs_btn = make_button(actions, "Logs", self.open_logs)
        self.tray_btn = make_button(actions, "Hide to tray", self.hide_to_tray)
        self.exit_btn = make_button(actions, "Exit app", self.exit_app)

        layout = [
            (self.run_now_btn, 0, 0, 1),
            (self.dashboard_btn, 0, 1, 2),
            (self.start_auto_btn, 1, 0, 1),
            (self.stop_auto_btn, 1, 1, 1),
            (self.settings_btn, 1, 2, 1),
            (self.updates_btn, 2, 0, 1),
            (self.diagnostics_btn, 2, 1, 1),
            (self.data_btn, 2, 2, 1),
            (self.logs_btn, 3, 0, 1),
            (self.tray_btn, 3, 1, 1),
            (self.exit_btn, 3, 2, 1),
        ]
        for btn, row, column, columnspan in layout:
            btn.grid(row=row, column=column, columnspan=columnspan, sticky="ew", padx=4, pady=4)

    def _build_health_card(self, parent):
        card = self._make_card(parent, "RUN HEALTH", 1, 0, columnspan=2)
        grid = tk.Frame(card, bg=CARD_BG)
        grid.pack(fill="x", pady=(10, 0))
        grid.grid_columnconfigure((0, 1, 2, 3), weight=1)
        items = [
            ("Auto polling", self.polling_var),
            ("Polling interval", self.interval_var),
            ("App version", self.version_var),
            ("Last error", self.last_error_var),
        ]
        for idx, (label, var) in enumerate(items):
            tile = tk.Frame(grid, bg=CARD_ALT_BG, padx=12, pady=10, highlightthickness=1, highlightbackground=BORDER_FG)
            tile.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 5, 0 if idx == len(items) - 1 else 5))
            tk.Label(tile, text=label, bg=CARD_ALT_BG, fg=SUBTEXT_FG, font=ui_font(10, "bold")).pack(anchor="w")
            tk.Label(tile, textvariable=var, bg=CARD_ALT_BG, fg=HEADER_FG, font=ui_font(12), wraplength=200, justify="left").pack(anchor="w", pady=(4, 0))

    def _build_log_card(self, parent):
        card = self._make_card(parent, "ACTIVITY", 2, 0, columnspan=2, weight=3)
        self.activity_summary_var = tk.StringVar(value="Waiting for tracker activity.")
        tk.Label(
            card,
            textvariable=self.activity_summary_var,
            bg=CARD_BG,
            fg=SUBTEXT_FG,
            font=ui_font(12),
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        timeline = tk.Frame(card, bg=CARD_BG)
        timeline.pack(fill="both", expand=True, pady=(10, 0))
        timeline.columnconfigure(0, weight=1)
        timeline.rowconfigure(0, weight=1)

        self.activity_canvas = tk.Canvas(timeline, bg=CARD_BG, height=180, highlightthickness=0, borderwidth=0)
        self.activity_canvas.grid(row=0, column=0, sticky="nsew")
        if CUSTOM_TK_AVAILABLE:
            scrollbar = ctk.CTkScrollbar(
                timeline,
                orientation="vertical",
                command=self.activity_canvas.yview,
                fg_color=CARD_BG,
                button_color=CARD_ALT_BG,
                button_hover_color=ACCENT_HOVER_BG,
            )
        else:
            scrollbar = ttk.Scrollbar(timeline, orient="vertical", command=self.activity_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.activity_canvas.configure(yscrollcommand=scrollbar.set)

        self.activity_list = tk.Frame(self.activity_canvas, bg=CARD_BG)
        self.activity_window = self.activity_canvas.create_window((0, 0), window=self.activity_list, anchor="nw")
        self.activity_items: list[tk.Frame] = []
        self.activity_canvas.bind("<Configure>", self._resize_activity_list)
        self.activity_list.bind("<Configure>", lambda _event: self.activity_canvas.configure(scrollregion=self.activity_canvas.bbox("all")))
        self._append_activity_event("Status", "Tracker window is ready.", STATUS_IDLE)

    def _build_footer(self, shell):
        footer = tk.Frame(shell, bg=FOOTER_BG, padx=16, pady=5)
        footer.pack(side="bottom", fill="x")
        footer.grid_columnconfigure(0, weight=1)
        tk.Label(footer, textvariable=self.status_line_var, bg=FOOTER_BG, fg=SUBTEXT_FG, font=ui_font(11), anchor="w").grid(row=0, column=0, sticky="ew")
        tk.Label(footer, text=f"{APP_TITLE} / {get_execution_mode()}", bg=FOOTER_BG, fg=SUBTEXT_FG, font=ui_font(11), anchor="e").grid(row=0, column=1, sticky="e", padx=(12, 0))

    def _enqueue_log(self, message: str):
        self.log_queue.put(message)

    def _set_status_line(self, message: str):
        self.status_line_var.set(message)

    def _classify_activity(self, message: str) -> tuple[str, str]:
        text = message.lower()
        if any(token in text for token in ("error", "failed", "critical", "invalid", "cannot")):
            return "Error", STATUS_ERR
        if any(token in text for token in ("warning", "missing", "limited", "not available")):
            return "Warning", STATUS_RUN
        if "update" in text:
            return "Update", ACCENT_HOVER_BG
        if any(token in text for token in ("auto polling", "polling", "scheduled")):
            return "Polling", STATUS_OK
        if any(token in text for token in ("dashboard", "snapshot", "csv", "collection", "post")):
            return "Data", STATUS_OK
        return "Status", STATUS_IDLE

    def _resize_activity_list(self, event):
        self.activity_canvas.itemconfigure(self.activity_window, width=event.width)

    def _append_activity_event(self, kind: str, message: str, color: str):
        row = tk.Frame(self.activity_list, bg=CARD_ALT_BG, padx=12, pady=10, highlightthickness=1, highlightbackground=BORDER_FG)
        row.pack(fill="x", pady=(0, 8))
        row.columnconfigure(1, weight=1)

        timestamp = datetime.now().strftime("%H:%M:%S")
        meta = tk.Frame(row, bg=CARD_ALT_BG)
        meta.grid(row=0, column=0, sticky="nw", padx=(0, 12))
        tk.Label(meta, text=timestamp, bg=CARD_ALT_BG, fg=SUBTEXT_FG, font=ui_font(10, "bold")).pack(anchor="w")
        tk.Label(meta, text=kind, bg=CARD_ALT_BG, fg=color, font=ui_font(11, "bold")).pack(anchor="w", pady=(4, 0))

        tk.Label(
            row,
            text=message,
            bg=CARD_ALT_BG,
            fg=HEADER_FG,
            font=ui_font(12),
            justify="left",
            wraplength=760,
        ).grid(row=0, column=1, sticky="ew")

        self.activity_items.append(row)
        while len(self.activity_items) > 60:
            old = self.activity_items.pop(0)
            old.destroy()
        self.activity_summary_var.set(f"Latest event: {message}")
        self.activity_canvas.update_idletasks()
        self.activity_canvas.configure(scrollregion=self.activity_canvas.bbox("all"))
        self.activity_canvas.yview_moveto(1.0)

    def _pump_logs(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                kind, color = self._classify_activity(line)
                self._append_activity_event(kind, line, color)
                self._set_status_line(line)
        except queue.Empty:
            pass
        self._schedule(400, self._pump_logs)

    def _status_color(self, snap) -> str:
        if snap.is_running:
            return STATUS_RUN
        if snap.status == "Error":
            return STATUS_ERR
        if snap.auto_polling:
            return STATUS_OK
        return STATUS_IDLE

    def _read_interval_text(self) -> str:
        cfg = load_json_config(self.config_path) if self.config_path.exists() else default_config()
        minutes = deep_get(cfg, "tracking.poll_minutes", 15)
        try:
            minutes = int(minutes)
        except Exception:
            minutes = 15
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    def _refresh_status(self):
        snap = self.runner.snapshot()
        self.status_var.set(snap.status)
        self.status_dot.configure(fg=self._status_color(snap))
        self.last_success_var.set(format_elapsed_time(snap.last_success_at))
        self.next_run_var.set(format_next_run_time(snap.next_run_at))
        if snap.is_running:
            self.status_summary_var.set("Collecting current post data and refreshing the dashboard.")
        elif snap.status == "Error":
            self.status_summary_var.set(snap.last_error or "The last run failed. Open Diagnostics for details.")
        elif snap.auto_polling:
            self.status_summary_var.set(f"Auto polling is active. Next run {format_next_run_time(snap.next_run_at).split(' · ', 1)[0]}.")
        else:
            self.status_summary_var.set("Ready for a manual run.")
        self.last_error_var.set(snap.last_error or "No recent errors")
        self.polling_var.set("On" if snap.auto_polling else "Off")
        self.interval_var.set(self._read_interval_text())

        if snap.is_running:
            self.run_now_btn.configure(state="disabled")
            self.start_auto_btn.configure(state="disabled")
            self.stop_auto_btn.configure(state="normal" if snap.auto_polling else "disabled")
        else:
            self.run_now_btn.configure(state="normal")
            self.start_auto_btn.configure(state="disabled" if snap.auto_polling else "normal")
            self.stop_auto_btn.configure(state="normal" if snap.auto_polling else "disabled")

        self._schedule(1000, self._refresh_status)

    def run_now(self):
        threading.Thread(target=self.runner.run_once, daemon=True).start()

    def _start_auto_on_launch(self):
        if self.runner.start_auto_polling():
            self._enqueue_log("Auto polling started on launch.")
        else:
            self._enqueue_log("Auto polling is already running.")

    def start_auto(self):
        if self.runner.start_auto_polling():
            self._enqueue_log("Auto polling enabled.")
        else:
            self._enqueue_log("Auto polling is already running.")

    def stop_auto(self):
        if self.runner.stop_auto_polling():
            self._enqueue_log("Stopping auto polling...")
        else:
            self._enqueue_log("Auto polling is not active.")

    def open_dashboard(self):
        cfg = load_json_config(self.config_path) if self.config_path.exists() else default_config()
        configured_path = Path(str(deep_get(cfg, "paths.html", "dashboard.html") or "dashboard.html")).expanduser()
        html_path = configured_path if configured_path.is_absolute() else self.runtime_dir / configured_path
        if not html_path.exists():
            messagebox.showinfo("Dashboard", f"Dashboard file not found:\n{html_path}")
            return
        version = int(html_path.stat().st_mtime)
        webbrowser.open(f"{html_path.resolve().as_uri()}?v={version}")

    def open_data_folder(self):
        self._open_path(self.runtime_dir)

    def open_logs(self):
        logs_dir = self.runtime_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        self._open_path(logs_dir)

    def open_diagnostics(self):
        report = self.last_diagnostics_report or run_startup_self_check(self.runtime_dir, self.bundle_dir, self.config_path, self.config_data)
        self.last_diagnostics_report = report
        DiagnosticsDialog(self, report)

    def open_updates(self):
        UpdateDialog(self, self.runtime_dir, get_execution_mode(), self._open_path)

    def _check_updates_on_launch(self):
        threading.Thread(target=self._startup_update_worker, daemon=True).start()

    def _startup_update_worker(self):
        try:
            info = fetch_latest_release(current_version=APP_VERSION, timeout_seconds=12)
        except Exception:
            return
        if not info.update_available:
            return
        latest = info.latest_tag or info.latest_version
        self._enqueue_log(f"Update available: {latest}. Open Updates to download it.")
        self._schedule(0, self._set_status_line, f"Update available: {latest}.")

    def _apply_startup_diagnostics(self, hidden_launch: bool):
        report = self.last_diagnostics_report or {}
        if not report:
            return
        summary = startup_check_summary(report)
        self._enqueue_log(summary)
        for item in report.get("critical", []):
            self._enqueue_log(f"CRITICAL: {item}")
        for item in report.get("warnings", []):
            self._enqueue_log(f"Warning: {item}")
        if report.get("critical_count", 0) and not hidden_launch:
            self._schedule(900, lambda: messagebox.showwarning(
                "Startup self-check",
                summary + "\n\nOpen Diagnostics for details.",
                parent=self,
            ))

    def _open_path(self, path: Path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Open path failed", str(exc))

    def open_settings(self):
        current = load_json_config(self.config_path) if self.config_path.exists() else default_config()

        def on_save(cfg):
            self.config_data = cfg
            self.last_diagnostics_report = run_startup_self_check(self.runtime_dir, self.bundle_dir, self.config_path, self.config_data)
            self._enqueue_log("Configuration saved.")
            self._enqueue_log(startup_check_summary(self.last_diagnostics_report))
            if deep_get(cfg, "options.launch_with_windows", False):
                self._enqueue_log("Windows autostart enabled.")
            else:
                self._enqueue_log("Windows autostart disabled.")
            if deep_get(cfg, "options.start_auto_polling_on_launch", False):
                self._enqueue_log("Auto polling will start automatically on launch.")
            else:
                self._enqueue_log("Auto polling on launch is disabled.")
            if deep_get(cfg, "options.check_updates_on_launch", True):
                self._enqueue_log("Update check on launch is enabled.")
            else:
                self._enqueue_log("Update check on launch is disabled.")

        SettingsDialog(self, self.runtime_dir, current, on_save)

    def _create_tray_image(self):
        image = Image.new("RGB", (64, 64), color=(17, 19, 23))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((6, 6, 58, 58), radius=12, fill=(141, 29, 36))
        draw.ellipse((18, 18, 46, 46), fill=(255, 255, 255))
        draw.rectangle((29, 14, 35, 50), fill=(17, 19, 23))
        draw.rectangle((14, 29, 50, 35), fill=(17, 19, 23))
        return image

    def _ensure_tray(self):
        if pystray is None or Image is None or ImageDraw is None:
            return False
        if self.tray_icon is not None:
            return True
        try:
            menu = pystray.Menu(
                pystray.MenuItem(
                    "Open",
                    lambda icon, item: self._schedule(0, self.show_from_tray),
                    default=True,
                    visible=False,
                ),
                pystray.MenuItem("Open", lambda icon, item: self._schedule(0, self.show_from_tray)),
                pystray.MenuItem("Run now", lambda icon, item: self._schedule(0, self.run_now)),
                pystray.MenuItem("Start auto polling", lambda icon, item: self._schedule(0, self.start_auto)),
                pystray.MenuItem("Stop auto polling", lambda icon, item: self._schedule(0, self.stop_auto)),
                pystray.MenuItem("Open dashboard", lambda icon, item: self._schedule(0, self.open_dashboard)),
                pystray.MenuItem("Diagnostics", lambda icon, item: self._schedule(0, self.open_diagnostics)),
                pystray.MenuItem("Updates", lambda icon, item: self._schedule(0, self.open_updates)),
                pystray.MenuItem("Exit", lambda icon, item: self._schedule(0, self.exit_app)),
            )
            self.tray_icon = pystray.Icon("civitai_tracker", self._create_tray_image(), "CivitAI Tracker", menu)
            self.tray_icon.run_detached()
            return True
        except Exception as exc:
            self._enqueue_log(f"Tray mode unavailable: {exc}")
            self.tray_icon = None
            return False

    def hide_to_tray(self):
        if self._ensure_tray():
            self.withdraw()
            self.runner.set_app_mode("tray")
            self._enqueue_log("App hidden to tray.")
        else:
            self.iconify()

    def show_from_tray(self):
        self.deiconify()
        try:
            self.state("normal")
        except Exception:
            pass
        self._force_show_main_window()
        self.runner.set_app_mode("window")
        self._schedule(50, self.lift)
        self._schedule(100, self.focus_force)
        self._schedule(150, self._force_show_main_window)

    def _force_show_main_window(self):
        if not sys.platform.startswith("win"):
            return
        try:
            user32 = ctypes.windll.user32
            handles: list[int] = []
            raw_handles = [self.winfo_id()]
            try:
                raw_handles.append(self.wm_frame())
            except Exception:
                pass
            for raw_handle in raw_handles:
                try:
                    hwnd = int(str(raw_handle), 0)
                except (TypeError, ValueError):
                    continue
                if hwnd and hwnd not in handles:
                    handles.append(hwnd)
                try:
                    root_hwnd = int(user32.GetAncestor(hwnd, 2))
                    if root_hwnd and root_hwnd not in handles:
                        handles.append(root_hwnd)
                except Exception:
                    pass
            for hwnd in handles:
                user32.ShowWindow(hwnd, 5)
                user32.ShowWindow(hwnd, 9)
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0043)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _on_close(self):
        self.hide_to_tray()

    def exit_app(self):
        self._closing_to_tray = False
        self.runner.stop_auto_polling()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()


def build_parser():
    parser = argparse.ArgumentParser(description="CivitAI Tracker desktop app")
    parser.add_argument("--minimized", action="store_true", help="Start minimized to tray")
    parser.add_argument("--setup", action="store_true", help="Open settings immediately")
    parser.add_argument("--version", action="store_true", help="Show app version and exit")
    parser.add_argument("--hide-console", action="store_true", help=argparse.SUPPRESS)
    return parser


def hide_console_window():
    if not sys.platform.startswith("win"):
        return
    try:
        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.ShowWindow(console, 0)
    except Exception:
        pass


def main():
    args = build_parser().parse_args()
    if args.version:
        print(APP_TITLE)
        return 0
    if args.hide_console:
        hide_console_window()
    runtime_dir = get_runtime_data_dir(__file__)
    instance_lock = SingleInstanceLock(runtime_dir / ".civitai_tracker.lock")
    if not instance_lock.acquire():
        show_already_running_message(runtime_dir)
        return 0
    try:
        app = TrackerApp(minimized=args.minimized)
        if args.setup:
            app._schedule(200, app.open_settings)
        app.mainloop()
        return 0
    finally:
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
