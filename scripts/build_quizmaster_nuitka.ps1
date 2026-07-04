<#
    Nuitka build for QuizMaster (parallel to the PyInstaller build in
    scripts/build_quizmaster.ps1). Nuitka compiles ALL Python -> machine code,
    so the whole app (not just the Cython gate) ships as compiled binaries that
    are far harder to decompile/clone than PyInstaller bytecode.

    Packaging: this builds a one-FOLDER app (dist\QuizMaster\QuizMaster.exe) by
    default, which the Inno installer wraps into a single QuizMasterSetup.exe --
    exactly like the PyInstaller path. This is the SAFE mode for a QtWebEngine
    app: the WebEngine helper process needs its resources in a stable folder
    beside the exe. A single-file exe (-Onefile) must self-extract the entire
    ~500 MB Qt/Chromium payload to a temp dir on every launch, which (a) makes
    the final build step crawl at "99%" while it compresses+scans that payload,
    and (b) is unreliable for WebEngine at runtime. Only use -Onefile if you
    specifically need a loose single exe and have verified it launches.

    Flags:
      -Onefile   : build a single-file exe (dist\QuizMaster.exe) instead of the
                   one-folder app. Not recommended for this app (see above).
      -Installer : after a one-folder build, compile installer\QuizMaster.iss
                   into installer\output\QuizMasterSetup-<ver>.exe (needs Inno
                   Setup 6). Ignored with -Onefile (the installer packs a folder).
      -Console   : keep a console window with live tracebacks (debug). Default
                   is a windowed release build with no console.

    Notes:
      * The Cython step is intentionally skipped: Nuitka compiles
        subscription_gate.py to machine code too, so the separate .pyd is
        redundant. Any stale .pyd is removed first so Nuitka compiles the .py.
      * Frontend HTML/CSS/JS is NOT shipped as loose files. It is embedded via
        core/resources/web_assets_bundle.py (generated below) + the compiled Qt
        resource module, so a copy of the install dir exposes no readable UI
        source. Do NOT add --include-data-dir for the frontend folders.
      * Assistant/CI cannot verify a Qt/WebEngine launch on non-Windows; the
        Windows CI workflow and the user's machine are the real verification.
#>
param(
    [switch]$Onefile,
    [switch]$Installer,
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$RequiredPythonVersion = "3.14"

function Resolve-BuildPython {
    $candidates = @(
        @{ Exe = "py"; Args = @("-$RequiredPythonVersion"); Label = "py -$RequiredPythonVersion" },
        @{ Exe = ".\.venv\Scripts\python.exe"; Args = @(); Label = ".venv python" },
        @{ Exe = "python"; Args = @(); Label = "python" },
        @{ Exe = "python3"; Args = @(); Label = "python3" }
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $versionOutput = (& $candidate.Exe @($candidate.Args) --version 2>&1) -join "`n"
        } catch {
            continue
        }
        if ($LASTEXITCODE -eq 0 -and $versionOutput -like "Python $RequiredPythonVersion.*") {
            return [pscustomobject]@{
                Exe = $candidate.Exe
                Args = $candidate.Args
                Label = $candidate.Label
                Version = $versionOutput.Trim()
            }
        }
    }

    throw "Python $RequiredPythonVersion was not found. Install Python $RequiredPythonVersion or create a matching .venv, then reopen PowerShell and rerun the build."
}

$Python = Resolve-BuildPython
$script:PythonExe = $Python.Exe
$script:PythonPrefixArgs = @($Python.Args)

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)] [string]$Step,
        [Parameter(Mandatory = $true)] [string[]]$Arguments
    )
    $allArgs = @()
    $allArgs += $script:PythonPrefixArgs
    $allArgs += $Arguments
    & $script:PythonExe @allArgs
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

