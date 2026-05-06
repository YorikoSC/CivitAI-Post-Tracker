from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from app_info import APP_NAME, APP_VERSION, GITHUB_RELEASES_API, GITHUB_REPO


CHUNK_SIZE = 1024 * 256
VERSION_PATTERN = re.compile(r"(\d+(?:\.\d+){0,3})")
SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._ -]+")
URL_PATTERN = re.compile(r"https?://[^\s<>)\"']+", re.IGNORECASE)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", re.IGNORECASE)
MIRROR_LABEL_PATTERN = re.compile(
    r"^\s*(?:[-*]\s*)?(?:update package mirror|portable package mirror|package mirror|download mirror|mirror)\s*:\s*(.+)$",
    re.IGNORECASE,
)
APP_PACKAGE_TOKENS = ("civitaitracker", "civitai-tracker")
PORTABLE_PACKAGE_TOKENS = ("win", "windows", "exe", "onedir", "portable")
RUNTIME_PRESERVE_NAMES = (
    "config.json",
    "api_key.txt",
    "civitai_tracker.db",
    "csv",
    "logs",
    "dashboard.html",
    "runtime_status.json",
    ".civitai_tracker.lock",
    "updates",
    "release",
)


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0
    content_type: str = ""
    source: str = "github"


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
    mirror_assets: tuple[ReleaseAsset, ...] = ()
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


def _mirror_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    candidate = unquote(Path(parsed.path).name)
    if candidate.lower().endswith(".zip"):
        return safe_filename(candidate)
    return ""


def _mirror_fallback_name(latest_version: str, index: int) -> str:
    version = safe_filename(str(latest_version or APP_VERSION), fallback=APP_VERSION)
    suffix = f"-{index}" if index > 1 else ""
    return f"CivitAITracker-v{version}-win64-mirror{suffix}.zip"


def extract_mirror_assets(release_notes: str, latest_version: str) -> tuple[ReleaseAsset, ...]:
    assets: list[ReleaseAsset] = []
    for line in (release_notes or "").splitlines():
        label_match = MIRROR_LABEL_PATTERN.match(line)
        if not label_match:
            continue
        value = label_match.group(1).strip()
        markdown_match = MARKDOWN_LINK_PATTERN.search(value)
        if markdown_match:
            label = markdown_match.group(1).strip()
            url = markdown_match.group(2).strip()
        else:
            url_match = URL_PATTERN.search(value)
            if not url_match:
                continue
            label = ""
            url = url_match.group(0).rstrip(".,;")

        name = safe_filename(label) if label.lower().endswith(".zip") else _mirror_filename_from_url(url)
        if not name:
            name = _mirror_fallback_name(latest_version, len(assets) + 1)
        assets.append(ReleaseAsset(name=name, download_url=url, source="mirror"))
    return tuple(assets)


def _release_to_update_info(payload: dict, current_version: str) -> UpdateInfo:
    tag = str(payload.get("tag_name") or "")
    release_name = str(payload.get("name") or tag or "Latest release")
    latest_version = tag or release_name
    release_notes = str(payload.get("body") or "").strip()
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
        release_notes=release_notes,
        published_at=str(payload.get("published_at") or ""),
        prerelease=bool(payload.get("prerelease")),
        update_available=is_newer_version(latest_version, current_version),
        assets=assets,
        mirror_assets=extract_mirror_assets(release_notes, latest_version),
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


def _compact_asset_name(name: str) -> str:
    return name.lower().replace(" ", "").replace("_", "-")


def is_portable_release_asset(asset: ReleaseAsset) -> bool:
    if not asset.name.lower().endswith(".zip"):
        return False
    compact_name = _compact_asset_name(asset.name)
    return any(token in compact_name for token in APP_PACKAGE_TOKENS) and any(
        token in compact_name for token in PORTABLE_PACKAGE_TOKENS
    )


