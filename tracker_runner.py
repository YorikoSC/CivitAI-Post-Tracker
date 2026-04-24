from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from config_utils import deep_get, load_json_config, validate_config

LogCallback = Callable[[str], None]


@dataclass
class RunnerState:
    is_running: bool = False
    auto_polling: bool = False
    status: str = "Idle"
    last_started_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_error: str = ""
    next_run_at: Optional[datetime] = None
    last_exit_code: Optional[int] = None
    selected_host: str = ""
    app_mode: str = "window"
    app_started_at: Optional[datetime] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class TrackerRunner:
    def __init__(self, base_dir: str | Path = ".", config_path: str = "config.json", log_callback: Optional[LogCallback] = None):
        self.base_dir = Path(base_dir).resolve()
        self.config_path = str((self.base_dir / config_path).resolve())
        self.core_script = self.base_dir / "tracker_core.py"
        self.log_callback = log_callback
        self.runtime_status_path = self.base_dir / "runtime_status.json"
        self.state = RunnerState(app_started_at=datetime.now())
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._persist_runtime_status(refresh_dashboard=False)

    def _log(self, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}"
        if self.log_callback:
            self.log_callback(line)
        logs_dir = self.base_dir / "logs"
        logs_dir.mkdir(exist_ok=True)
        with (logs_dir / "app.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _read_poll_minutes(self) -> int:
        cfg = load_json_config(self.config_path)
        poll = deep_get(cfg, "tracking.poll_minutes", 15)
        try:
            poll = int(poll)
        except Exception:
            poll = 15
        return max(1, poll)

    def _read_start_mode(self) -> str:
        cfg = load_json_config(self.config_path)
        return str(deep_get(cfg, "tracking.start_mode", "post_id"))

    def _to_iso(self, value: Optional[datetime]) -> str | None:
        return value.replace(microsecond=0).isoformat() if value else None

    def _extract_selected_host(self, text: str) -> str:
        match = re.search(r"host=(https?://[^\s]+)", text or "")
        return match.group(1) if match else ""

    def _refresh_dashboard_status(self) -> None:
        try:
            import tracker_core
            tracker_core.refresh_dashboard_from_config(self.config_path)
        except Exception as exc:
            self._log(f"Dashboard refresh skipped: {exc}")

    def _persist_runtime_status(self, refresh_dashboard: bool = False) -> None:
        cfg = load_json_config(self.config_path) if Path(self.config_path).exists() else {}
        payload = {
            "app_started_at": self._to_iso(self.state.app_started_at),
            "status": self.state.status,
            "auto_polling": bool(self.state.auto_polling),
            "poll_minutes": self._read_poll_minutes(),
            "last_started_at": self._to_iso(self.state.last_started_at),
            "last_success_at": self._to_iso(self.state.last_success_at),
            "next_run_at": self._to_iso(self.state.next_run_at),
            "last_error": self.state.last_error,
            "last_exit_code": self.state.last_exit_code,
            "selected_host": self.state.selected_host,
            "start_mode": self._read_start_mode(),
            "app_mode": self.state.app_mode,
            "config_path": self.config_path,
            "start_auto_polling_on_launch": bool(deep_get(cfg, "options.start_auto_polling_on_launch", False)),
        }
        self.runtime_status_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if refresh_dashboard:
            self._refresh_dashboard_status()

    def set_app_mode(self, mode: str) -> None:
        with self.state.lock:
            self.state.app_mode = mode
        self._persist_runtime_status(refresh_dashboard=True)

    def run_once(self) -> bool:
        with self.state.lock:
            if self.state.is_running:
                self._log("Skipped manual run: collection already in progress.")
                return False
            self.state.is_running = True
            self.state.status = "Running"
            self.state.last_started_at = datetime.now()
            self.state.last_error = ""
            self.state.last_exit_code = None
        self._persist_runtime_status(refresh_dashboard=True)

        try:
            if not self.core_script.exists():
                raise FileNotFoundError(f"tracker_core.py not found in {self.base_dir}")

            cfg = load_json_config(self.config_path)
            errors = validate_config(cfg)
            if errors:
                raise ValueError("Invalid config: " + "; ".join(errors))

            logs_dir = self.base_dir / "logs"
            logs_dir.mkdir(exist_ok=True)

            cmd = [sys.executable, str(self.core_script), "--config", self.config_path]
            self._log(f"Starting collection: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=str(self.base_dir),
                capture_output=True,
                text=True,
                timeout=1800,
            )

            core_log = logs_dir / "core_last.log"
            with core_log.open("w", encoding="utf-8") as f:
                if result.stdout:
                    f.write("=== STDOUT ===\n")
                    f.write(result.stdout)
                    if not result.stdout.endswith("\n"):
                        f.write("\n")
                if result.stderr:
                    f.write("=== STDERR ===\n")
                    f.write(result.stderr)
                    if not result.stderr.endswith("\n"):
                        f.write("\n")

            with self.state.lock:
                self.state.last_exit_code = result.returncode
                extracted_host = self._extract_selected_host((result.stdout or "") + "\n" + (result.stderr or ""))
                if extracted_host:
                    self.state.selected_host = extracted_host

            if result.returncode == 0:
                with self.state.lock:
                    self.state.last_success_at = datetime.now()
                    self.state.status = "Waiting" if self.state.auto_polling else "Idle"
                self._log("Collection finished successfully.")
                self._persist_runtime_status(refresh_dashboard=True)
                return True

            err = (result.stderr or result.stdout or f"Process exited with code {result.returncode}").strip()
            with self.state.lock:
                self.state.last_error = err
                self.state.status = "Error"
            self._log(f"Collection failed: {err}")
            self._persist_runtime_status(refresh_dashboard=True)
            return False

        except subprocess.TimeoutExpired:
            with self.state.lock:
                self.state.last_error = "Collection timed out after 30 minutes."
                self.state.status = "Error"
            self._log("Collection timed out after 30 minutes.")
            self._persist_runtime_status(refresh_dashboard=True)
            return False
        except Exception as exc:
            with self.state.lock:
                self.state.last_error = str(exc)
                self.state.status = "Error"
            self._log(f"Collection failed: {exc}")
            self._persist_runtime_status(refresh_dashboard=True)
            return False
        finally:
            with self.state.lock:
                self.state.is_running = False
            self._persist_runtime_status(refresh_dashboard=False)

    def _loop(self) -> None:
        self._log("Auto polling started.")
        with self.state.lock:
            self.state.auto_polling = True
            if self.state.status != "Error":
                self.state.status = "Waiting"
        self._persist_runtime_status(refresh_dashboard=True)

        while not self._stop_event.is_set():
            self.run_once()
            poll_minutes = self._read_poll_minutes()
            next_run = datetime.now() + timedelta(minutes=poll_minutes)
            with self.state.lock:
                self.state.next_run_at = next_run
                self.state.auto_polling = True
                if self.state.status != "Error":
                    self.state.status = "Waiting"
            self._persist_runtime_status(refresh_dashboard=True)

            end_at = time.monotonic() + poll_minutes * 60
            while time.monotonic() < end_at:
                if self._stop_event.wait(timeout=1):
                    break

        with self.state.lock:
            self.state.auto_polling = False
            self.state.next_run_at = None
            if self.state.status != "Error":
                self.state.status = "Idle"
        self._log("Auto polling stopped.")
        self._persist_runtime_status(refresh_dashboard=True)

    def start_auto_polling(self) -> bool:
        with self.state.lock:
            if self.state.auto_polling:
                return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop_auto_polling(self) -> bool:
        with self.state.lock:
            active = self.state.auto_polling
        if not active:
            return False
        self._stop_event.set()
        return True

    def snapshot(self) -> RunnerState:
        with self.state.lock:
            return RunnerState(
                is_running=self.state.is_running,
                auto_polling=self.state.auto_polling,
                status=self.state.status,
                last_started_at=self.state.last_started_at,
                last_success_at=self.state.last_success_at,
                last_error=self.state.last_error,
                next_run_at=self.state.next_run_at,
                last_exit_code=self.state.last_exit_code,
                selected_host=self.state.selected_host,
                app_mode=self.state.app_mode,
                app_started_at=self.state.app_started_at,
            )