function Resolve-InnoSetupCompiler {
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $programFilesX86 = [Environment]::GetFolderPath("ProgramFilesX86")
    $programFiles = [Environment]::GetFolderPath("ProgramFiles")
    $candidates = @(
        (Join-Path $programFilesX86 "Inno Setup 6\ISCC.exe"),
        (Join-Path $programFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $programFilesX86 "Inno Setup 5\ISCC.exe"),
        (Join-Path $programFiles "Inno Setup 5\ISCC.exe")
    ) | Where-Object { $_ -and $_.Trim() }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "Inno Setup compiler ISCC.exe was not found. The app build succeeded; install Inno Setup 6 and rerun with -Installer."
}

function Stop-OldBuildProcesses {
    foreach ($name in @("QuizMaster", "QtWebEngineProcess")) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                Write-Host "Stopping old process before clean: $($_.ProcessName) ($($_.Id))" -ForegroundColor Yellow
                Stop-Process -Id $_.Id -Force -ErrorAction Stop
            } catch {
                Write-Host "Could not stop $($_.ProcessName) ($($_.Id)). Close it manually if dist cleanup fails." -ForegroundColor Yellow
            }
        }
    }
    Start-Sleep -Milliseconds 750
}

function Remove-BuildFolder {
    param([Parameter(Mandatory = $true)] [string]$Path)
    if (-not (Test-Path $Path)) { return }
    try {
        Remove-Item $Path -Recurse -Force -ErrorAction Stop
    } catch {
        throw "Could not delete '$Path'. QuizMaster or QtWebEngine is still running and locking a DLL. Close every QuizMaster window and console, then run: Get-Process QuizMaster,QtWebEngineProcess -ErrorAction SilentlyContinue | Stop-Process -Force"
    }
}

if ($Installer -and $Onefile) {
    throw "-Installer packages the one-folder app and cannot be combined with -Onefile. Drop -Onefile to build the installable one-folder app."
}

$modeLabel = if ($Console) { "DEBUG (console window, error tracing)" } else { "RELEASE (windowed, hardened)" }
$packLabel = if ($Onefile) { "single exe (--onefile, not recommended for WebEngine)" } else { "one-folder app (--standalone)" }
Write-Host "Building QuizMaster with Nuitka -- $modeLabel -- $packLabel" -ForegroundColor Cyan
Write-Host "Using Python: $($Python.Label) ($($Python.Version))" -ForegroundColor Green

$env:QUIZMASTER_MODE = "release"
$env:QUIZMASTER_AUTH_MODE = "account"

Invoke-Python -Step "Upgrade pip" -Arguments @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Python -Step "Install requirements" -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
Invoke-Python -Step "Install Nuitka toolchain" -Arguments @("-m", "pip", "install", "nuitka", "ordered-set", "zstandard")

Stop-OldBuildProcesses
Remove-BuildFolder -Path "build"
Remove-BuildFolder -Path "dist"

# Embed the frontend so no loose HTML/CSS/JS/assets ship next to the exe.
Invoke-Python -Step "Generate web asset bundle" -Arguments @("scripts/generate_web_assets_bundle.py")

# Nuitka compiles subscription_gate.py itself; a stale Cython .pyd would be
# imported (and merely copied) instead of compiled, so remove it first.
Get-ChildItem "core/services" -Filter "subscription_gate*.pyd" -ErrorAction SilentlyContinue | Remove-Item -Force

$consoleMode = if ($Console) { "force" } else { "disable" }
$packMode = if ($Onefile) { "--onefile" } else { "--standalone" }

