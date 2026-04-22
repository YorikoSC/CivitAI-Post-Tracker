from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path("config.json")


def ask(prompt: str, default: str | None = None, required: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default not in (None, "") else ""
        value = input(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            value = default
        if required and not value:
            print("This value is required.")
            continue
        return value


def ask_choice(prompt: str, choices: list[str], default: str) -> str:
    choices_str = "/".join(choices)
    while True:
        value = ask(f"{prompt} ({choices_str})", default=default, required=True).lower()
        if value in choices:
            return value
        print(f"Please choose one of: {', '.join(choices)}")


def extract_post_id(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    for pattern in (r"/posts/(\d+)", r"[?&]postId=(\d+)"):
        match = re.search(pattern, value)
        if match:
            return int(match.group(1))
    return None


def validate_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def main() -> None:
    print("=== CivitAI Tracker setup ===")
    print("A config.json file will be created.\n")

    username = ask("CivitAI username", required=True)
    display_name = ask("Display name", default=username)
    timezone = ask("Timezone", default="Europe/Moscow")

    print("\nChoose how to store your API key.")
    auth_mode = ask_choice("API key storage mode", ["inline", "file"], default="file")

    api_key = ""
    api_key_file = "api_key.txt"
    if auth_mode == "inline":
        api_key = ask("API key", required=True)
        api_key_file = ""
    else:
        api_key_file = ask("API key file name", default="api_key.txt", required=True)

    print("\nChoose how to define the tracking start point.")
    start_mode = ask_choice("Tracking start mode", ["post_id", "date"], default="post_id")

    start_post_id = None
    start_date = None
    if start_mode == "post_id":
        while True:
            raw = ask("Paste post ID or post URL", required=True)
            post_id = extract_post_id(raw)
            if post_id is not None:
                start_post_id = post_id
                break
            print("Could not extract post ID. Paste a number or a URL like https://civitai.red/posts/27925458")
    else:
        while True:
            raw = ask("Start date (YYYY-MM-DD)", required=True)
            if validate_date(raw):
                start_date = raw
                break
            print("Invalid date format. Use YYYY-MM-DD")

    poll_minutes = int(ask("Polling interval in minutes", default="15", required=True))
    db_path = ask("SQLite database file", default="civitai_tracker_v8_2.db")
    csv_dir = ask("CSV output directory", default="csv")
    html_path = ask("Dashboard HTML file", default="dashboard.html")

    api_mode = ask_choice("API mode", ["auto", "red", "com"], default="auto")
    view_host = ask("View host", default="https://civitai.red")
    nsfw_level = ask_choice("NSFW level", ["none", "soft", "mature", "x"], default="x").upper()
    allow_rest_fallback = ask_choice("Allow REST fallback", ["yes", "no"], default="no") == "yes"

    config = {
        "profile": {"username": username, "display_name": display_name, "timezone": timezone},
        "auth": {"api_key": api_key, "api_key_file": api_key_file},
        "tracking": {"start_mode": start_mode, "start_post_id": start_post_id, "start_date": start_date, "poll_minutes": poll_minutes},
        "api": {"mode": api_mode, "view_host": view_host, "nsfw_level": nsfw_level},
        "paths": {"db": db_path, "csv_dir": csv_dir, "html": html_path},
        "options": {"allow_rest_fallback": allow_rest_fallback},
    }

    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Created file: {CONFIG_PATH.resolve()}")
    if auth_mode == "file":
        key_file = Path(api_key_file)
        if not key_file.exists():
            print(f"Do not forget to create {key_file} and put your API key there as a single line.")


if __name__ == "__main__":
    main()
