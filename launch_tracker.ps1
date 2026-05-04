param(
    [switch]$Minimized,
    [switch]$Setup
)

$ErrorActionPreference = "Stop"

function Show-TrackerError {
    param(
        [string]$Message,
        [string]$Details = ""
    )

    $body = $Message
    if (-not [string]::IsNullOrWhiteSpace($Details)) {
        $body = "$Message`n`n$Details"
    }

    try {
        Add-Type -AssemblyName PresentationFramework -ErrorAction Stop
        [System.Windows.MessageBox]::Show($body, "CivitAI Tracker", "OK", "Error") | Out-Null
    } catch {
        Write-Host $body
        Read-Host "Press Enter to close"
    }
}

try {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    if (-not $scriptDir) { $scriptDir = Get-Location }
    $scriptDir = (Resolve-Path $scriptDir).Path
    Set-Location $scriptDir

    $logDir = Join-Path $scriptDir "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $launcherLog = Join-Path $logDir "launcher_last.log"
    $stdoutLog = Join-Path $logDir "launcher_stdout.log"
    $stderrLog = Join-Path $logDir "launcher_stderr.log"
    Remove-Item -LiteralPath $stdoutLog, $stderrLog -Force -ErrorAction SilentlyContinue

    "[$(Get-Date -Format s)] launch_tracker.ps1 starting in $scriptDir" | Set-Content -Path $launcherLog -Encoding UTF8

    $consoleCandidates = @()
    foreach ($name in @("python.exe", "py.exe")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $consoleCandidates += [pscustomobject]@{
                Path = $cmd.Source
                LauncherArgs = if ($name -eq "py.exe") { @("-3") } else { @() }
                Name = $name
            }
        }
    }

    $guiCandidates = @()
    foreach ($name in @("pythonw.exe", "pyw.exe")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $guiCandidates += [pscustomobject]@{
                Path = $cmd.Source
                LauncherArgs = if ($name -eq "pyw.exe") { @("-3") } else { @() }
                Name = $name
            }
        }
    }

    $consoleCandidates = $consoleCandidates | Sort-Object Path -Unique
    $guiCandidates = $guiCandidates | Sort-Object Path -Unique
    if ((-not $consoleCandidates -or $consoleCandidates.Count -eq 0) -and (-not $guiCandidates -or $guiCandidates.Count -eq 0)) {
        Show-TrackerError "Python 3.11+ was not found. Please install Python and run the setup instructions."
        exit 1
    }

    $trackerScript = Join-Path $scriptDir "tracker_app.py"
    if (-not (Test-Path $trackerScript)) {
        Show-TrackerError "tracker_app.py was not found." "Expected path: $trackerScript"
        exit 1
    }

    $preflight = if ($consoleCandidates -and $consoleCandidates.Count -gt 0) { $consoleCandidates[0] } else { $null }
    if ($preflight) {
        $preflightArgs = @()
        $preflightArgs += $preflight.LauncherArgs
        $preflightArgs += @("-c", '"import tracker_app"')
        "[$(Get-Date -Format s)] preflight python: $($preflight.Path)" | Add-Content -Path $launcherLog -Encoding UTF8
        $check = Start-Process `
            -FilePath $preflight.Path `
            -ArgumentList $preflightArgs `
            -WorkingDirectory $scriptDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -Wait `
            -PassThru
        if ($check.ExitCode -ne 0) {
            $stderrText = if (Test-Path $stderrLog) { (Get-Content -Path $stderrLog -Raw -ErrorAction SilentlyContinue).Trim() } else { "" }
            $stdoutText = if (Test-Path $stdoutLog) { (Get-Content -Path $stdoutLog -Raw -ErrorAction SilentlyContinue).Trim() } else { "" }
            $details = @(
                "Python: $($preflight.Path)"
                "Exit code: $($check.ExitCode)"
                "stderr:"
                $stderrText
                "stdout:"
                $stdoutText
                "Log: $launcherLog"
            ) -join "`n"
            "[$(Get-Date -Format s)] preflight failed with code $($check.ExitCode)" | Add-Content -Path $launcherLog -Encoding UTF8
            Show-TrackerError "CivitAI Tracker cannot start from source." $details
            exit $check.ExitCode
        }
    }

    $selected = if ($guiCandidates -and $guiCandidates.Count -gt 0) { $guiCandidates[0] } else { $consoleCandidates[0] }
    $appArgs = @()
    $appArgs += $selected.LauncherArgs
    $appArgs += "`"$trackerScript`""
    if ($Minimized) { $appArgs += "--minimized" }
    if ($Setup) { $appArgs += "--setup" }

    "[$(Get-Date -Format s)] selected launcher: $($selected.Path)" | Add-Content -Path $launcherLog -Encoding UTF8
    "[$(Get-Date -Format s)] args: $($appArgs -join ' ')" | Add-Content -Path $launcherLog -Encoding UTF8

    $startInfo = @{
        FilePath = $selected.Path
        ArgumentList = $appArgs
        WorkingDirectory = $scriptDir
        PassThru = $true
    }

    if ($selected.Name -in @("python.exe", "py.exe")) {
        $startInfo.WindowStyle = "Hidden"
        $startInfo.RedirectStandardOutput = $stdoutLog
        $startInfo.RedirectStandardError = $stderrLog
    }

    $process = Start-Process @startInfo
    Start-Sleep -Milliseconds 1500
    $process.Refresh()

    if ($process.HasExited) {
        $stderrText = if (Test-Path $stderrLog) { (Get-Content -Path $stderrLog -Raw -ErrorAction SilentlyContinue).Trim() } else { "" }
        $stdoutText = if (Test-Path $stdoutLog) { (Get-Content -Path $stdoutLog -Raw -ErrorAction SilentlyContinue).Trim() } else { "" }
        $details = @(
            "Python: $($selected.Path)"
            "Exit code: $($process.ExitCode)"
            "stderr:"
            $stderrText
            "stdout:"
            $stdoutText
            "Log: $launcherLog"
        ) -join "`n"
        "[$(Get-Date -Format s)] process exited immediately with code $($process.ExitCode)" | Add-Content -Path $launcherLog -Encoding UTF8
        Show-TrackerError "CivitAI Tracker exited immediately." $details
        exit $process.ExitCode
    }

    "[$(Get-Date -Format s)] process started: $($process.Id)" | Add-Content -Path $launcherLog -Encoding UTF8
} catch {
    $message = $_.Exception.Message
    try {
        if ($launcherLog) {
            "[$(Get-Date -Format s)] launcher error: $message" | Add-Content -Path $launcherLog -Encoding UTF8
        }
    } catch {}
    Show-TrackerError "Failed to launch CivitAI Tracker." $message
    exit 1
}