$nuitkaArgs = @(
    "-m", "nuitka",
    $packMode,
    "--assume-yes-for-downloads",
    # Compile with clang-cl instead of MSVC. TikTokLive.proto.tiktok_proto is a
    # single ~79k-line generated protobuf module that makes MSVC 'cl' die with
    # "C1002: out of heap space in pass 2". clang handles the huge translation
    # unit, and clang-cl keeps the MSVC runtime/headers so Qt + QtWebEngine
    # linkage is unaffected (unlike swapping to a full MinGW toolchain).
    "--clang",
    "--enable-plugin=pyside6",
    "--windows-console-mode=$consoleMode",
    "--windows-icon-from-ico=core/assets/images/icon.ico",
    "--company-name=LiveForge",
    "--product-name=QuizMaster",
    "--product-version=1.0.0",
    "--file-version=1.0.0",
    # Runtime data. Frontend folders are deliberately NOT included -- they are
    # embedded via web_assets_bundle + the Qt resource module (anti-copy).
    "--include-data-files=config/production.env=config/production.env",
    "--include-data-files=License.txt=License.txt",
    "--include-data-files=THIRD_PARTY_LICENSES.txt=THIRD_PARTY_LICENSES.txt",
    # ServiceRegistry imports services by dotted string, so force whole packages.
    "--include-package=core",
    "--include-package=config",
    "--include-package=uvicorn",
    "--include-package=fastapi",
    "--include-package=socketio",
    "--include-package=engineio",
    # Import name is TikTokLive (capitalized); the pip dist is 'tiktoklive'.
    # Nuitka resolves --include-package by import name, so it must match the
    # package directory casing or it fails with "failed to locate package".
    "--include-package=TikTokLive",
    "--include-package=keyring",
    "--include-module=core.resources.web_assets_bundle",
    "--include-module=core.services.subscription_gate",
    "--output-dir=dist",
    "--output-filename=QuizMaster.exe",
    "main.py"
)

if ($Onefile) {
    Write-Host "Running Nuitka onefile (slow -- the final single-file compression of the Qt/Chromium payload can sit at ~99% for many minutes)..." -ForegroundColor Cyan
} else {
    Write-Host "Running Nuitka standalone (this is slow -- 20-40 min is normal)..." -ForegroundColor Cyan
}
Invoke-Python -Step "Nuitka build" -Arguments $nuitkaArgs

if ($Onefile) {
    $exe = Join-Path $Root "dist\QuizMaster.exe"
    if (-not (Test-Path $exe)) { throw "Build failed: dist\QuizMaster.exe was not produced." }
    $distRoot = $null
} else {
    # Nuitka names the standalone folder after the entry module (main.dist);
    # older/other setups may produce QuizMaster.dist. Normalize to dist\QuizMaster
    # so the Inno installer (which packages ..\dist\QuizMaster\*) consumes it
    # unchanged, matching the PyInstaller output layout.
    $distRoot = Join-Path $Root "dist\QuizMaster"
    $produced = @("dist\main.dist", "dist\QuizMaster.dist") |
        ForEach-Object { Join-Path $Root $_ } |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1
    if (-not $produced) { throw "Build failed: no Nuitka standalone folder (dist\main.dist) was produced." }
    if ((Resolve-Path $produced).Path -ne $distRoot) {
        if (Test-Path $distRoot) { Remove-BuildFolder -Path $distRoot }
        Rename-Item -Path $produced -NewName "QuizMaster"
    }
    $exe = Join-Path $distRoot "QuizMaster.exe"
    if (-not (Test-Path $exe)) { throw "Build failed: $exe was not produced." }
}

Write-Host "Nuitka build complete: $exe" -ForegroundColor Green
if ($Console) {
    Write-Host "DEBUG build -- run it from THIS terminal to see errors:" -ForegroundColor Yellow
    Write-Host "    $exe" -ForegroundColor Yellow
}
Write-Host "Everything (including the subscription gate) is compiled to machine code; no readable app .py source ships in this build." -ForegroundColor Yellow

if ($Installer) {
    $iscc = Resolve-InnoSetupCompiler
    $innoArgs = @("installer\QuizMaster.iss")
    if ($Console) { $innoArgs = @("/DConsoleBuild=1") + $innoArgs }
    Write-Host "Using Inno Setup compiler: $iscc" -ForegroundColor Green
    if ($Console) { Write-Host "Compiling CONSOLE debug installer..." -ForegroundColor Cyan }
    else { Write-Host "Compiling installer..." -ForegroundColor Cyan }
    & $iscc @innoArgs
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE." }
    Write-Host "Installer written to installer\output\" -ForegroundColor Green
}
