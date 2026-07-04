"""QuizMaster local identity foundation.

This service creates and validates a local profile document under AppData so
future account-linking work can associate existing local data with a user
without changing quiz data, settings.ini, databases, or any cloud/login/
subscription behavior.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PROFILE_SCHEMA_VERSION = "quizmaster-local-profile.v1"
DEFAULT_PROFILE_NAME = "Default QuizMaster Profile"
CLOUD_ACCOUNT_STATUS = "not_connected"
MIGRATION_STAGE = "local_identity_created"

CRITICAL_FIELDS = (
    "schema_version",
    "profile_id",
    "installation_id",
    "device_id",
    "created_at",
)

SAFE_DEFAULT_FIELDS = {
    "profile_name": DEFAULT_PROFILE_NAME,
    "cloud_user_id": None,
    "cloud_account_status": CLOUD_ACCOUNT_STATUS,
    "cloud_sync_enabled": False,
    "migration_stage": MIGRATION_STAGE,
}

ID_PREFIXES = {
    "profile_id": "profile_",
    "installation_id": "install_",
    "device_id": "device_",
}
UUID_ID_PATTERN = re.compile(r"^(profile|install|device)_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class LocalIdentityService:
    """Ensure and report the QuizMaster local profile identity without touching app data."""

    def __init__(self, appdata_root: Optional[Path] = None):
        self.appdata_root = Path(appdata_root) if appdata_root else self._resolve_appdata_root()
        self.quizmaster_root = self.appdata_root / "QuizMaster"
        self.profile_dir = self.quizmaster_root / "profiles" / "local"
        self.profile_path = self.profile_dir / "profile.json"

    @staticmethod
    def _resolve_appdata_root() -> Path:
        env_appdata = os.environ.get("APPDATA")
        if env_appdata:
            return Path(env_appdata).expanduser()
        if sys.platform == "win32":
            return Path.home() / "AppData" / "Roaming"
        return Path.home() / ".config"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}{uuid.uuid4()}"

    def ensure_profile(self) -> Dict[str, Any]:
        """Create or load profile.json and safely repair non-critical fields.

        Existing IDs are never regenerated. If an existing profile is malformed
        or lacks a critical identity field, this method reports a warning/error
        instead of overwriting the identity file.
        """
        try:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            if not self.profile_path.exists():
                profile = self._create_profile()
                self._write_profile(profile)
                logger.info("QuizMaster local identity created: %s", self._safe_profile_label(profile))
                return self._status(profile, created=True, repaired=False)

            profile, load_error = self._load_profile()
            if load_error:
                logger.warning("QuizMaster local identity could not be loaded: %s", load_error)
                return self._status(None, error=load_error, warning="profile_json_unreadable")

            validation = self._validate_profile(profile)
            if validation["critical_errors"]:
                message = "; ".join(validation["critical_errors"])
                logger.warning("QuizMaster local identity has critical validation errors: %s", message)
                return self._status(profile, error=message, warning="critical_identity_fields_missing")

            repaired = self._repair_safe_fields(profile)
            if repaired:
                self._write_profile(profile)
                logger.info("QuizMaster local identity repaired safe metadata fields: %s", self._safe_profile_label(profile))
            else:
                logger.info("QuizMaster local identity ready: %s", self._safe_profile_label(profile))
            return self._status(profile, created=False, repaired=repaired)
        except Exception as exc:
            logger.warning("QuizMaster local identity setup failed without blocking startup: %s", exc, exc_info=True)
            return self._status(None, error=str(exc), warning="profile_creation_failed")

    def get_status(self) -> Dict[str, Any]:
        """Return current profile status, ensuring profile.json exists when safe."""
        return self.ensure_profile()

    def _create_profile(self) -> Dict[str, Any]:
        now = self._now_iso()
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profile_id": self._new_id(ID_PREFIXES["profile_id"]),
            "installation_id": self._new_id(ID_PREFIXES["installation_id"]),
            "device_id": self._new_id(ID_PREFIXES["device_id"]),
            "profile_name": DEFAULT_PROFILE_NAME,
            "created_at": now,
            "updated_at": now,
            "cloud_user_id": None,
            "cloud_account_status": CLOUD_ACCOUNT_STATUS,
            "cloud_sync_enabled": False,
            "migration_stage": MIGRATION_STAGE,
        }

    def _load_profile(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            with self.profile_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return None, "profile.json must contain a JSON object"
            return data, None
        except Exception as exc:
            return None, str(exc)

    def _validate_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        critical_errors = []
        warnings = []
        for field in CRITICAL_FIELDS:
            if profile.get(field) in (None, ""):
                critical_errors.append(f"Missing required field: {field}")
        if profile.get("schema_version") and profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
            critical_errors.append(f"Unsupported schema_version: {profile.get('schema_version')}")
        for field, prefix in ID_PREFIXES.items():
            value = profile.get(field)
            if value and not str(value).startswith(prefix):
                critical_errors.append(f"{field} must start with {prefix}")
            elif value and not UUID_ID_PATTERN.match(str(value)):
                critical_errors.append(f"{field} must be a stable UUID-based ID")
        for field in ("profile_name", "updated_at", "cloud_user_id", "cloud_account_status", "cloud_sync_enabled", "migration_stage"):
            if field not in profile:
                warnings.append(f"Missing safe metadata field: {field}")
        return {"critical_errors": critical_errors, "warnings": warnings}

    def _repair_safe_fields(self, profile: Dict[str, Any]) -> bool:
        changed = False
        for field, value in SAFE_DEFAULT_FIELDS.items():
            if field not in profile:
                profile[field] = value
                changed = True
        if "updated_at" not in profile:
            profile["updated_at"] = self._now_iso()
            changed = True
        return changed

    def _write_profile(self, profile: Dict[str, Any]) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        with self.profile_path.open("w", encoding="utf-8") as handle:
            json.dump(profile, handle, indent=2)
            handle.write("\n")

    def _status(
        self,
        profile: Optional[Dict[str, Any]],
        *,
        created: bool = False,
        repaired: bool = False,
        error: Optional[str] = None,
        warning: Optional[str] = None,
    ) -> Dict[str, Any]:
        validation = self._validate_profile(profile) if profile else {"critical_errors": [], "warnings": []}
        return {
            "exists": self.profile_path.exists(),
            "ready": bool(profile) and not validation["critical_errors"] and not error,
            "created": created,
            "repaired": repaired,
            "warning": warning,
            "error": error,
            "profile": profile,
            "validation": validation,
            "paths": {
                "appdata_root": str(self.appdata_root),
                "quizmaster_root": str(self.quizmaster_root),
                "profiles_local": str(self.profile_dir),
                "profile_json": str(self.profile_path),
            },
            "safety": {
                "local_only": True,
                "external_apis_called": False,
                "login_added": False,
                "cloud_sync_added": False,
                "subscriptions_added": False,
                "settings_ini_mutated": False,
                "quiz_data_mutated": False,
                "media_references_mutated": False,
                "database_schema_changed": False,
            },
        }

    @staticmethod
    def _safe_profile_label(profile: Dict[str, Any]) -> str:
        return f"profile_id={profile.get('profile_id')} device_id={profile.get('device_id')} cloud=not_connected sync=disabled"
