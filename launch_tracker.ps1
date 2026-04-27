param(
    [switch]$Minimized,
    [switch]$Setup
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$pythonCandidates = @()
$pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if ($pythonw) { $pythonCandidates += $pythonw.Source }
$pyw = Get-Command pyw.exe -ErrorAction SilentlyContinue
if ($pyw) { $pythonCandidates += $pyw.Source }
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if ($python) { $pythonCandidates += $python.Source }
$py = Get-Command py.exe -ErrorAction SilentlyContinue
if ($py) { $pythonCandidates += $py.Source }

$pythonCandidates = $pythonCandidates | Select-Object -Unique
if (-not $pythonCandidates -or $pythonCandidates.Count -eq 0) {
    Add-Type -AssemblyName PresentationFramework -ErrorAction SilentlyContinue
    [System.Windows.MessageBox]::Show(
        "Python 3.11+ was not found. Please install Python and run the setup instructions.",
        "CivitAI Tracker",
        'OK',
        'Error'
    ) | Out-Null
    exit 1
}

$pyExe = $pythonCandidates[0]
$appArgs = @("tracker_app.py")
if ($Minimized) { $appArgs += "--minimized" }
if ($Setup) { $appArgs += "--setup" }

if ($pyExe.ToLower().EndsWith("py.exe")) {
    $appArgs = @("-3") + $appArgs
}

try {
    Start-Process -FilePath $pyExe -ArgumentList $appArgs -WorkingDirectory $scriptDir -WindowStyle Hidden | Out-Null
} catch {
    Add-Type -AssemblyName PresentationFramework -ErrorAction SilentlyContinue
    [System.Windows.MessageBox]::Show(
        "Failed to launch the tracker. `n`n$($_.Exception.Message)",
        "CivitAI Tracker",
        'OK',
        'Error'
    ) | Out-Null
    exit 1
}
