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
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

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


APP_BG = "#101317"
CARD_BG = "#171b21"
CARD_ALT_BG = "#12161c"
HEADER_BG = "#8d1d24"
HEADER_FG = "#ffffff"
SUBTEXT_FG = "#b9c0cb"
BORDER_FG = "#252b34"
ACCENT_BG = "#a51f2b"
STATUS_OK = "#37c871"
STATUS_RUN = "#f3c969"
STATUS_ERR = "#e25555"
STATUS_IDLE = "#7c8a9d"


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


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, base_dir: Path, config: dict, on_save):
        super().__init__(master)
        self.title("CivitAI Tracker Settings")
        self.geometry("900x760")
        self.minsize(860, 700)
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

        self.status_var = tk.StringVar(value="Fill in your settings and click Save.")

        self._build()
        self._toggle_auth_mode()
        self._toggle_start_mode()
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build(self):
        self.configure(bg=APP_BG)
        container = ttk.Frame(self, padding=14)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        header = tk.Frame(container, bg=HEADER_BG, padx=14, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text="Settings", bg=HEADER_BG, fg=HEADER_FG, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(
            header,
            text="Configure your profile, authentication, tracking start point, and app behavior.",
            bg=HEADER_BG,
            fg="#f2d6d8",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(container)
        notebook.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

        self.profile_tab = self._make_tab(notebook, "Profile")
        self.auth_tab = self._make_tab(notebook, "Authentication")
        self.tracking_tab = self._make_tab(notebook, "Tracking")
        self.api_tab = self._make_tab(notebook, "API")
        self.output_tab = self._make_tab(notebook, "Output")
        self.app_tab = self._make_tab(notebook, "Application")

        notebook.add(self.profile_tab, text="Profile")
        notebook.add(self.auth_tab, text="Authentication")
        notebook.add(self.tracking_tab, text="Tracking")
        notebook.add(self.api_tab, text="API")
        notebook.add(self.output_tab, text="Output")
        notebook.add(self.app_tab, text="Application")

        self._build_profile_tab()
        self._build_auth_tab()
        self._build_tracking_tab()
        self._build_api_tab()
        self._build_output_tab()
        self._build_app_tab()

        footer = ttk.Frame(container)
        footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, wraplength=620).grid(row=0, column=0, sticky="w")
        buttons = ttk.Frame(footer)
        buttons.grid(row=0, column=1, sticky="e")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Save", command=self._save).pack(side="left")

    def _make_tab(self, notebook: ttk.Notebook, title: str) -> ttk.Frame:
        frame = ttk.Frame(notebook, padding=16)
        frame.columnconfigure(1, weight=1)
        return frame

    def _add_help(self, parent: ttk.Frame, row: int, text: str) -> int:
        ttk.Label(parent, text=text, wraplength=520, justify="left").grid(row=row, column=1, sticky="w", pady=(0, 10))
        return row + 1

    def _add_entry_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, *, width: int = 40, help_text: str | None = None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        entry = ttk.Entry(parent, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=(0, 4))
        row += 1
        if help_text:
            row = self._add_help(parent, row, help_text)
        return entry, row

    def _build_profile_tab(self):
        row = 0
        _, row = self._add_entry_row(self.profile_tab, row, "Username", self.username_var, help_text="Your public CivitAI username.")
        _, row = self._add_entry_row(self.profile_tab, row, "Display name", self.display_name_var, help_text="Optional friendly name used inside the app.")

        ttk.Label(self.profile_tab, text="Timezone").grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        tz_row = ttk.Frame(self.profile_tab)
        tz_row.grid(row=row, column=1, sticky="ew", pady=(0, 4))
        tz_row.columnconfigure(0, weight=1)
        self.timezone_entry = ttk.Entry(tz_row, textvariable=self.timezone_var)
        self.timezone_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(tz_row, text="Examples", command=self._show_timezone_examples).grid(row=0, column=1, padx=(6, 0))
        row += 1
        self._add_help(self.profile_tab, row, "Use IANA timezone format, for example Europe/Moscow or America/New_York.")

    def _build_auth_tab(self):
        row = 0
        ttk.Label(self.auth_tab, text="Storage mode").grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        mode_row = ttk.Frame(self.auth_tab)
        mode_row.grid(row=row, column=1, sticky="w", pady=(0, 4))
        ttk.Radiobutton(mode_row, text="Store inside config", variable=self.api_storage_var, value="inline", command=self._toggle_auth_mode).pack(side="left")
        ttk.Radiobutton(mode_row, text="Store in file", variable=self.api_storage_var, value="file", command=self._toggle_auth_mode).pack(side="left", padx=(12, 0))
        row += 1
        row = self._add_help(self.auth_tab, row, "File mode is safer for sharing configs. The app will create and update the key file automatically.")

        ttk.Label(self.auth_tab, text="API key").grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        self.api_key_entry = ttk.Entry(self.auth_tab, textvariable=self.api_key_var, show="•")
        self.api_key_entry.grid(row=row, column=1, sticky="ew", pady=(0, 4))
        row += 1

        ttk.Label(self.auth_tab, text="Key file").grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        file_row = ttk.Frame(self.auth_tab)
        file_row.grid(row=row, column=1, sticky="ew", pady=(0, 4))
        file_row.columnconfigure(0, weight=1)
        self.api_key_file_entry = ttk.Entry(file_row, textvariable=self.api_key_file_var)
        self.api_key_file_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(file_row, text="Browse", command=self._browse_key_file).grid(row=0, column=1, padx=(6, 0))
        row += 1

        self.auth_help_label = ttk.Label(self.auth_tab, text="", wraplength=520, justify="left")
        self.auth_help_label.grid(row=row, column=1, sticky="w", pady=(0, 10))

    def _build_tracking_tab(self):
        row = 0
        ttk.Label(self.tracking_tab, text="Start mode").grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        mode_row = ttk.Frame(self.tracking_tab)
        mode_row.grid(row=row, column=1, sticky="w", pady=(0, 4))
        ttk.Radiobutton(mode_row, text="Post ID or URL", variable=self.start_mode_var, value="post_id", command=self._toggle_start_mode).pack(side="left")
        ttk.Radiobutton(mode_row, text="Date", variable=self.start_mode_var, value="date", command=self._toggle_start_mode).pack(side="left", padx=(12, 0))
        row += 1

        ttk.Label(self.tracking_tab, text="Start post").grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        self.start_post_entry = ttk.Entry(self.tracking_tab, textvariable=self.start_post_var)
        self.start_post_entry.grid(row=row, column=1, sticky="ew", pady=(0, 4))
        row += 1
        row = self._add_help(self.tracking_tab, row, "Paste a post ID or a full post URL.")

        ttk.Label(self.tracking_tab, text="Start date").grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        date_row = ttk.Frame(self.tracking_tab)
        date_row.grid(row=row, column=1, sticky="w", pady=(0, 4))
        self.start_day_entry = ttk.Entry(date_row, textvariable=self.start_day_var, width=4, justify="center")
        self.start_day_entry.pack(side="left")
        ttk.Label(date_row, text="/").pack(side="left", padx=4)
        self.start_month_entry = ttk.Entry(date_row, textvariable=self.start_month_var, width=4, justify="center")
        self.start_month_entry.pack(side="left")
        ttk.Label(date_row, text="/").pack(side="left", padx=4)
        self.start_year_entry = ttk.Entry(date_row, textvariable=self.start_year_var, width=6, justify="center")
        self.start_year_entry.pack(side="left")
        ttk.Label(date_row, text="  DD / MM / YYYY").pack(side="left", padx=(8, 0))
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
        ttk.Label(self.api_tab, text="API mode").grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        ttk.Combobox(self.api_tab, textvariable=self.api_mode_var, values=["red", "auto", "com"], state="readonly", width=14).grid(row=row, column=1, sticky="w", pady=(0, 4))
        row += 1
        row = self._add_help(self.api_tab, row, "Use 'red' for full visibility, including content above PG-13.")
        _, row = self._add_entry_row(self.api_tab, row, "View host", self.view_host_var, help_text="Used for links opened from the app and dashboard.")

        ttk.Label(self.api_tab, text="NSFW level").grid(row=row, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        ttk.Combobox(self.api_tab, textvariable=self.nsfw_var, values=["None", "Soft", "Mature", "X"], state="readonly", width=14).grid(row=row, column=1, sticky="w", pady=(0, 4))
        row += 1
        ttk.Checkbutton(self.api_tab, text="Allow REST fallback for image enrichment", variable=self.allow_rest_var).grid(row=row, column=1, sticky="w", pady=(4, 0))

    def _build_output_tab(self):
        row = 0
        _, row = self._add_entry_row(self.output_tab, row, "Database", self.db_var, help_text="SQLite database file used to store snapshots and history.")
        _, row = self._add_entry_row(self.output_tab, row, "CSV directory", self.csv_var, help_text="Folder where CSV exports are generated.")
        _, row = self._add_entry_row(self.output_tab, row, "Dashboard HTML", self.html_var, help_text="The local dashboard file generated by the tracker.")

    def _build_app_tab(self):
        row = 0
        ttk.Checkbutton(self.app_tab, text="Launch with Windows", variable=self.launch_with_windows_var).grid(row=row, column=1, sticky="w")
        row += 1
        row = self._add_help(self.app_tab, row, "Start the app automatically when Windows starts.")
        ttk.Checkbutton(self.app_tab, text="Start minimized to tray", variable=self.start_minimized_var).grid(row=row, column=1, sticky="w")
        row += 1
        row = self._add_help(self.app_tab, row, "Launch hidden and stay in the system tray until opened.")
        ttk.Checkbutton(self.app_tab, text="Start auto polling on launch", variable=self.start_auto_polling_on_launch_var).grid(row=row, column=1, sticky="w")
        row += 1
        row = self._add_help(self.app_tab, row, "Automatically start background polling when the app launches. Recommended for startup and tray-only use.")
        ttk.Checkbutton(self.app_tab, text="Check for updates on launch", variable=self.check_updates_on_launch_var).grid(row=row, column=1, sticky="w")
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

class DiagnosticsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, report: dict):
        super().__init__(master)
        self.title("Diagnostics")
        self.geometry("820x620")
        self.minsize(720, 520)
        self.report = report

        wrapper = ttk.Frame(self, padding=14)
        wrapper.pack(fill="both", expand=True)
        ttk.Label(wrapper, text="Startup diagnostics", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(wrapper, text=startup_check_summary(report)).pack(anchor="w", pady=(4, 10))

        self.text = ScrolledText(wrapper, wrap="word")
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", format_startup_self_check(report))
        self.text.configure(state="disabled")

        buttons = ttk.Frame(wrapper)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Copy to clipboard", command=self._copy).pack(side="left")
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _copy(self):
        text = format_startup_self_check(self.report)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()


class UpdateDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, runtime_dir: Path, execution_mode: str, open_path):
        super().__init__(master)
        self.title("Updates")
        self.geometry("760x560")
        self.minsize(720, 520)
        self.runtime_dir = runtime_dir
        self.execution_mode = execution_mode
        self.open_path = open_path
        self.info: UpdateInfo | None = None
        self.asset: ReleaseAsset | None = None
        self.downloaded_path: Path | None = None
        self.downloaded_package_ready = False
        self.is_busy = False

        self.status_var = tk.StringVar(value="Ready to check.")
        self.current_var = tk.StringVar(value=f"v{APP_VERSION}")
        self.latest_var = tk.StringVar(value="-")
        self.asset_var = tk.StringVar(value="-")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="")

        self._build()
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(150, self.check_now)

    def _build(self):
        self.configure(bg=APP_BG)
        wrapper = ttk.Frame(self, padding=14)
        wrapper.pack(fill="both", expand=True)
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(2, weight=1)

        header = tk.Frame(wrapper, bg=HEADER_BG, padx=14, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text="Updates", bg=HEADER_BG, fg=HEADER_FG, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(
            header,
            text="Check GitHub releases and download the latest portable package.",
            bg=HEADER_BG,
            fg="#f4d9db",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        summary = ttk.Frame(wrapper)
        summary.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        summary.columnconfigure(1, weight=1)
        summary.columnconfigure(3, weight=1)
        rows = [
            ("Current version", self.current_var),
            ("Latest release", self.latest_var),
            ("Package", self.asset_var),
            ("Status", self.status_var),
        ]
        for idx, (label, var) in enumerate(rows):
            row = idx // 2
            col = (idx % 2) * 2
            ttk.Label(summary, text=label).grid(row=row, column=col, sticky="w", padx=(0, 8), pady=4)
            ttk.Label(summary, textvariable=var, wraplength=250).grid(row=row, column=col + 1, sticky="w", pady=4)

        self.notes_text = ScrolledText(wrapper, height=13, wrap="word")
        self.notes_text.grid(row=2, column=0, sticky="nsew", pady=(4, 8))
        self._set_notes("Release notes will appear here after the check.")

        progress_row = ttk.Frame(wrapper)
        progress_row.grid(row=3, column=0, sticky="ew")
        progress_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_row, variable=self.progress_var, maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(progress_row, textvariable=self.progress_text_var, width=18).grid(row=0, column=1, sticky="e")

        buttons = ttk.Frame(wrapper)
        buttons.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.check_btn = ttk.Button(buttons, text="Check now", command=self.check_now)
        self.release_btn = ttk.Button(buttons, text="Open release", command=self.open_release, state="disabled")
        self.download_btn = ttk.Button(buttons, text="Download package", command=self.download_update, state="disabled")
        self.select_btn = ttk.Button(buttons, text="Select ZIP", command=self.select_local_package)
        self.apply_btn = ttk.Button(buttons, text="Apply downloaded update", command=self.apply_update, state="disabled")
        self.downloads_btn = ttk.Button(buttons, text="Open downloads", command=self.open_downloads)
        self.close_btn = ttk.Button(buttons, text="Close", command=self.destroy)
        buttons.columnconfigure(5, weight=1)
        self.check_btn.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        self.release_btn.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=2)
        self.download_btn.grid(row=0, column=2, sticky="w", padx=(0, 8), pady=2)
        self.select_btn.grid(row=0, column=3, sticky="w", padx=(0, 8), pady=2)
        self.apply_btn.grid(row=1, column=0, columnspan=2, sticky="w", padx=(0, 8), pady=(6, 0))
        self.downloads_btn.grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(6, 0))
        self.close_btn.grid(row=1, column=5, sticky="e", pady=(6, 0))

    def _set_notes(self, text: str):
        self.notes_text.configure(state="normal")
        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("1.0", text)
        self.notes_text.configure(state="disabled")

    def _run_on_ui(self, callback, *args):
        try:
            self.after(0, lambda: self._dispatch_on_ui(callback, *args))
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
                self._set_notes(
                    f"{action}:\n{target}\n\nPackage check passed. Payload root: {payload_root}\n\nYou can apply this update automatically in EXE mode. The updater will close {APP_NAME}, keep local runtime data, back up replaced app files, and restart the app."
                )
            except Exception as exc:
                self.status_var.set(f"Package {action.lower()} but cannot be applied automatically.")
                self._set_notes(
                    f"{action}:\n{target}\n\nThis ZIP cannot be applied automatically:\n{exc}\n\nOpen the release page and update manually."
                )
        else:
            self._set_notes(
                f"{action}:\n{target}\n\nSource mode is updated through Git. Use downloaded packages only for EXE/manual inspection."
            )
        self._set_busy(False)

    def _download_failed(self, message: str):
        self.status_var.set("Download failed.")
        self.downloaded_package_ready = False
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
        self._set_notes(f"Updater started.\n\nLog:\n{log_path}\n\n{APP_NAME} will close now and restart after the update is applied.")
        messagebox.showinfo("Updates", "Updater started. The app will close now.", parent=self)
        master = self.master
        self.destroy()
        if hasattr(master, "exit_app"):
            master.after(100, master.exit_app)


