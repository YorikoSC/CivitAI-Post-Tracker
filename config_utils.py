from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

CONFIG_FILE = "config.json"
EXAMPLE_CONFIG_FILE = "config.example.json"
AUTOSTART_FILE = "CivitAI_Post_Tracker_Autostart.vbs"
LAUNCHER_FILE = "launch_tracker.vbs"
TIMEZONE_EXAMPLES = [
    "UTC",
    "Europe/Moscow",
    "Europe/Berlin",
    "America/New_York",
    "Asia/Tokyo",
]


def deep_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(part)
        if current is None:
            return default
    return current


def choose(cli_value: Any, cfg_value: Any, default: Any = None) -> Any:
    if cli_value is not None:
        return cli_value
    if cfg_value is not None:
        return cfg_value
    return default


def default_config() -> dict[str, Any]:
    return {
        "profile": {
            "username": "",
            "display_name": "",
            "timezone": "UTC",
        },
        "auth": {
            "api_key": "",
            "api_key_file": "api_key.txt",
        },
        "tracking": {
            "start_mode": "post_id",
            "start_post_id": None,
            "start_date": None,
            "poll_minutes": 15,
        },
        "api": {
            "mode": "red",
            "view_host": "https://civitai.red",
            "nsfw_level": "X",
        },
        "paths": {
            "db": "civitai_tracker.db",
            "csv_dir": "csv",
            "html": "dashboard.html",
        },
        "options": {
            "allow_rest_fallback": False,
            "launch_with_windows": False,
            "start_minimized": False,
            "start_auto_polling_on_launch": False,
        },
    }


def load_json_config(path: str | Path = CONFIG_FILE) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("config.json must contain a top-level object")
    return data


def load_yaml_config(path: str | Path = CONFIG_FILE) -> dict[str, Any]:
    return load_json_config(path)


def save_json_config(data: dict[str, Any], path: str | Path = CONFIG_FILE) -> None:
    path = Path(path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def read_api_key(inline_key: str | None, key_file: str | None) -> str | None:
    if inline_key:
        return inline_key.strip()
    if key_file:
        path = Path(key_file)
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    return None


def materialize_api_key(config: dict[str, Any], provided_key: str | None, base_dir: str | Path = ".") -> None:
    if provided_key is None:
        return
    provided_key = provided_key.strip()
    if not provided_key:
        return
    inline = deep_get(config, "auth.api_key", "")
    key_file = deep_get(config, "auth.api_key_file", "api_key.txt")
    if inline:
        return
    if key_file:
        path = Path(base_dir) / key_file
        path.write_text(provided_key + "\n", encoding="utf-8")


def ensure_example_copied_if_missing(base_dir: str | Path = ".") -> None:
    base_dir = Path(base_dir)
    example = base_dir / EXAMPLE_CONFIG_FILE
    config = base_dir / CONFIG_FILE
    if config.exists() or not example.exists():
        return
    shutil.copyfile(example, config)


def is_valid_timezone_name(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    if value.upper() == "UTC":
        return True
    if ZoneInfo is None:
        return False
    try:
        ZoneInfo(value)
        return True
    except Exception:
        return False


def timezone_error_message() -> str:
    return (
        "Please use a valid IANA timezone, for example Europe/Moscow, Europe/Berlin, "
        "America/New_York, or Asia/Tokyo."
    )


def validate_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    username = deep_get(config, "profile.username", "")
    if not username:
        errors.append("profile.username is required")

    timezone_name = deep_get(config, "profile.timezone", "UTC")
    if not is_valid_timezone_name(timezone_name):
        errors.append("profile.timezone must be a valid IANA timezone, for example Europe/Moscow")

    start_mode = deep_get(config, "tracking.start_mode", "post_id")
    if start_mode not in {"post_id", "date"}:
        errors.append("tracking.start_mode must be 'post_id' or 'date'")

    if start_mode == "post_id":
        start_post_id = deep_get(config, "tracking.start_post_id")
        if start_post_id in (None, "", 0):
            errors.append("tracking.start_post_id is required when start_mode is 'post_id'")

    if start_mode == "date":
        start_date = deep_get(config, "tracking.start_date")
        if not start_date:
            errors.append("tracking.start_date is required when start_mode is 'date'")

    poll_minutes = deep_get(config, "tracking.poll_minutes", 15)
    try:
        poll_minutes = int(poll_minutes)
        if poll_minutes <= 0:
            raise ValueError
    except Exception:
        errors.append("tracking.poll_minutes must be a positive integer")

    mode = deep_get(config, "api.mode", "red")
    if mode not in {"red", "auto", "com"}:
        errors.append("api.mode must be red, auto, or com")

    return errors


def startup_folder() -> Path:
    appdata = Path(os.environ.get("APPDATA", str(Path.home())))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_script_path() -> Path:
    return startup_folder() / AUTOSTART_FILE


def launcher_script_path(base_dir: str | Path = ".") -> Path:
    return Path(base_dir).resolve() / LAUNCHER_FILE


def set_windows_autostart(enabled: bool, base_dir: str | Path = ".", start_minimized: bool = False) -> None:
    path = autostart_script_path()
    if not enabled:
        if path.exists():
            path.unlink()
        return

    base_dir = Path(base_dir).resolve()
    launcher = launcher_script_path(base_dir)
    if not launcher.exists():
        raise FileNotFoundError(f"Launcher script not found: {launcher}")

    workdir = str(base_dir).replace('"', '""')
    launcher_str = str(launcher).replace('"', '""')
    extra_args = " --minimized" if start_minimized else ""

    vbs = f'''Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "{workdir}"
shell.Run "wscript.exe " & chr(34) & "{launcher_str}" & chr(34) & "{extra_args}", 0
Set shell = Nothing
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(vbs, encoding="utf-8")


def autostart_enabled() -> bool:
    return autostart_script_path().exists()
