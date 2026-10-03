# Juggler installer for Windows. Run via Install.bat (double-click).
# 1. makes sure Python 3.10+ is present (installs it per-user if not)
# 2. installs the Python packages
# 3. downloads Xray, Iran geo data and Tor into bin\
# 4. creates "Juggler" shortcuts on the Desktop and in the Start menu
$ErrorActionPreference = "Continue"  # native tools write to stderr; failures are checked explicitly
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Host.UI.RawUI.WindowTitle = "Juggler setup"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Step($n, $msg) { Write-Host ""; Write-Host " [$n/4] $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "       $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "       $msg" -ForegroundColor Gray }
function Fail($msg) {
    Write-Host ""; Write-Host " Setup could not finish: $msg" -ForegroundColor Red
    Write-Host " Fix the problem above and double-click Install.bat again." -ForegroundColor Yellow
    if (-not $env:JUGGLER_UNATTENDED) { Read-Host " Press Enter to close" }
    exit 1
}

function Find-Python {
    # The py launcher first, then python on PATH (skipping the Microsoft Store stub).
    foreach ($cand in @(@("py", "-3"), @("python"))) {
        $exe = $cand[0]; $args0 = @($cand | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $out = & $exe @args0 -c "import sys; print(sys.version_info[0]*100+sys.version_info[1]); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -and [int]$out[0] -ge 310) { return $out[1].Trim() }
        } catch { }
    }
    return $null
}

# Files from a downloaded ZIP carry the "from the internet" mark; clear it once so
# SmartScreen doesn't warn again on every launch of Juggler.bat, xray.exe or tor.exe.
Get-ChildItem -Path $Root -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "  JUGGLER setup" -ForegroundColor Magenta
Write-Host "  This takes a few minutes the first time. Keep this window open." -ForegroundColor Gray

# ---------- 1. Python ----------
Step 1 "Python"
$py = Find-Python
if (-not $py) {
    Info "Python 3.10+ not found, installing it (just for you, no admin needed)..."
    $installed = $false
    $bundled = Get-ChildItem (Join-Path $Root "installers\python-*.exe") -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($bundled) {
        Info "using the bundled installer $($bundled.Name)"
        Start-Process $bundled.FullName -Wait -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0"
        $installed = $true
    }
    if (-not $installed -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        try {
            winget install -e --id Python.Python.3.13 --scope user --silent `
                --accept-package-agreements --accept-source-agreements | Out-Host
            $installed = $true
        } catch { Info "winget failed, downloading from python.org instead" }
    }
    if (-not $installed) {
        $url = "https://www.python.org/ftp/python/3.13.12/python-3.13.12-amd64.exe"
        $exe = Join-Path $env:TEMP "python-installer.exe"
        Info "downloading $url"
        try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $exe }
        catch { Fail "could not download Python. Install it from https://www.python.org/downloads/ (tick 'Add to PATH')." }
        Start-Process $exe -Wait -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0"
    }
    # pick up the new PATH without reopening the window
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $py = Find-Python
    if (-not $py) { Fail "Python was installed but can't be found yet. Restart Windows, then run Install.bat again." }
}
Ok "using $py"

# ---------- 2. packages ----------
Step 2 "Python packages (Flask, qrcode)"
& $py -c "import flask, qrcode" 2>$null
if ($LASTEXITCODE -ne 0) {
    $ok = $false
    if (Test-Path (Join-Path $Root "wheels")) {   # release ZIP: install offline
        & $py -m pip install --user --disable-pip-version-check -q --no-index --find-links wheels -r requirements.txt
        $ok = ($LASTEXITCODE -eq 0)
    }
    if (-not $ok) {
        & $py -m pip install --user --disable-pip-version-check -q -r requirements.txt
        $ok = ($LASTEXITCODE -eq 0)
    }
    if (-not $ok) { Fail "pip could not install the packages (is the internet reachable? try with any VPN on)." }
}
Ok "ready"

# ---------- 3. Xray / geo data / Tor ----------
Step 3 "Xray, Iran geo data and Tor (about 70 MB, only once)"
& $py -m juggler.setup
if (-not (Test-Path (Join-Path $Root "bin\xray.exe"))) {
    Fail "Xray could not be downloaded (GitHub blocked?). Turn on any VPN for this one step, or copy the bin folder from another computer."
}
Ok "ready"

# ---------- 4. shortcuts ----------
Step 4 "Shortcuts"
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Juggler.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Juggler.lnk")
)
foreach ($lnk in $targets) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $Root "Juggler.bat"
    $s.WorkingDirectory = $Root
    $s.IconLocation = (Join-Path $Root "assets\juggler.ico") + ",0"
    $s.Description = "Juggler - self-hosted VPN"
    $s.Save()
}
# shortcut name used by older versions
Remove-Item (Join-Path ([Environment]::GetFolderPath("Desktop")) "Juggler VPN.lnk") -ErrorAction SilentlyContinue
Ok "Desktop and Start menu: Juggler"

Write-Host ""
Write-Host "  All set. From now on just open 'Juggler' from your Desktop or Start menu." -ForegroundColor Green
Write-Host "  Tip: turn off other VPNs while Juggler runs, so it tests your real connection." -ForegroundColor Gray
Write-Host ""
if ($env:JUGGLER_UNATTENDED) { exit 0 }
$a = Read-Host "  Start Juggler now? [Y/n]"
if ($a -eq "" -or $a -match "^[yY]") { Start-Process (Join-Path $Root "Juggler.bat") -WorkingDirectory $Root }
