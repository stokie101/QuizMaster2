"""Public desktop configuration for the QuizMaster Cloudflare authentication broker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

AUTH_BASE_URL_FIELD = "QUIZMASTER_TIKTOK_AUTH_BASE_URL"
LEGACY_AUTH_BASE_URL_FIELD = "LIVEFORGE_TIKTOK_AUTH_BASE_URL"
DEFAULT_AUTH_BASE_URL = "https://quizmaster-tiktok-auth.stokie-md.workers.dev"
CONFIG_FIELDS = (AUTH_BASE_URL_FIELD, LEGACY_AUTH_BASE_URL_FIELD)
DEFAULT_PRODUCTION_CONFIG_PATH = Path(__file__).resolve().parent / "production.env"


def _read_public_values(path: Path) -> dict[str, str]:
    """Read a sectionless KEY=VALUE public config file."""
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")) or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in CONFIG_FIELDS:
            values[key] = value.strip()
    return values


def _first_configured_value(*values: str | None) -> str:
    for value in values:
        cleaned = (value or "").strip()
        if cleaned:
            return cleaned
    return ""


@dataclass(frozen=True)
class AuthBrokerConfig:
    """Public QuizMaster Cloudflare auth broker configuration."""

    auth_base_url: str = ""
    source_field: str = "default"

    def status(self) -> dict[str, object]:
        """Return configuration presence for startup diagnostics."""
        configured = bool(self.auth_base_url)
        return {
            f"{AUTH_BASE_URL_FIELD} configured": configured,
            "auth_base_url_source": self.source_field,
            "legacy_field_supported": LEGACY_AUTH_BASE_URL_FIELD,
            "missing_fields": [] if configured else [AUTH_BASE_URL_FIELD],
        }


def load_auth_broker_config(
    *,
    environment: Mapping[str, str] | None = None,
    production_config_path: Path | str = DEFAULT_PRODUCTION_CONFIG_PATH,
) -> AuthBrokerConfig:
    """Load the public broker URL from env override, packaged config, or built-in default.

    QuizMaster prefers its app-specific broker field. The old LiveForge field is
    accepted only as a migration fallback for existing local environments.
    """
    environment_values = os.environ if environment is None else environment
    production_values = _read_public_values(Path(production_config_path))

    sources = (
        ("environment:QUIZMASTER_TIKTOK_AUTH_BASE_URL", environment_values.get(AUTH_BASE_URL_FIELD)),
        ("production.env:QUIZMASTER_TIKTOK_AUTH_BASE_URL", production_values.get(AUTH_BASE_URL_FIELD)),
        ("environment:LIVEFORGE_TIKTOK_AUTH_BASE_URL", environment_values.get(LEGACY_AUTH_BASE_URL_FIELD)),
        ("production.env:LIVEFORGE_TIKTOK_AUTH_BASE_URL", production_values.get(LEGACY_AUTH_BASE_URL_FIELD)),
    )
    for source_field, value in sources:
        configured_value = _first_configured_value(value)
        if configured_value:
            return AuthBrokerConfig(auth_base_url=configured_value, source_field=source_field)

    return AuthBrokerConfig(auth_base_url=DEFAULT_AUTH_BASE_URL, source_field="default")
