from __future__ import annotations

import argparse
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    pystray = None
    Image = None
    ImageDraw = None

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


APP_BG = "#101317"
CARD_BG = "#171b21"
HEADER_BG = "#8d1d24"
HEADER_FG = "#ffffff"
SUBTEXT_FG = "#b9c0cb"
STATUS_OK = "#37c871"
STATUS_RUN = "#f3c969"
STATUS_ERR = "#e25555"
STATUS_IDLE = "#7c8a9d"


def extract_post_id(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    for pattern in (r"/posts/(\d+)", r"[?&]postId=(\d+)"):
        match = re.search(pattern, value)
        if match:
            return int(match.group(1))
    return None


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
        self._add_help(self.app_tab, row, "Closing the window sends the app to the tray. Use the tray menu to exit fully.")

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


class TrackerApp(tk.Tk):
    def __init__(self, minimized: bool = False):
        super().__init__()
        self.title("CivitAI Tracker")
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

        if deep_get(self.config_data, "options.start_auto_polling_on_launch", False):
            self.after(700 if launch_hidden else 450, self._start_auto_on_launch)

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        shell = tk.Frame(self, bg=APP_BG)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=HEADER_BG, padx=18, pady=16)
        header.pack(fill="x")
        tk.Label(header, text="CivitAI Tracker", bg=HEADER_BG, fg=HEADER_FG, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(
            header,
            text="Local post analytics, auto polling, and dashboard generation for CivitAI.",
            bg=HEADER_BG,
            fg="#f4d9db",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(shell, bg=APP_BG, padx=16, pady=16)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(3, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        self._build_status_card(body)
        self._build_actions_card(body)
        self._build_notes_card(body)
        self._build_log_card(body)
        self._build_footer(shell)

    def _make_card(self, parent, title: str, row: int, column: int, *, columnspan: int = 1, weight: int = 0):
        frame = tk.Frame(parent, bg=CARD_BG, padx=14, pady=14, highlightthickness=1, highlightbackground="#252b34")
        frame.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=6, pady=6)
        if weight:
            parent.grid_rowconfigure(row, weight=weight)
        tk.Label(frame, text=title, bg=CARD_BG, fg=HEADER_FG, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        return frame

    def _build_status_card(self, parent):
        card = self._make_card(parent, "Tracker status", 0, 0)
        grid = tk.Frame(card, bg=CARD_BG)
        grid.pack(fill="x", pady=(10, 0))
        grid.grid_columnconfigure(1, weight=1)
        grid.grid_columnconfigure(3, weight=1)

        self.status_var = tk.StringVar(value="Idle")
        self.last_success_var = tk.StringVar(value="Never")
        self.next_run_var = tk.StringVar(value="Not scheduled")
        self.last_error_var = tk.StringVar(value="-")
        self.polling_var = tk.StringVar(value="Off")
        self.interval_var = tk.StringVar(value="15 minutes")
        self.status_dot = tk.Label(grid, text="●", bg=CARD_BG, fg=STATUS_IDLE, font=("Segoe UI", 12, "bold"))

        rows = [
            ("Status", self.status_var, True),
            ("Last successful run", self.last_success_var, False),
            ("Next scheduled run", self.next_run_var, False),
            ("Auto polling", self.polling_var, False),
            ("Polling interval", self.interval_var, False),
            ("Last error", self.last_error_var, False),
        ]
        for idx, (label, var, is_status) in enumerate(rows):
            r = idx // 2
            c = (idx % 2) * 2
            tk.Label(grid, text=label, bg=CARD_BG, fg=SUBTEXT_FG, font=("Segoe UI", 9)).grid(row=r, column=c, sticky="w", pady=4)
            if is_status:
                status_wrap = tk.Frame(grid, bg=CARD_BG)
                status_wrap.grid(row=r, column=c + 1, sticky="w", pady=4)
                self.status_dot.pack(in_=status_wrap, side="left")
                tk.Label(status_wrap, textvariable=var, bg=CARD_BG, fg=HEADER_FG, font=("Segoe UI", 10, "bold")).pack(side="left", padx=(6, 0))
            else:
                tk.Label(grid, textvariable=var, bg=CARD_BG, fg=HEADER_FG, wraplength=300, justify="left").grid(row=r, column=c + 1, sticky="w", pady=4)

    def _build_actions_card(self, parent):
        card = self._make_card(parent, "Quick actions", 0, 1)
        actions = tk.Frame(card, bg=CARD_BG)
        actions.pack(fill="x", pady=(10, 0))
        actions.grid_columnconfigure((0, 1), weight=1)

        self.run_now_btn = ttk.Button(actions, text="Run now", command=self.run_now)
        self.start_auto_btn = ttk.Button(actions, text="Start auto polling", command=self.start_auto)
        self.stop_auto_btn = ttk.Button(actions, text="Stop auto polling", command=self.stop_auto)
        self.settings_btn = ttk.Button(actions, text="Settings", command=self.open_settings)
        self.dashboard_btn = ttk.Button(actions, text="Open dashboard", command=self.open_dashboard)
        self.data_btn = ttk.Button(actions, text="Open data folder", command=self.open_data_folder)
        self.logs_btn = ttk.Button(actions, text="Open logs", command=self.open_logs)
        self.diagnostics_btn = ttk.Button(actions, text="Diagnostics", command=self.open_diagnostics)
        self.tray_btn = ttk.Button(actions, text="Hide to tray", command=self.hide_to_tray)

        buttons = [
            self.run_now_btn,
            self.start_auto_btn,
            self.stop_auto_btn,
            self.settings_btn,
            self.dashboard_btn,
            self.data_btn,
            self.logs_btn,
            self.diagnostics_btn,
            self.tray_btn,
        ]
        for i, btn in enumerate(buttons):
            btn.grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=4)

    def _build_notes_card(self, parent):
        card = self._make_card(parent, "How it behaves", 1, 0, columnspan=2)
        notes = [
            "Closing the window sends the app to the system tray instead of exiting.",
            "Auto polling keeps running while the app is hidden in the tray.",
            "Timezone must use IANA format, for example Europe/Moscow or America/New_York.",
            "Use API mode 'red' if you want full visibility for content above PG-13.",
            "Use Diagnostics if startup, config, or write-path issues are suspected.",
        ]
        for line in notes:
            tk.Label(card, text="• " + line, bg=CARD_BG, fg=SUBTEXT_FG, anchor="w", justify="left", wraplength=860).pack(fill="x", pady=2)

    def _build_log_card(self, parent):
        card = self._make_card(parent, "Activity log", 3, 0, columnspan=2, weight=1)
        self.log_text = ScrolledText(card, height=18, wrap="word", bg="#0f1318", fg="#dde3eb", insertbackground="#ffffff", relief="flat")
        self.log_text.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text.configure(state="disabled")

    def _build_footer(self, shell):
        footer = tk.Frame(shell, bg="#0c0f13", padx=16, pady=10)
        footer.pack(fill="x")
        self.status_line_var = tk.StringVar(value="Ready.")
        tk.Label(footer, textvariable=self.status_line_var, bg="#0c0f13", fg=SUBTEXT_FG, anchor="w").pack(fill="x")

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
        self.last_success_var.set(snap.last_success_at.strftime("%Y-%m-%d %H:%M:%S") if snap.last_success_at else "Never")
        self.next_run_var.set(snap.next_run_at.strftime("%Y-%m-%d %H:%M:%S") if snap.next_run_at else "Not scheduled")
        self.last_error_var.set(snap.last_error or "-")
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
        html_path = self.runtime_dir / deep_get(cfg, "paths.html", "dashboard.html")
        if not html_path.exists():
            messagebox.showinfo("Dashboard", f"Dashboard file not found:\n{html_path}")
            return
        webbrowser.open(html_path.resolve().as_uri())

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
        self.runner.set_app_mode("window")
        self.after(50, self.lift)
        self.after(100, self.focus_force)

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
    return parser


def main():
    args = build_parser().parse_args()
    app = TrackerApp(minimized=args.minimized)
    if args.setup:
        app.after(200, app.open_settings)
    app.mainloop()


if __name__ == "__main__":
    main()
