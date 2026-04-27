from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

CONFIG_FILE = "config.json"
EXAMPLE_CONFIG_FILE = "config.example.json"
AUTOSTART_SHORTCUT_NAME = "CivitAI Post Tracker.lnk"
POWERSHELL_LAUNCHER_FILE = "launch_tracker.ps1"
TIMEZONE_EXAMPLES = [
    "UTC",
    "Europe/Moscow",
    "Europe/Berlin",
    "America/New_York",
    "Asia/Tokyo",
]


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_execution_mode() -> str:
    return "frozen" if is_frozen_app() else "source"


def get_app_base_dir(anchor_file: str | Path | None = None) -> Path:
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    if anchor_file is not None:
        return Path(anchor_file).resolve().parent
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if main_file:
        return Path(main_file).resolve().parent
    return Path.cwd().resolve()


def get_runtime_data_dir(anchor_file: str | Path | None = None) -> Path:
    # Stage 3 policy: keep runtime data alongside the source tree or EXE bundle.
    return get_app_base_dir(anchor_file)


def get_default_config_path(anchor_file: str | Path | None = None) -> Path:
    return get_runtime_data_dir(anchor_file) / CONFIG_FILE


def get_default_logs_dir(anchor_file: str | Path | None = None) -> Path:
    return get_runtime_data_dir(anchor_file) / "logs"


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
    path.parent.mkdir(parents=True, exist_ok=True)
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(provided_key + "\n", encoding="utf-8")


def ensure_example_copied_if_missing(runtime_dir: str | Path = ".", template_dir: str | Path | None = None) -> None:
    runtime_dir = Path(runtime_dir)
    template_root = Path(template_dir) if template_dir is not None else runtime_dir
    example = template_root / EXAMPLE_CONFIG_FILE
    config = runtime_dir / CONFIG_FILE
    if config.exists() or not example.exists():
        return
    runtime_dir.mkdir(parents=True, exist_ok=True)
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


def autostart_shortcut_path() -> Path:
    return startup_folder() / AUTOSTART_SHORTCUT_NAME


def source_launcher_path(base_dir: str | Path = ".") -> Path:
    return Path(base_dir).resolve() / POWERSHELL_LAUNCHER_FILE


def autostart_enabled() -> bool:
    return autostart_shortcut_path().exists()


def _escape_ps_single(value: str) -> str:
    return value.replace("'", "''")


def _create_windows_shortcut(shortcut_path: Path, target: str, arguments: str, working_dir: str, icon_location: str | None = None) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    icon_location = icon_location or target
    ps_script = f"""
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut('{_escape_ps_single(str(shortcut_path))}')
$sc.TargetPath = '{_escape_ps_single(target)}'
$sc.Arguments = '{_escape_ps_single(arguments)}'
$sc.WorkingDirectory = '{_escape_ps_single(working_dir)}'
$sc.IconLocation = '{_escape_ps_single(icon_location)}'
$sc.Save()
""".strip()
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps_script,
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Unknown PowerShell error").strip()
        raise RuntimeError(f"Failed to create Windows startup shortcut: {message}")


def set_windows_autostart(enabled: bool, base_dir: str | Path = ".", start_minimized: bool = False) -> None:
    shortcut = autostart_shortcut_path()
    if not enabled:
        if shortcut.exists():
            shortcut.unlink()
        return

    base_dir = Path(base_dir).resolve()
    minimized_arg = " --minimized" if start_minimized else ""

    if is_frozen_app():
        target = str(Path(sys.executable).resolve())
        arguments = minimized_arg.strip()
        working_dir = str(base_dir)
        _create_windows_shortcut(shortcut, target, arguments, working_dir, icon_location=target)
        return

    launcher = source_launcher_path(base_dir)
    if not launcher.exists():
        raise FileNotFoundError(f"Source launcher not found: {launcher}")

    target = "powershell.exe"
    ps_args = [
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", str(launcher),
    ]
    if start_minimized:
        ps_args.append("-Minimized")
    arguments = " ".join(f'"{arg}"' if " " in arg else arg for arg in ps_args)
    _create_windows_shortcut(shortcut, target, arguments, str(base_dir), icon_location=target)



