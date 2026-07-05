from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/QuizMaster")

# NOTE: core/assets is intentionally NOT listed -- media assets (images, sounds)
# ship as loose data files on purpose. Frontend SOURCE under it is still caught
# by the BAD_SUFFIXES scan below.
BAD_DIRS = [
    "core/server/static",
    "core/server/themes",
    "core/server/overlays",
    "core/quiz/html",
    "core/quiz/css",
    "core/quiz/js",
    "_internal/core/server/static",
    "_internal/core/server/themes",
    "_internal/core/server/overlays",
    "_internal/core/quiz/html",
    "_internal/core/quiz/css",
    "_internal/core/quiz/js",
    "data",
    "logs",
    "auth",
    "cache",
    "avatar_cache",
    "_internal/data",
    "_internal/logs",
    "_internal/auth",
    "_internal/cache",
    "_internal/avatar_cache",
]

BAD_SUFFIXES = {".py", ".pyw", ".html", ".css", ".js", ".map"}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def main() -> int:
    if not ROOT.exists():
        print(f"Release layout check failed: missing {ROOT}", file=sys.stderr)
        return 1

    bad = []
    for item in BAD_DIRS:
        p = ROOT / item
        if p.exists():
            bad.append(item)

    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in BAD_SUFFIXES:
            continue
        r = rel(p)
        if r.startswith(("core/", "_internal/core/", "config/", "_internal/config/")) or p.name in {"main.py", "setup_cython.py", "QuizMaster.spec"}:
            bad.append(r)

    if bad:
        print("Release layout check failed. These app files/folders are still loose in dist:", file=sys.stderr)
        for item in bad[:100]:
            print(f" - {item}", file=sys.stderr)
        if len(bad) > 100:
            print(f" ... and {len(bad) - 100} more", file=sys.stderr)
        return 1

    print("Release layout check passed: app frontend/source folders are not loose in dist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
