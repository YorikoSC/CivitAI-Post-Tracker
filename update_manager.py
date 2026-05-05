from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app_info import APP_NAME, APP_VERSION, GITHUB_RELEASES_API, GITHUB_REPO


CHUNK_SIZE = 1024 * 256
VERSION_PATTERN = re.compile(r"(\d+(?:\.\d+){0,3})")
SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0
    content_type: str = ""


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    latest_tag: str
    release_name: str
    release_url: str
    release_notes: str
    published_at: str
    prerelease: bool
    update_available: bool
    assets: tuple[ReleaseAsset, ...]
    zipball_url: str = ""


class UpdateError(RuntimeError):
    pass


def version_key(value: str) -> tuple[int, int, int, int]:
    match = VERSION_PATTERN.search(value or "")
    if not match:
        return (0, 0, 0, 0)
    parts = [int(part) for part in match.group(1).split(".")]
    return tuple((parts + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    return version_key(candidate) > version_key(current)


def _request_json(url: str, timeout_seconds: int) -> object:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{APP_NAME.replace(' ', '-')}-update-checker/{APP_VERSION}",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise UpdateError(f"GitHub returned HTTP {exc.code}.") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"Could not reach GitHub: {reason}") from exc
    except TimeoutError as exc:
        raise UpdateError("GitHub did not respond before the timeout.") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError("GitHub returned an unreadable response.") from exc


def _release_to_update_info(payload: dict, current_version: str) -> UpdateInfo:
    tag = str(payload.get("tag_name") or "")
    release_name = str(payload.get("name") or tag or "Latest release")
    latest_version = tag or release_name
    assets = tuple(
        ReleaseAsset(
            name=str(asset.get("name") or ""),
            download_url=str(asset.get("browser_download_url") or ""),
            size=int(asset.get("size") or 0),
            content_type=str(asset.get("content_type") or ""),
        )
        for asset in payload.get("assets", [])
        if isinstance(asset, dict) and asset.get("browser_download_url")
    )
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        latest_tag=tag,
        release_name=release_name,
        release_url=str(payload.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"),
        release_notes=str(payload.get("body") or "").strip(),
        published_at=str(payload.get("published_at") or ""),
        prerelease=bool(payload.get("prerelease")),
        update_available=is_newer_version(latest_version, current_version),
        assets=assets,
        zipball_url=str(payload.get("zipball_url") or ""),
    )


def fetch_latest_release(
    *,
    current_version: str = APP_VERSION,
    include_prerelease: bool = False,
    timeout_seconds: int = 20,
) -> UpdateInfo:
    payload = _request_json(f"{GITHUB_RELEASES_API}?per_page=10", timeout_seconds)
    if not isinstance(payload, list):
        raise UpdateError("GitHub returned an unexpected release list.")

    for release in payload:
        if not isinstance(release, dict):
            continue
        if release.get("draft"):
            continue
        if release.get("prerelease") and not include_prerelease:
            continue
        return _release_to_update_info(release, current_version)

    raise UpdateError("No public releases were found.")


def choose_download_asset(info: UpdateInfo, execution_mode: str) -> ReleaseAsset | None:
    zip_assets = [asset for asset in info.assets if asset.name.lower().endswith(".zip")]
    if not zip_assets:
        return None

    mode = (execution_mode or "").lower()
    preferred_tokens = ["civitaitracker", "civitai-tracker", "civitai_tracker"]
    if mode == "frozen":
        preferred_tokens.extend(["win", "windows", "exe", "onedir"])
    else:
        preferred_tokens.extend(["source", "src"])

    def score(asset: ReleaseAsset) -> tuple[int, int]:
        compact_name = asset.name.lower().replace(" ", "").replace("_", "-")
        token_hits = sum(1 for token in preferred_tokens if token in compact_name)
        size_score = min(asset.size, 2_000_000_000)
        return (token_hits, size_score)

    return max(zip_assets, key=score)


def safe_filename(name: str, fallback: str = "CivitAITracker-update.zip") -> str:
    cleaned = SAFE_FILENAME_PATTERN.sub("_", Path(name).name).strip(" ._")
    return cleaned or fallback


def format_bytes(size: int) -> str:
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(size)} B"


def download_asset(
    asset: ReleaseAsset,
    destination_dir: Path,
    *,
    timeout_seconds: int = 60,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / safe_filename(asset.name)
    temp_target = target.with_suffix(target.suffix + ".part")
    request = Request(
        asset.download_url,
        headers={"User-Agent": f"{APP_NAME.replace(' ', '-')}-updater/{APP_VERSION}"},
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            total = int(response.headers.get("Content-Length") or asset.size or 0)
            downloaded = 0
            with temp_target.open("wb") as output:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(downloaded, total)
        shutil.move(str(temp_target), str(target))
        if progress:
            progress(target.stat().st_size, target.stat().st_size)
        return target
    except HTTPError as exc:
        raise UpdateError(f"Download failed with HTTP {exc.code}.") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(f"Download failed: {reason}") from exc
    except TimeoutError as exc:
        raise UpdateError("Download did not finish before the timeout.") from exc
    finally:
        try:
            temp_target.unlink(missing_ok=True)
        except OSError:
            pass


def release_asset_names(assets: Iterable[ReleaseAsset]) -> list[str]:
    return [asset.name for asset in assets]

