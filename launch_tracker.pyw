from __future__ import annotations

import os
import site
import sys
import traceback
from datetime import datetime
from pathlib import Path


APP_NAME = "CivitAI Tracker"


def _base_dir() -> Path:
    return Path(__file__).resolve().parent


def _launcher_log(base_dir: Path) -> Path:
    log_dir = base_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    return log_dir / "launcher_last.log"


def _write_log(log_path: Path, message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def _show_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, message, parent=root)
        root.destroy()
    except Exception:
        pass


def _add_local_venv_site(base_dir: Path) -> None:
    venv_site = base_dir / ".venv" / "Lib" / "site-packages"
    if venv_site.exists():
        site.addsitedir(str(venv_site))
    venv_scripts = base_dir / ".venv" / "Scripts"
    if venv_scripts.exists():
        os.environ["PATH"] = str(venv_scripts) + os.pathsep + os.environ.get("PATH", "")


def main() -> int:
    base_dir = _base_dir()
    os.chdir(base_dir)
    log_path = _launcher_log(base_dir)
    log_path.write_text("", encoding="utf-8")
    _write_log(log_path, f"launch_tracker.pyw starting in {base_dir}")

    tracker_script = base_dir / "tracker_app.py"
    if not tracker_script.exists():
        message = f"tracker_app.py was not found.\n\nExpected path: {tracker_script}"
        _write_log(log_path, message.replace("\n", " "))
        _show_error(message)
        return 1

    try:
        _add_local_venv_site(base_dir)
        if str(base_dir) not in sys.path:
            sys.path.insert(0, str(base_dir))
        sys.argv = [str(tracker_script), *sys.argv[1:]]
        _write_log(log_path, f"args: {' '.join(sys.argv[1:])}")
        import tracker_app

        tracker_app.main()
        return 0
    except Exception:
        details = traceback.format_exc()
        _write_log(log_path, details)
        _show_error(f"Failed to launch CivitAI Tracker.\n\nSee logs\\launcher_last.log for details.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
