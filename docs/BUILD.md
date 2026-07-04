# Building QuizMaster — the hardened release pipeline

This is the step-by-step for turning the QuizMaster source into a professional,
single-file Windows installer whose paid-subscription gate ships as **native
machine code**, not readable Python. Read it once and you'll be able to do (and
change) every step yourself.

---

## The big picture

There are two completely separate worlds:

| | Developing | Releasing |
|---|---|---|
| What runs | your plain `.py`, interpreted | a compiled app installed from `Setup.exe` |
| Where | PyCharm, run/debug as normal | the build script or GitHub CI |
| Touches your source? | you edit it freely | **reads** it, never changes it |

Nothing below changes how you work day to day. You keep editing `.py` files in
PyCharm and running them interpreted. The hardening is bolted on **only at
release time**.

The release pipeline is four stages:

```
 1. Cython      2. PyInstaller       3. Inno Setup        4. (later) Sign
 compile the    bundle app folder    wrap it in a        Authenticode-sign
 gate module -> with native .pyd  -> professional     -> so Windows trusts it
 to a .pyd      + Qt runtime files    installer .exe
```

---

## Why it's built this way (the security logic)

- **You can't make Python impossible to reverse-engineer.** Plain PyInstaller
  bundles your code as bytecode that tools can decompile back to near-original
  source. So the *real* lock on paid features is **server-side** (the app only
  gets Pro data the server agrees to serve). Read that again: the exe is not
  what protects your revenue — the server is.
- **On the client we raise the bar** by compiling the one module that decides
  "is this account allowed to run QuizMaster" — `core/services/subscription_gate.py`
  — into a native `.pyd`. Native code has no bytecode to decompile, so the gate
  logic can't be trivially read or edited out.
- **We keep the compiled list tiny.** Only small, self-contained modules (no Qt,
  minimal imports) compile cleanly. Compiling the whole app is a separate,
  heavier option (Nuitka) we can add later if wholesale source-copying ever
  becomes a real concern.
- **Nothing lands in system folders.** The installer drops a single exe into a
  per-user Program Files folder; all user data lives in `%APPDATA%\QuizMaster`.

---

## One-time setup on your Windows machine

1. **Python 3.14**. QuizMaster is currently built and tested in your local
   Python 3.14 virtual environment, and the native Cython gate is produced as a
   `cp314` Windows extension. The local build script should therefore use Python
   3.14 for release builds.
2. **Inno Setup 6**. This is only needed for the `-Installer` step. It installs
   `ISCC.exe` (the installer compiler), normally at
   `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`. The build script also checks
   PATH and the normal 64-bit Program Files location.
3. A C compiler for Cython — the **"Build Tools for Visual Studio"** (the free
   "Desktop development with C++" workload). Cython needs this to turn the gate
   module into a `.pyd`.

If the Microsoft Store keeps opening when you type `python`, disable the
`python.exe` / `python3.exe` App execution aliases in Windows Settings, or use
`py -3.14` directly. You don't strictly need any of this locally if you only
ever build via GitHub CI (next-to-last section) — the runner has it all.

---

## Build it yourself (Windows)

From the repo root, in **PowerShell**:

```powershell
# Full release build: hardened exe + installer, in one go
scripts\build_quizmaster.ps1 -Installer
```

That script:

1. Finds Python 3.14 and stops immediately if it is missing.
2. Installs build dependencies (`cython`, `pyinstaller`, …).
3. Compiles `core/services/subscription_gate.py` → a native `.pyd` (the gate
   hardening). In a **release** build it **fails loudly** if the `.pyd` isn't
   produced, so the gate can never silently ship as readable source.