def _can_write_to_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="civitai_tracker_", suffix=".tmp", dir=path, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        tmp_path.unlink(missing_ok=True)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def run_startup_self_check(
    runtime_dir: str | Path,
    bundle_dir: str | Path,
    config_path: str | Path,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_dir = Path(runtime_dir).resolve()
    bundle_dir = Path(bundle_dir).resolve()
    config_path = Path(config_path).resolve()
    config = config or {}

    result: dict[str, Any] = {
        "ok": True,
        "critical": [],
        "warnings": [],
        "info": [],
        "details": {},
    }

    def add_critical(message: str) -> None:
        result["critical"].append(message)
        result["ok"] = False

    def add_warning(message: str) -> None:
        result["warnings"].append(message)

    def add_info(message: str) -> None:
        result["info"].append(message)

    result["details"].update({
        "execution_mode": get_execution_mode(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "bundle_dir": str(bundle_dir),
        "runtime_dir": str(runtime_dir),
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
    })

    if sys.version_info < (3, 11):
        add_critical("Python 3.11 or newer is required for supported source mode.")

    runtime_ok, runtime_msg = _can_write_to_dir(runtime_dir)
    result["details"]["runtime_dir_writable"] = runtime_ok
    if not runtime_ok:
        add_critical(f"Runtime directory is not writable: {runtime_msg}")

    logs_dir = get_default_logs_dir(runtime_dir)
    logs_ok, logs_msg = _can_write_to_dir(logs_dir)
    result["details"]["logs_dir"] = str(logs_dir)
    result["details"]["logs_dir_writable"] = logs_ok
    if not logs_ok:
        add_warning(f"Logs directory is not writable: {logs_msg}")

    if not config_path.exists():
        add_warning("Config file does not exist yet. Open Settings and save your configuration.")
        result["critical_count"] = len(result["critical"])
        result["warning_count"] = len(result["warnings"])
        result["info_count"] = len(result["info"])
        return result

    username = str(deep_get(config, "profile.username", "") or "").strip()
    result["details"]["username_configured"] = bool(username)
    if not username:
        add_warning("Username is not configured yet.")

    timezone_name = str(deep_get(config, "profile.timezone", "UTC") or "UTC").strip()
    result["details"]["timezone"] = timezone_name
    if not is_valid_timezone_name(timezone_name):
        add_warning(timezone_error_message())

    try:
        poll_minutes = int(deep_get(config, "tracking.poll_minutes", 15))
        if poll_minutes <= 0:
            raise ValueError
    except Exception:
        add_warning("Polling interval is invalid. Set a positive integer number of minutes.")

    start_mode = deep_get(config, "tracking.start_mode", "post_id")
    result["details"]["start_mode"] = start_mode
    if start_mode == "post_id" and not deep_get(config, "tracking.start_post_id"):
        add_warning("Start mode is set to post_id, but no start post is configured.")
    if start_mode == "date" and not deep_get(config, "tracking.start_date"):
        add_warning("Start mode is set to date, but no start date is configured.")

    inline_key = str(deep_get(config, "auth.api_key", "") or "").strip()
    key_file = str(deep_get(config, "auth.api_key_file", "api_key.txt") or "api_key.txt").strip()
    key_path = (runtime_dir / key_file).resolve() if key_file else None
    api_key = read_api_key(inline_key, str(key_path) if key_path else None)
    result["details"]["api_key_mode"] = "inline" if inline_key else "file"
    result["details"]["api_key_file"] = str(key_path) if key_path else ""
    result["details"]["api_key_available"] = bool(api_key)
    if not api_key:
        add_warning("API key is not available yet.")
    elif key_path is not None and not inline_key and not key_path.exists():
        add_warning(f"API key file is configured but missing: {key_path}")

    html_rel = str(deep_get(config, "paths.html", "dashboard.html") or "dashboard.html")
    html_parent = (runtime_dir / html_rel).resolve().parent
    html_ok, html_msg = _can_write_to_dir(html_parent)
    result["details"]["dashboard_parent"] = str(html_parent)
    result["details"]["dashboard_parent_writable"] = html_ok
    if not html_ok:
        add_warning(f"Dashboard output directory is not writable: {html_msg}")

    db_rel = str(deep_get(config, "paths.db", "civitai_tracker.db") or "civitai_tracker.db")
    db_parent = (runtime_dir / db_rel).resolve().parent
    db_ok, db_msg = _can_write_to_dir(db_parent)
    result["details"]["db_parent"] = str(db_parent)
    result["details"]["db_parent_writable"] = db_ok
    if not db_ok:
        add_warning(f"Database directory is not writable: {db_msg}")

    if is_frozen_app():
        add_info("Running in frozen mode.")
    else:
        add_info("Running in source mode.")

    result["critical_count"] = len(result["critical"])
    result["warning_count"] = len(result["warnings"])
    result["info_count"] = len(result["info"])
    return result


def startup_check_summary(report: dict[str, Any]) -> str:
    critical = int(report.get("critical_count", 0))
    warnings = int(report.get("warning_count", 0))
    if critical:
        return f"Startup self-check found {critical} critical issue(s) and {warnings} warning(s)."
    if warnings:
        return f"Startup self-check completed with {warnings} warning(s)."
    return "Startup self-check passed."


def format_startup_self_check(report: dict[str, Any]) -> str:
    lines = [startup_check_summary(report), "", "Environment"]
    details = report.get("details", {})
    for label, key in [
        ("Execution mode", "execution_mode"),
        ("Python", "python_version"),
        ("Bundle dir", "bundle_dir"),
        ("Runtime dir", "runtime_dir"),
        ("Config path", "config_path"),
    ]:
        value = details.get(key, "")
        lines.append(f"- {label}: {value}")

    lines.append("")
    lines.append("Checks")
    checks = [
        ("Config exists", details.get("config_exists")),
        ("Runtime dir writable", details.get("runtime_dir_writable")),
        ("Logs dir writable", details.get("logs_dir_writable")),
        ("Username configured", details.get("username_configured")),
        ("API key available", details.get("api_key_available")),
        ("Dashboard parent writable", details.get("dashboard_parent_writable")),
        ("DB parent writable", details.get("db_parent_writable")),
    ]
    for label, value in checks:
        pretty = "Yes" if bool(value) else "No"
        lines.append(f"- {label}: {pretty}")

    if report.get("critical"):
        lines.append("")
        lines.append("Critical issues")
        for item in report["critical"]:
            lines.append(f"- {item}")

    if report.get("warnings"):
        lines.append("")
        lines.append("Warnings")
        for item in report["warnings"]:
            lines.append(f"- {item}")

    if report.get("info"):
        lines.append("")
        lines.append("Info")
        for item in report["info"]:
            lines.append(f"- {item}")

    return "\n".join(lines).strip()