class TrackerApp(tk.Tk):
    def __init__(self, minimized: bool = False):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x700")
        self.minsize(920, 620)
        self.configure(bg=APP_BG)
        self.bundle_dir = get_app_base_dir(__file__)
        self.runtime_dir = get_runtime_data_dir(__file__)
        self.base_dir = self.runtime_dir
        ensure_example_copied_if_missing(self.runtime_dir, self.bundle_dir)
        self.config_path = self.runtime_dir / "config.json"
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.runner = TrackerRunner(self.runtime_dir, "config.json", log_callback=self._enqueue_log)
        self.last_diagnostics_report: dict | None = None
        self.tray_icon = None
        self._closing_to_tray = True
        self._build_ui()
        self.after(400, self._pump_logs)
        self.after(1000, self._refresh_status)

        self.config_data = load_json_config(self.config_path) if self.config_path.exists() else default_config()
        self.last_diagnostics_report = run_startup_self_check(self.runtime_dir, self.bundle_dir, self.config_path, self.config_data)
        if deep_get(self.config_data, "options.launch_with_windows", False):
            set_windows_autostart(
                True,
                self.bundle_dir,
                start_minimized=bool(deep_get(self.config_data, "options.start_minimized", False)),
            )

        if not self.config_path.exists():
            self.after(250, self.open_settings)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.runner.set_app_mode("window")
        self._enqueue_log(f"Running in {get_execution_mode()} mode.")
        self._apply_startup_diagnostics(bool(minimized or deep_get(self.config_data, "options.start_minimized", False)))

        launch_hidden = bool(minimized or deep_get(self.config_data, "options.start_minimized", False))
        if launch_hidden:
            self.after(350, self.hide_to_tray)
        else:
            self.after(350, self.show_from_tray)

        if deep_get(self.config_data, "options.start_auto_polling_on_launch", False):
            self.after(700 if launch_hidden else 450, self._start_auto_on_launch)
        if deep_get(self.config_data, "options.check_updates_on_launch", True):
            self.after(1800, self._check_updates_on_launch)

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Primary.TButton", padding=(14, 9), font=("Segoe UI", 10, "bold"))
        style.configure("Secondary.TButton", padding=(12, 8), font=("Segoe UI", 9))
        style.map(
            "Primary.TButton",
            foreground=[("disabled", "#7d8795"), ("!disabled", "#ffffff")],
            background=[("disabled", "#323842"), ("active", "#bd2a38"), ("!disabled", ACCENT_BG)],
        )
        style.map(
            "Secondary.TButton",
            foreground=[("disabled", "#7d8795"), ("!disabled", "#f4f6f8")],
            background=[("disabled", "#242a33"), ("active", "#343c48"), ("!disabled", "#232932")],
        )

        shell = tk.Frame(self, bg=APP_BG)
        shell.pack(fill="both", expand=True)
        self.status_line_var = tk.StringVar(value="Ready.")

        header = tk.Frame(shell, bg=APP_BG, padx=20, pady=18)
        header.pack(fill="x")
        header.grid_columnconfigure(0, weight=1)
        tk.Label(header, text=APP_NAME, bg=APP_BG, fg=HEADER_FG, font=("Segoe UI", 21, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text=f"v{APP_VERSION} / {get_execution_mode()}",
            bg=CARD_ALT_BG,
            fg=SUBTEXT_FG,
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=4,
        ).grid(row=0, column=1, sticky="e")
        tk.Label(
            header,
            text="Local creator analytics for posts, collections, and dashboard monitoring.",
            bg=APP_BG,
            fg=SUBTEXT_FG,
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        body = tk.Frame(shell, bg=APP_BG, padx=16, pady=10)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(2, weight=1)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)

        self._build_status_card(body)
        self._build_actions_card(body)
        self._build_health_card(body)
        self._build_log_card(body)
        self._build_footer(shell)

    def _make_card(self, parent, title: str, row: int, column: int, *, columnspan: int = 1, weight: int = 0):
        frame = tk.Frame(parent, bg=CARD_BG, padx=16, pady=16, highlightthickness=1, highlightbackground=BORDER_FG)
        frame.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=7, pady=7)
        if weight:
            parent.grid_rowconfigure(row, weight=weight)
        tk.Label(frame, text=title, bg=CARD_BG, fg=SUBTEXT_FG, font=("Segoe UI", 9, "bold")).pack(anchor="w")
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
        hero.pack(fill="x", pady=(12, 0))
        self.status_dot = tk.Label(hero, text="●", bg=CARD_BG, fg=STATUS_IDLE, font=("Segoe UI", 22, "bold"))
        self.status_dot.pack(side="left", anchor="n", padx=(0, 10))
        text_stack = tk.Frame(hero, bg=CARD_BG)
        text_stack.pack(side="left", fill="x", expand=True)
        tk.Label(text_stack, textvariable=self.status_var, bg=CARD_BG, fg=HEADER_FG, font=("Segoe UI", 24, "bold")).pack(anchor="w")
        tk.Label(
            text_stack,
            textvariable=self.status_summary_var,
            bg=CARD_BG,
            fg=SUBTEXT_FG,
            font=("Segoe UI", 10),
            justify="left",
            wraplength=520,
        ).pack(anchor="w", pady=(4, 0))

        quick = tk.Frame(card, bg=CARD_BG)
        quick.pack(fill="x", pady=(18, 0))
        quick.grid_columnconfigure((0, 1), weight=1)
        self._build_status_tile(quick, "Last successful run", self.last_success_var, 0, 0)
        self._build_status_tile(quick, "Next scheduled run", self.next_run_var, 0, 1)

    def _build_status_tile(self, parent, label: str, var: tk.StringVar, row: int, column: int):
        tile = tk.Frame(parent, bg=CARD_ALT_BG, padx=12, pady=10, highlightthickness=1, highlightbackground=BORDER_FG)
        tile.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0 if column == 1 else 6), pady=0)
        tk.Label(tile, text=label, bg=CARD_ALT_BG, fg=SUBTEXT_FG, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(tile, textvariable=var, bg=CARD_ALT_BG, fg=HEADER_FG, font=("Segoe UI", 10), wraplength=260, justify="left").pack(anchor="w", pady=(4, 0))

    def _build_actions_card(self, parent):
        card = self._make_card(parent, "ACTIONS", 0, 1)
        actions = tk.Frame(card, bg=CARD_BG)
        actions.pack(fill="x", pady=(10, 0))
        actions.grid_columnconfigure((0, 1), weight=1)

        self.run_now_btn = ttk.Button(actions, text="Run now", command=self.run_now, style="Primary.TButton")
        self.dashboard_btn = ttk.Button(actions, text="Open dashboard", command=self.open_dashboard, style="Primary.TButton")
        self.start_auto_btn = ttk.Button(actions, text="Start auto polling", command=self.start_auto, style="Secondary.TButton")
        self.stop_auto_btn = ttk.Button(actions, text="Stop auto polling", command=self.stop_auto, style="Secondary.TButton")
        self.settings_btn = ttk.Button(actions, text="Settings", command=self.open_settings, style="Secondary.TButton")
        self.updates_btn = ttk.Button(actions, text="Updates", command=self.open_updates, style="Secondary.TButton")
        self.diagnostics_btn = ttk.Button(actions, text="Diagnostics", command=self.open_diagnostics, style="Secondary.TButton")
        self.data_btn = ttk.Button(actions, text="Data folder", command=self.open_data_folder, style="Secondary.TButton")
        self.logs_btn = ttk.Button(actions, text="Logs", command=self.open_logs, style="Secondary.TButton")
        self.tray_btn = ttk.Button(actions, text="Hide to tray", command=self.hide_to_tray, style="Secondary.TButton")
        self.exit_btn = ttk.Button(actions, text="Exit app", command=self.exit_app, style="Secondary.TButton")

        layout = [
            (self.run_now_btn, 0, 0, 1),
            (self.dashboard_btn, 0, 1, 1),
            (self.start_auto_btn, 1, 0, 1),
            (self.stop_auto_btn, 1, 1, 1),
            (self.settings_btn, 2, 0, 1),
            (self.updates_btn, 2, 1, 1),
            (self.diagnostics_btn, 3, 0, 1),
            (self.data_btn, 3, 1, 1),
            (self.logs_btn, 4, 0, 1),
            (self.tray_btn, 4, 1, 1),
            (self.exit_btn, 5, 0, 2),
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
            tk.Label(tile, text=label, bg=CARD_ALT_BG, fg=SUBTEXT_FG, font=("Segoe UI", 8, "bold")).pack(anchor="w")
            tk.Label(tile, textvariable=var, bg=CARD_ALT_BG, fg=HEADER_FG, font=("Segoe UI", 10), wraplength=200, justify="left").pack(anchor="w", pady=(4, 0))

    def _build_log_card(self, parent):
        card = self._make_card(parent, "ACTIVITY", 2, 0, columnspan=2, weight=1)
        self.log_text = ScrolledText(card, height=14, wrap="word", bg="#0f1318", fg="#dde3eb", insertbackground="#ffffff", relief="flat")
        self.log_text.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text.configure(state="disabled")

    def _build_footer(self, shell):
        footer = tk.Frame(shell, bg="#0c0f13", padx=16, pady=10)
        footer.pack(fill="x")
        footer.grid_columnconfigure(0, weight=1)
        tk.Label(footer, textvariable=self.status_line_var, bg="#0c0f13", fg=SUBTEXT_FG, anchor="w").grid(row=0, column=0, sticky="ew")
        tk.Label(footer, text=f"{APP_TITLE} / {get_execution_mode()}", bg="#0c0f13", fg=SUBTEXT_FG, anchor="e").grid(row=0, column=1, sticky="e", padx=(12, 0))

    def _enqueue_log(self, message: str):
        self.log_queue.put(message)

    def _set_status_line(self, message: str):
        self.status_line_var.set(message)

    def _pump_logs(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
                self._set_status_line(line)
        except queue.Empty:
            pass
        self.after(400, self._pump_logs)

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

        self.after(1000, self._refresh_status)

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
        try:
            self.after(0, lambda: self._set_status_line(f"Update available: {latest}."))
        except tk.TclError:
            pass

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
            self.after(900, lambda: messagebox.showwarning(
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
                    lambda icon, item: self.after(0, self.show_from_tray),
                    default=True,
                    visible=False,
                ),
                pystray.MenuItem("Open", lambda icon, item: self.after(0, self.show_from_tray)),
                pystray.MenuItem("Run now", lambda icon, item: self.after(0, self.run_now)),
                pystray.MenuItem("Start auto polling", lambda icon, item: self.after(0, self.start_auto)),
                pystray.MenuItem("Stop auto polling", lambda icon, item: self.after(0, self.stop_auto)),
                pystray.MenuItem("Open dashboard", lambda icon, item: self.after(0, self.open_dashboard)),
                pystray.MenuItem("Diagnostics", lambda icon, item: self.after(0, self.open_diagnostics)),
                pystray.MenuItem("Updates", lambda icon, item: self.after(0, self.open_updates)),
                pystray.MenuItem("Exit", lambda icon, item: self.after(0, self.exit_app)),
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
        self.after(50, self.lift)
        self.after(100, self.focus_force)
        self.after(150, self._force_show_main_window)

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
            app.after(200, app.open_settings)
        app.mainloop()
        return 0
    finally:
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