def choose_download_asset(info: UpdateInfo, execution_mode: str) -> ReleaseAsset | None:
    zip_assets = [asset for asset in (*info.mirror_assets, *info.assets) if asset.name.lower().endswith(".zip")]
    if not zip_assets:
        return None

    mode = (execution_mode or "").lower()
    if mode == "frozen":
        zip_assets = [asset for asset in zip_assets if is_portable_release_asset(asset)]
        if not zip_assets:
            return None
        preferred_tokens = [*APP_PACKAGE_TOKENS, *PORTABLE_PACKAGE_TOKENS]
    else:
        preferred_tokens = [*APP_PACKAGE_TOKENS, "source", "src"]

    def score(asset: ReleaseAsset) -> tuple[int, int, int]:
        compact_name = _compact_asset_name(asset.name)
        source_score = 1 if asset.source == "mirror" else 0
        token_hits = sum(1 for token in preferred_tokens if token in compact_name)
        size_score = min(asset.size, 2_000_000_000)
        return (source_score, token_hits, size_score)

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
    retry_attempts: int = 3,
    retry_delay_seconds: float = 1.5,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / safe_filename(asset.name)
    temp_target = target.with_suffix(target.suffix + ".part")
    attempts = max(1, retry_attempts)
    last_message = "Download failed."
    last_error: BaseException | None = None

    for attempt in range(1, attempts + 1):
        request = Request(
            asset.download_url,
            headers={"User-Agent": f"{APP_NAME.replace(' ', '-')}-updater/{APP_VERSION}"},
        )
        try:
            temp_target.unlink(missing_ok=True)
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
                if total > 0 and downloaded < total:
                    raise UpdateError(f"Download stopped early: {format_bytes(downloaded)} of {format_bytes(total)}.")
            shutil.move(str(temp_target), str(target))
            if progress:
                progress(target.stat().st_size, target.stat().st_size)
            return target
        except HTTPError as exc:
            last_message = f"Download failed with HTTP {exc.code}."
            last_error = exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            last_message = f"Download failed: {reason}"
            last_error = exc
        except TimeoutError as exc:
            last_message = "Download did not finish before the timeout."
            last_error = exc
        except OSError as exc:
            last_message = f"Download failed: {exc}"
            last_error = exc
        except UpdateError as exc:
            last_message = str(exc)
            last_error = exc

        try:
            temp_target.unlink(missing_ok=True)
        except OSError:
            pass
        if attempt < attempts:
            time.sleep(max(0, retry_delay_seconds) * attempt)

    raise UpdateError(f"{last_message} Tried {attempts} times.") from last_error


def release_asset_names(assets: Iterable[ReleaseAsset]) -> list[str]:
    return [asset.name for asset in assets]


def _normalize_zip_name(name: str) -> str:
    return name.replace("\\", "/").strip("/")


def _zip_parent(path: str) -> str:
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


def validate_portable_update_package(package_path: Path) -> str:
    if not package_path.exists():
        raise UpdateError(f"Update package was not found: {package_path}")

    try:
        with zipfile.ZipFile(package_path) as package:
            files = [
                _normalize_zip_name(info.filename)
                for info in package.infolist()
                if not info.is_dir() and _normalize_zip_name(info.filename)
            ]
    except zipfile.BadZipFile as exc:
        raise UpdateError("The downloaded update package is not a readable ZIP file.") from exc
    except OSError as exc:
        raise UpdateError(f"Could not read the update package: {exc}") from exc

    exe_roots = [
        _zip_parent(name)
        for name in files
        if name.lower() == "civitaitracker.exe" or name.lower().endswith("/civitaitracker.exe")
    ]
    if not exe_roots:
        raise UpdateError("The ZIP does not contain CivitAITracker.exe.")

    for root in exe_roots:
        internal_prefix = f"{root}/_internal/" if root else "_internal/"
        if any(name.startswith(internal_prefix) for name in files):
            return root or "."

    raise UpdateError("The ZIP contains CivitAITracker.exe but is missing the _internal app folder.")


