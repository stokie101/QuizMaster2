"""Serve bundled frontend assets without loose HTML/CSS/JS files in Program Files.

In development, routes can still fall back to normal files. In frozen builds,
scripts/generate_web_assets_bundle.py creates core.resources.web_assets_bundle,
which PyInstaller stores inside its Python archive instead of exposing each
frontend file as an editable Program Files file.
"""

from __future__ import annotations

import base64
import mimetypes
import zlib
from typing import Optional

from fastapi.responses import Response

try:
    from core.resources.web_assets_bundle import ASSETS as _EMBEDDED_ASSETS
except Exception:  # Development before the generated bundle exists.
    _EMBEDDED_ASSETS = {}


def _normalize(relative_path: str) -> str:
    return str(relative_path or "").replace("\\", "/").lstrip("/")


def has_embedded_assets() -> bool:
    """True when a generated asset bundle is present (i.e. a frozen/release build).

    Used to decide whether the bridge should serve frontend assets from memory
    instead of mounting on-disk folders (which don't ship in release builds).
    """
    return bool(_EMBEDDED_ASSETS)


def has_embedded_asset(relative_path: str) -> bool:
    return _normalize(relative_path) in _EMBEDDED_ASSETS


def get_embedded_asset_bytes(relative_path: str) -> Optional[bytes]:
    entry = _EMBEDDED_ASSETS.get(_normalize(relative_path))
    if not entry:
        return None
    try:
        compressed = base64.b64decode(entry["data"])
        return zlib.decompress(compressed)
    except Exception:
        return None


def get_embedded_asset_text(relative_path: str, encoding: str = "utf-8") -> Optional[str]:
    data = get_embedded_asset_bytes(relative_path)
    if data is None:
        return None
    return data.decode(encoding)


def embedded_asset_response(relative_path: str, media_type: str | None = None) -> Optional[Response]:
    key = _normalize(relative_path)
    data = get_embedded_asset_bytes(key)
    if data is None:
        return None
    entry = _EMBEDDED_ASSETS.get(key, {})
    resolved_media_type = media_type or entry.get("media_type") or mimetypes.guess_type(key)[0] or "application/octet-stream"
    return Response(content=data, media_type=resolved_media_type)
