"""Public desktop authentication configuration."""

from __future__ import annotations

import os

# Public Cloudflare Turnstile site key for liveforge.online. This is not a secret.
# It is packaged with the desktop application and is used only to render the
# Cloudflare Turnstile widget in the PySide security verification dialog.
# Never place the Turnstile secret key or any Supabase credential in this module.
DEFAULT_TURNSTILE_SITE_KEY = "0x4AAAAAADdzoCWL5onMITv9"


def get_turnstile_site_key() -> str:
    """Return the development override, or the packaged public site key."""
    environment_value = os.environ.get("LIVEFORGE_TURNSTILE_SITE_KEY", "").strip()
    if environment_value:
        return environment_value

    return str(globals().get("DEFAULT_TURNSTILE_SITE_KEY", "") or "").strip()
