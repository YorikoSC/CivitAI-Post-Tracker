from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def load_yaml_config(path: str | None = None) -> dict[str, Any]:
    config_path = Path(path or "config.json")
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("config.json must contain a top-level JSON object")
    return data


def deep_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split('.'):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def choose(cli_value: Any, cfg_value: Any, default: Any = None) -> Any:
    if cli_value is not None:
        return cli_value
    if cfg_value is not None:
        return cfg_value
    return default


def read_api_key(inline_key: str | None, key_file: str | None) -> str | None:
    if inline_key:
        return inline_key.strip()
    if key_file:
        path = Path(key_file)
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    return None