4. Runs PyInstaller with `QuizMaster.spec` as a **one-folder** app (required —
   QtWebEngine won't launch as one-file), leaving `dist\QuizMaster\QuizMaster.exe`
   plus its runtime files.
5. With `-Installer`, compiles the Inno Setup installer →
   `installer\output\QuizMasterSetup-1.0.0.exe` (the file you hand to customers).

The installer bundles the whole `dist\QuizMaster\` folder into a single
`Setup.exe`; it installs into the app's own folder and writes user data to
`%APPDATA%\QuizMaster` — nothing lands in system folders.

If `QuizMaster.exe` builds but the script stops at `ISCC.exe was not found`, the
app build succeeded and only the installer tool is missing. Install Inno Setup 6,
reopen PowerShell, then run `scripts\build_quizmaster.ps1 -Installer` again.

## Debug locally / trace errors

The release exe is **windowed** — on a crash it shows nothing. Three ways to see
the actual error, fastest first:

**1. Run from source (no build needed — best for most bugs).**
```powershell
$env:QUIZMASTER_AUTH_MODE="local"   # skip the login gate while debugging
py -3.14 main.py
```
Full traceback prints in the terminal. If it fails here, it's an app bug (you
have the exact error). If it works here but the installed exe doesn't, it's a
*packaging* problem.

**2. Build a DEBUG exe with a console window.**
```powershell
scripts\build_quizmaster.ps1 -Console
.\dist\QuizMaster\QuizMaster.exe   # run from the terminal — errors show in a console
```
`-Console` also **skips the hard Cython requirement**, so you can build it even
without the Visual Studio C++ build tools. Use it to reproduce a *packaged* crash
and read the traceback. (Don't ship a `-Console` build — it's for debugging.)

**3. Read the crash log.** However the app dies, it writes to:
```
%APPDATA%\QuizMaster\logs\
```
Paste that path into Explorer's address bar and open the newest file.

---

## What each file does

| File | Role |
|---|---|
| `core/services/subscription_gate.py` | The gate logic (the thing we compile). Edit this, not `application.py`, to change entitlement rules. |
| `setup_cython.py` | The list of modules to compile + the Cython invocation. |
| `QuizMaster.spec` | PyInstaller recipe: what to bundle into the one exe. |
| `scripts/build_quizmaster.ps1` | One-command local build (Cython → PyInstaller). |
| `installer/QuizMaster.iss` | Inno Setup recipe: version, install dir, shortcuts, uninstall. |
| `.github/workflows/build-windows.yml` | Same pipeline, automated in the cloud. |

---

## How to change common things

- **Change who counts as "Pro":** edit `is_quizmaster_pro()` in
  `core/services/subscription_gate.py`. Run it interpreted to test; it recompiles
  automatically on the next build.
- **Bump the version:** change `MyAppVersion` in `installer/QuizMaster.iss`
  (the output filename follows it).
- **Compile another sensitive module too:** add its path to `SENSITIVE_MODULES`
  in `setup_cython.py`, and add its dotted name to `hiddenimports` in
  `QuizMaster.spec`. Keep such modules small and Qt-free.
- **Bundle another data folder:** add it to `DATA_DIRS` in `QuizMaster.spec`.

---

## Build in the cloud (no local toolchain needed)

`.github/workflows/build-windows.yml` runs the identical pipeline on a Windows
runner using Python 3.14. To use it:

1. Push this branch, open the repo on GitHub → **Actions**.
2. Pick **Build Windows Installer** → **Run workflow** (or push a tag like
   `v1.0.0`).
3. When it's green, download the **QuizMaster-Windows-Installer** artifact from
   the run — that's your installer exe.

This is the recommended way to cut releases: reproducible, and you don't have to
maintain a build machine.

---

## Code signing (do this when you have a certificate)

Unsigned installers trigger a **SmartScreen "unknown publisher" warning** that
scares off installs. Fixing it needs an OV or EV code-signing certificate (a
paid, identity-verified purchase — this is a *you* task, not code).

Once you have a `.pfx`:

1. Add two repo secrets: `CODE_SIGN_PFX_BASE64` (the .pfx, base64-encoded) and
   `CODE_SIGN_PASSWORD`.
2. Uncomment the **Sign QuizMaster.exe** step in the workflow (it's already
   written, just disabled).

Signing proves the exe is genuinely from you and hasn't been tampered with — a
bigger trust win than any obfuscation.

---

## Clean install — no test data ever ships

Customers must get an empty, first-run app, never your testing state. Three
things guarantee this:

1. **The app reads/writes all runtime data to `%APPDATA%\QuizMaster`**, not the
   install folder — so a fresh machine starts with nothing.
2. **The spec bundles only code and assets.** Runtime/test folders (`data/`,
   `logs/`, `avatar_cache/`, `auth/`, `cache/`) are never bundled, and
   `QuizMaster.spec` will **hard-fail the build** if any of them ever get added
   to the bundle list (see `_FORBIDDEN_PREFIXES`).
3. **Those folders are gitignored and not tracked**, so a fresh CI checkout
   doesn't contain them either.

Two habits that keep it clean:

- **Never `git add` your local `config/settings.ini` changes.** It's tracked as
  a neutral default; when you run the app it fills with your TikTok handle,
  actions, and local paths. Don't commit that — `git checkout config/settings.ini`
  before committing if it changed.
- **Never run a build script that copies `data/`/`avatars/`/`logs/` into
  `dist/`.** The only sanctioned build is `QuizMaster.spec` (via
  `scripts/build_quizmaster.ps1` or CI). The old `build.py` that did this has
  been removed for exactly this reason.

## What this does *not* do (be honest with yourself)

- It does not make the app uncrackable — nothing does. A skilled reverser can
  still patch a binary. That's why paid features must stay **server-enforced**.
- Only the listed module is native; the rest of the app is still bundled
  bytecode. Upgrade path if you need more: compile everything with **Nuitka**
  (see below).
- The build must run on **Windows** (locally or in CI). It can't be verified in
  a Linux-only environment.

---

## Alternative: the fully-compiled Nuitka build

The PyInstaller pipeline above ships the app as bytecode (decompilable) with
only the gate as native code. The **Nuitka** build compiles **all** of the
Python — the entire app, gate included — to machine code, so a copy of the
install directory exposes no readable app source at all. It's a **parallel**
pipeline: the PyInstaller path is untouched and stays the default.

- **Local:** `scripts\build_quizmaster_nuitka.ps1`
  - **default:** a one-folder app in `dist\QuizMaster\` (`--standalone`),
    windowed release. This is the **safe, installable** mode — the folder is
    laid out exactly like the PyInstaller output so the same Inno installer
    packages it.
  - `-Installer`: after the one-folder build, compile the Inno installer into
    `installer\output\QuizMasterSetup-<ver>.exe` — the single file you actually
    ship. Needs Inno Setup 6. This is the normal release command:
    `scripts\build_quizmaster_nuitka.ps1 -Installer`.
  - `-Onefile`: build a single `dist\QuizMaster.exe` instead. **Not recommended
    for this app.** A one-file build must self-extract the entire ~500 MB
    Qt/Chromium payload to a temp dir on every launch, which (a) makes the final
    build step sit at "99%" for many minutes while it compresses + Defender scans
    that payload, and (b) is unreliable for QtWebEngine at runtime. That fragility
    is the same reason the PyInstaller build is one-folder. `-Installer` is
    rejected with `-Onefile` (the installer packages a folder).
  - `-Console`: keep a console window with live tracebacks for debugging.
- **CI:** `.github/workflows/build-windows-nuitka.yml` runs on `windows-latest`,
  compiles the app, and uploads it as an artifact you can download and launch on
  a real Windows machine. Trigger it manually from the Actions tab (defaults to
  `standalone`; `onefile` is available but not recommended). Nuitka builds are
  slow — 20-40 min is normal.

Notes specific to the Nuitka build:

- **No Cython step.** Nuitka compiles `subscription_gate.py` to machine code
  itself, so the separate `.pyd` is redundant; the script removes any stale
  `.pyd` first so the `.py` gets compiled in.
- **Frontend stays embedded.** As with PyInstaller, the script regenerates
  `core/resources/web_assets_bundle.py` first so HTML/CSS/JS is embedded, not
  shipped as loose files. Do **not** add `--include-data-dir` for the frontend
  folders — that would undo the anti-copy win.
- **Nuitka has no `sys._MEIPASS`.** `core/utils/resource_loader.py` detects the
  Nuitka case (`"__compiled__" in globals()`) and resolves resources from the
  app tree instead, so the same code works under both packagers.