def build_update_applier_script() -> str:
    preserve_items = ", ".join(f"'{name}'" for name in RUNTIME_PRESERVE_NAMES)
    return f"""param(
    [Parameter(Mandatory=$true)][string]$PackagePath,
    [Parameter(Mandatory=$true)][string]$AppDir,
    [Parameter(Mandatory=$true)][int]$PidToWait,
    [Parameter(Mandatory=$true)][string]$RestartPath,
    [Parameter(Mandatory=$true)][string]$LogPath
)

$ErrorActionPreference = 'Stop'
$preserve = @({preserve_items})

function Write-UpdateLog([string]$Message) {{
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$stamp] $Message"
    $logDir = Split-Path -Parent $LogPath
    if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {{
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    }}
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}}

function Copy-UpdateItem([string]$SourcePath, [string]$TargetPath) {{
    Copy-Item -LiteralPath $SourcePath -Destination $TargetPath -Recurse -Force
}}

Write-UpdateLog "Updater started."
$AppDir = (Resolve-Path -LiteralPath $AppDir).Path
$PackagePath = (Resolve-Path -LiteralPath $PackagePath).Path

if ($PidToWait -gt 0) {{
    Write-UpdateLog "Waiting for app process $PidToWait to exit."
    for ($i = 0; $i -lt 120; $i++) {{
        $proc = Get-Process -Id $PidToWait -ErrorAction SilentlyContinue
        if (-not $proc) {{
            break
        }}
        Start-Sleep -Milliseconds 500
    }}
    if (Get-Process -Id $PidToWait -ErrorAction SilentlyContinue) {{
        throw "The app did not close in time."
    }}
}}

$updatesDir = Join-Path $AppDir 'updates'
if (-not (Test-Path -LiteralPath $updatesDir)) {{
    New-Item -ItemType Directory -Force -Path $updatesDir | Out-Null
}}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$extractDir = Join-Path $updatesDir "extract-$stamp"
$backupDir = Join-Path $updatesDir "backup-$stamp"
New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

Write-UpdateLog "Extracting package $PackagePath."
Expand-Archive -LiteralPath $PackagePath -DestinationPath $extractDir -Force

$payloadRoot = $null
$exeCandidates = @(Get-ChildItem -LiteralPath $extractDir -Recurse -File -Filter 'CivitAITracker.exe')
foreach ($candidate in $exeCandidates) {{
    $candidateRoot = Split-Path -Parent $candidate.FullName
    if (Test-Path -LiteralPath (Join-Path $candidateRoot '_internal')) {{
        $payloadRoot = $candidateRoot
        break
    }}
}}
if (-not $payloadRoot) {{
    $children = @(Get-ChildItem -LiteralPath $extractDir -Force)
    if ($children.Count -eq 1 -and $children[0].PSIsContainer) {{
        $payloadRoot = $children[0].FullName
    }}
}}
if (-not $payloadRoot) {{
    throw "Could not find the application files in the update package."
}}
Write-UpdateLog "Payload root: $payloadRoot"

$moved = @()
$appliedTargets = @()
try {{
    foreach ($item in Get-ChildItem -LiteralPath $payloadRoot -Force) {{
        if ($preserve -contains $item.Name) {{
            Write-UpdateLog "Preserving runtime item $($item.Name)."
            continue
        }}
        $target = Join-Path $AppDir $item.Name
        $backupTarget = Join-Path $backupDir $item.Name
        if (Test-Path -LiteralPath $target) {{
            $backupParent = Split-Path -Parent $backupTarget
            if ($backupParent -and -not (Test-Path -LiteralPath $backupParent)) {{
                New-Item -ItemType Directory -Force -Path $backupParent | Out-Null
            }}
            Move-Item -LiteralPath $target -Destination $backupTarget -Force
            $moved += [pscustomobject]@{{ Target = $target; Backup = $backupTarget }}
        }}
        $appliedTargets += $target
        Copy-UpdateItem -SourcePath $item.FullName -TargetPath $target
        Write-UpdateLog "Updated $($item.Name)."
    }}
}} catch {{
    Write-UpdateLog "Update failed: $($_.Exception.Message)"
    foreach ($target in $appliedTargets) {{
        if (Test-Path -LiteralPath $target) {{
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
        }}
    }}
    for ($i = $moved.Count - 1; $i -ge 0; $i--) {{
        $entry = $moved[$i]
        if (Test-Path -LiteralPath $entry.Backup) {{
            Move-Item -LiteralPath $entry.Backup -Destination $entry.Target -Force
        }}
    }}
    throw
}}

Write-UpdateLog "Update applied successfully. Backup: $backupDir"
if (Test-Path -LiteralPath $RestartPath) {{
    Write-UpdateLog "Restarting app."
    Start-Process -FilePath $RestartPath -WorkingDirectory $AppDir
}}
"""


def write_update_applier_script(updates_dir: Path) -> Path:
    updates_dir.mkdir(parents=True, exist_ok=True)
    script_path = updates_dir / "apply_update.ps1"
    script_path.write_text(build_update_applier_script(), encoding="utf-8")
    return script_path


def launch_update_applier(
    *,
    package_path: Path,
    app_dir: Path,
    restart_path: Path,
    pid_to_wait: int,
) -> Path:
    if not sys.platform.startswith("win"):
        raise UpdateError("Automatic update apply is currently available on Windows only.")
    if not package_path.exists():
        raise UpdateError(f"Update package was not found: {package_path}")
    if not app_dir.exists():
        raise UpdateError(f"App folder was not found: {app_dir}")
    validate_portable_update_package(package_path)

    updates_dir = app_dir / "updates"
    script_path = write_update_applier_script(updates_dir)
    log_path = updates_dir / "update_apply.log"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-PackagePath",
        str(package_path),
        "-AppDir",
        str(app_dir),
        "-PidToWait",
        str(pid_to_wait),
        "-RestartPath",
        str(restart_path),
        "-LogPath",
        str(log_path),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(command, cwd=str(app_dir), creationflags=creationflags, close_fds=True)
    except OSError as exc:
        raise UpdateError(f"Could not start the update applier: {exc}") from exc
    return log_path
