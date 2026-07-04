"""QuizMaster local account architecture foundation.

This service owns the local-only account state document used to prepare
QuizMaster for website accounts, cloud sync, and subscriptions. It does not
perform login, subscription validation, network requests, data uploads, sync,
database migrations, settings.ini writes, or quiz data changes.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

ACCOUNT_STATE_SCHEMA_VERSION = "quizmaster-account-state.v1"

DEFAULT_ACCOUNT_STATE: Dict[str, Any] = {
    "schema_version": ACCOUNT_STATE_SCHEMA_VERSION,
    "account_status": "not_connected",
    "cloud_user_id": None,
    "email": None,
    "subscription_tier": "free",
    "subscription_status": "inactive",
    "account_linked_at": None,
    "last_sync_at": None,
    "sync_enabled": False,
}

VALID_ACCOUNT_STATUSES = {"not_connected", "linked", "disabled"}
VALID_SUBSCRIPTION_TIERS = {"free", "creator", "pro", "enterprise"}
VALID_SUBSCRIPTION_STATUSES = {"inactive", "active", "trialing", "past_due", "canceled"}
LOCAL_ONLY_MUTABLE_FIELDS = {
    "account_status",
    "cloud_user_id",
    "email",
    "subscription_tier",
    "subscription_status",
    "account_linked_at",
    "last_sync_at",
    "sync_enabled",
}


class AccountService:
    """Create, validate, read, and locally update QuizMaster account_state.json.

    This class only writes local account_state.json. It never contacts external
    services or mutates quiz data, settings.ini, database schemas, or media references.
    """

    def __init__(self, appdata_root: Optional[Path] = None):
        self.appdata_root = Path(appdata_root) if appdata_root else self._resolve_appdata_root()
        self.quizmaster_root = self.appdata_root / "QuizMaster"
        self.profile_dir = self.quizmaster_root / "profiles" / "local"
        self.account_state_path = self.profile_dir / "account_state.json"

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

    def ensure_account_state(self) -> Dict[str, Any]:
        """Ensure account_state.json exists and safely repair optional fields."""
        try:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            if not self.account_state_path.exists():
                state = self.default_state()
                self._write_state(state)
                logger.info("QuizMaster local account state created at %s", self.account_state_path)
                return self._status(state, created=True, repaired=False)

            state, load_error = self._load_state()
            if load_error:
                logger.warning("QuizMaster local account state could not be loaded: %s", load_error)
                return self._status(None, error=load_error, warning="account_state_json_unreadable")

            validation = self._validate_state(state)
            if validation["critical_errors"]:
                message = "; ".join(validation["critical_errors"])
                logger.warning("QuizMaster local account state has critical validation errors: %s", message)
                return self._status(state, error=message, warning="critical_account_state_fields_invalid")

            repaired = self._repair_missing_fields(state)
            if repaired:
                self._write_state(state)
                logger.info("QuizMaster local account state repaired missing optional fields at %s", self.account_state_path)
            else:
                logger.info("QuizMaster local account state ready at %s", self.account_state_path)
            return self._status(state, created=False, repaired=repaired)
        except Exception as exc:
            logger.warning("QuizMaster local account state setup failed without blocking startup: %s", exc, exc_info=True)
            return self._status(None, error=str(exc), warning="account_state_setup_failed")

    def get_status(self) -> Dict[str, Any]:
        """Return local account state status, creating the file when safe."""
        return self.ensure_account_state()

    def get_state(self) -> Dict[str, Any]:
        """Return the validated local account state object."""
        status = self.ensure_account_state()
        if not status.get("ready"):
            raise ValueError(status.get("error") or "Account state is not ready")
        return deepcopy(status["account_state"])

    def update_local_state(self, updates: Mapping[str, Any]) -> Dict[str, Any]:
        """Update local account state fields without external side effects."""
        unknown_fields = set(updates) - LOCAL_ONLY_MUTABLE_FIELDS
        if unknown_fields:
            raise ValueError(f"Unsupported account state field(s): {', '.join(sorted(unknown_fields))}")

        state = self.get_state()
        state.update(dict(updates))
        validation = self._validate_state(state)
        if validation["critical_errors"]:
            raise ValueError("; ".join(validation["critical_errors"]))
        if validation["warnings"]:
            raise ValueError("; ".join(validation["warnings"]))

        self._write_state(state)
        return self._status(state, updated=True)

    @staticmethod
    def default_state() -> Dict[str, Any]:
        return deepcopy(DEFAULT_ACCOUNT_STATE)

    def _load_state(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            with self.account_state_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return None, "account_state.json must contain a JSON object"
            return data, None
        except Exception as exc:
            return None, str(exc)

    def _validate_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        critical_errors = []
        warnings = []

        schema_version = state.get("schema_version")
        if schema_version in (None, ""):
            critical_errors.append("Missing required field: schema_version")
        elif schema_version != ACCOUNT_STATE_SCHEMA_VERSION:
            critical_errors.append(f"Unsupported schema_version: {schema_version}")

        if "account_status" in state and state.get("account_status") not in VALID_ACCOUNT_STATUSES:
            critical_errors.append("account_status must be a supported local account status")
        if "subscription_tier" in state and state.get("subscription_tier") not in VALID_SUBSCRIPTION_TIERS:
            critical_errors.append("subscription_tier must be a supported local tier")
        if "subscription_status" in state and state.get("subscription_status") not in VALID_SUBSCRIPTION_STATUSES:
            critical_errors.append("subscription_status must be a supported local subscription status")
        if "sync_enabled" in state and not isinstance(state.get("sync_enabled"), bool):
            critical_errors.append("sync_enabled must be a boolean")

        nullable_string_fields = ("cloud_user_id", "email", "account_linked_at", "last_sync_at")
        for field in nullable_string_fields:
            value = state.get(field)
            if value is not None and not isinstance(value, str):
                critical_errors.append(f"{field} must be null or a string")

        for field in DEFAULT_ACCOUNT_STATE:
            if field not in state:
                warnings.append(f"Missing safe account state field: {field}")

        return {"critical_errors": critical_errors, "warnings": warnings}

    def _repair_missing_fields(self, state: Dict[str, Any]) -> bool:
        changed = False
        for field, default_value in DEFAULT_ACCOUNT_STATE.items():
            if field not in state:
                state[field] = deepcopy(default_value)
                changed = True
        return changed

    def _write_state(self, state: Dict[str, Any]) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        with self.account_state_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")

    def _status(
        self,
        state: Optional[Dict[str, Any]],
        *,
        created: bool = False,
        repaired: bool = False,
        updated: bool = False,
        error: Optional[str] = None,
        warning: Optional[str] = None,
    ) -> Dict[str, Any]:
        validation = self._validate_state(state) if state else {"critical_errors": [], "warnings": []}
        return {
            "exists": self.account_state_path.exists(),
            "ready": bool(state) and not validation["critical_errors"] and not error,
            "created": created,
            "repaired": repaired,
            "updated": updated,
            "warning": warning,
            "error": error,
            "account_state": state,
            "schema": deepcopy(DEFAULT_ACCOUNT_STATE),
            "validation": validation,
            "paths": {
                "appdata_root": str(self.appdata_root),
                "quizmaster_root": str(self.quizmaster_root),
                "profiles_local": str(self.profile_dir),
                "account_state_json": str(self.account_state_path),
            },
            "startup_behavior": {
                "ensure_account_state_json_exists": True,
                "validate_account_state": True,
                "repair_missing_optional_fields": True,
                "external_services_contacted": False,
            },
            "future_extension_points": {
                "account_login": "reserved_not_implemented",
                "cloud_backup": "reserved_not_implemented",
                "widget_sync": "reserved_not_implemented",
                "multi_device_support": "reserved_not_implemented",
            },
        }
