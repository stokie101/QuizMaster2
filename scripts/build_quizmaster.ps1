param(
    [switch]$Console,
    [switch]$Installer
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
    throw "Inno Setup compiler ISCC.exe was not found. The app exe build succeeded; install Inno Setup 6 and rerun with -Installer."
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

function Assert-CleanDist {
    param([Parameter(Mandatory = $true)] [string]$DistRoot)

    $forbidden = @(
        "data",
        "logs",
        "auth",
        "avatar_cache",
        "cache",
        "config\settings.ini",
        "_internal\data",
        "_internal\logs",
        "_internal\auth",
        "_internal\avatar_cache",
        "_internal\cache",
        "_internal\config\settings.ini"
    )

    $found = @()
    foreach ($relative in $forbidden) {
        $candidate = Join-Path $DistRoot $relative
        if (Test-Path $candidate) { $found += $relative }
    }

    if ($found.Count -gt 0) {
        throw "Refusing to package runtime/user data. Found in dist: $($found -join ', ')"
    }

    $sourceFiles = Get-ChildItem $DistRoot -Recurse -File -Include *.py,*.pyw -ErrorAction SilentlyContinue | Where-Object {
        $relative = $_.FullName.Substring((Resolve-Path $DistRoot).Path.Length).TrimStart('\', '/')
        $normalized = $relative.Replace('/', '\')
        $normalized -like 'core\*' -or
        $normalized -like '_internal\core\*' -or
        $normalized -like 'config\*' -or
        $normalized -like '_internal\config\*' -or
        $_.Name -in @('main.py', 'setup_cython.py', 'QuizMaster.spec')
    }

    if ($sourceFiles) {
        $sample = ($sourceFiles | Select-Object -First 20 | ForEach-Object {
            $_.FullName.Substring((Resolve-Path $DistRoot).Path.Length).TrimStart('\', '/')
        }) -join ', '
        throw "Refusing to package readable app Python source files. Found in dist: $sample"
    }

    Write-Host "Verified clean dist: no runtime/user data and no readable app .py/.pyw source bundled." -ForegroundColor Green
}

$modeLabel = if ($Console) { "DEBUG (console window, error tracing)" } else { "RELEASE (windowed, hardened)" }
Write-Host "Building QuizMaster -- $modeLabel" -ForegroundColor Cyan
Write-Host "Using Python: $($Python.Label) ($($Python.Version))" -ForegroundColor Green

$env:QUIZMASTER_MODE = "release"
$env:QUIZMASTER_AUTH_MODE = "account"
if ($Console) { $env:QUIZMASTER_DEBUG_CONSOLE = "1" }
else { Remove-Item Env:\QUIZMASTER_DEBUG_CONSOLE -ErrorAction SilentlyContinue }

Invoke-Python -Step "Upgrade pip" -Arguments @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Python -Step "Install requirements" -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
Invoke-Python -Step "Install build tools" -Arguments @("-m", "pip", "install", "cython", "setuptools", "pyinstaller", "pyinstaller-hooks-contrib")

Stop-OldBuildProcesses
Remove-BuildFolder -Path "build"
Remove-BuildFolder -Path "dist"

Invoke-Python -Step "Generate web asset bundle" -Arguments @("scripts/generate_web_assets_bundle.py")

Get-ChildItem "core/services" -Filter "subscription_gate*.pyd" -ErrorAction SilentlyContinue | Remove-Item -Force
Write-Host "Compiling security-sensitive modules with Cython..." -ForegroundColor Cyan
try {
    Invoke-Python -Step "Cython native gate build" -Arguments @("setup_cython.py")
} catch {
    if (-not $Console) { throw }
    Write-Host "WARNING: Cython native gate build failed. Continuing DEBUG build with the plain .py gate." -ForegroundColor Yellow
    Write-Host $_.Exception.Message -ForegroundColor Yellow
}

$gate = Get-ChildItem "core/services" -Filter "subscription_gate*.pyd" -ErrorAction SilentlyContinue
if (-not $gate) {
    if ($Console) {
        Write-Host "WARNING: gate not compiled. Continuing DEBUG build with the plain .py gate." -ForegroundColor Yellow
    } else {
        throw "Cython build failed: no subscription_gate .pyd produced. Refusing to ship the gate as source."
    }
} else {
    Write-Host "Native gate compiled: $($gate.Name)" -ForegroundColor Green
}

Invoke-Python -Step "PyInstaller build" -Arguments @("-m", "PyInstaller", ".\QuizMaster.spec", "--clean", "--noconfirm")

$distRoot = ".\dist\QuizMaster"
$exe = Join-Path $distRoot "QuizMaster.exe"
if (-not (Test-Path $exe)) { throw "Build failed: $exe was not created." }
Assert-CleanDist -DistRoot $distRoot
Invoke-Python -Step "Check release layout" -Arguments @("scripts/check_release_layout.py", $distRoot)
Write-Host "Build complete: $exe (one-folder)" -ForegroundColor Green
if ($Console) {
    Write-Host "DEBUG build -- run it from THIS terminal to see errors:" -ForegroundColor Yellow
    Write-Host "    $exe" -ForegroundColor Yellow
}

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

Write-Host "This build does not package avatar_cache, data, AppData auth sessions, logs, local TikTok account data, or readable app Python source files." -ForegroundColor Yellow
