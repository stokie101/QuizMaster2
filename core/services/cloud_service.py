"""QuizMaster local cloud architecture foundation.

This service owns the local-only cloud state and sync queue documents used to
prepare QuizMaster for future website login, cloud sync, hosted widgets, and
subscriptions. It does not perform login, connect to Supabase or Stripe, call
external APIs, upload/download data, sync saved quiz data, mutate settings.ini,
alter media references, or change database schemas.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

CLOUD_STATE_SCHEMA_VERSION = "quizmaster-cloud-state.v1"
SYNC_QUEUE_SCHEMA_VERSION = "quizmaster-sync-queue.v1"

DEFAULT_CLOUD_STATE: Dict[str, Any] = {
    "schema_version": CLOUD_STATE_SCHEMA_VERSION,
    "cloud_enabled": False,
    "api_base_url": None,
    "app_base_url": "https://liveforge.online",
    "widgets_base_url": None,
    "account_status": "not_connected",
    "sync_status": "disabled",
    "last_connection_check_at": None,
    "last_sync_at": None,
}

DEFAULT_SYNC_QUEUE: Dict[str, Any] = {
    "schema_version": SYNC_QUEUE_SCHEMA_VERSION,
    "enabled": False,
    "pending_operations": [],
    "last_updated_at": None,
}

VALID_ACCOUNT_STATUSES = {"not_connected", "offline", "linked", "disabled"}
VALID_SYNC_STATUSES = {"disabled", "idle", "pending", "error"}


class CloudService:
    """Create, validate, and expose safe local QuizMaster cloud architecture state.

    This intentionally stops at local file and method boundaries. All network/
    action methods return disabled/not-implemented responses and never call
    external services or mutate saved quiz data.
    """

    def __init__(self, appdata_root: Optional[Path] = None):
        self.appdata_root = Path(appdata_root) if appdata_root else self._resolve_appdata_root()
        self.quizmaster_root = self.appdata_root / "QuizMaster"
        self.cloud_dir = self.quizmaster_root / "cloud"
        self.cloud_state_path = self.cloud_dir / "cloud_state.json"
        self.sync_queue_path = self.cloud_dir / "sync_queue.json"

    @staticmethod
    def _resolve_appdata_root() -> Path:
        env_appdata = os.environ.get("APPDATA")
        if env_appdata:
            return Path(env_appdata).expanduser()
        if sys.platform == "win32":
            return Path.home() / "AppData" / "Roaming"
        return Path.home() / ".config"

    @staticmethod
    def default_cloud_state() -> Dict[str, Any]:
        return deepcopy(DEFAULT_CLOUD_STATE)

    @staticmethod
    def default_sync_queue() -> Dict[str, Any]:
        return deepcopy(DEFAULT_SYNC_QUEUE)

    def ensure_cloud_architecture(self) -> Dict[str, Any]:
        """Ensure local cloud files exist and are safe without blocking startup."""
        cloud_state = None
        sync_queue = None
        cloud_created = False
        queue_created = False
        cloud_repaired = False
        queue_repaired = False
        errors = []
        warnings = []

        try:
            self.cloud_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning("QuizMaster cloud architecture folder setup failed without blocking startup: %s", exc, exc_info=True)
            return self._status(
                None,
                None,
                errors=[str(exc)],
                warnings=["cloud_folder_setup_failed"],
            )

        try:
            if not self.cloud_state_path.exists():
                cloud_state = self.default_cloud_state()
                self._write_json(self.cloud_state_path, cloud_state)
                cloud_created = True
            else:
                cloud_state, error = self._load_json_object(self.cloud_state_path, "cloud_state.json")
                if error:
                    errors.append(error)
                    warnings.append("cloud_state_json_unreadable")
                elif cloud_state is not None:
                    validation = self._validate_cloud_state(cloud_state)
                    if validation["critical_errors"]:
                        errors.extend(validation["critical_errors"])
                        warnings.append("critical_cloud_state_fields_invalid")
                    else:
                        cloud_repaired = self._repair_missing_fields(cloud_state, DEFAULT_CLOUD_STATE)
                        if cloud_repaired:
                            self._write_json(self.cloud_state_path, cloud_state)
        except Exception as exc:
            logger.warning("QuizMaster cloud state setup failed without blocking startup: %s", exc, exc_info=True)
            errors.append(str(exc))
            warnings.append("cloud_state_setup_failed")

        try:
            if not self.sync_queue_path.exists():
                sync_queue = self.default_sync_queue()
                self._write_json(self.sync_queue_path, sync_queue)
                queue_created = True
            else:
                sync_queue, error = self._load_json_object(self.sync_queue_path, "sync_queue.json")
                if error:
                    errors.append(error)
                    warnings.append("sync_queue_json_unreadable")
                elif sync_queue is not None:
                    validation = self._validate_sync_queue(sync_queue)
                    if validation["critical_errors"]:
                        errors.extend(validation["critical_errors"])
                        warnings.append("critical_sync_queue_fields_invalid")
                    else:
                        queue_repaired = self._repair_missing_fields(sync_queue, DEFAULT_SYNC_QUEUE)
                        if queue_repaired:
                            self._write_json(self.sync_queue_path, sync_queue)
        except Exception as exc:
            logger.warning("QuizMaster sync queue setup failed without blocking startup: %s", exc, exc_info=True)
            errors.append(str(exc))
            warnings.append("sync_queue_setup_failed")

        status = self._status(
            cloud_state,
            sync_queue,
            cloud_created=cloud_created,
            queue_created=queue_created,
            cloud_repaired=cloud_repaired,
            queue_repaired=queue_repaired,
            errors=errors,
            warnings=warnings,
        )
        logger.info(
            "QuizMaster cloud architecture status: ready=%s cloud_state=%s sync_queue=%s pending_sync_count=%s external_apis_called=False",
            status["ready"],
            self.cloud_state_path,
            self.sync_queue_path,
            status["pending_sync_count"],
        )
        return status

    def get_status(self) -> Dict[str, Any]:
        """Return local cloud architecture status, creating files when safe."""
        return self.ensure_cloud_architecture()

    def get_cloud_state(self) -> Dict[str, Any]:
        """Return the validated local cloud state object."""
        status = self.ensure_cloud_architecture()
        if not status.get("cloud_state"):
            raise ValueError(status.get("error") or "Cloud state is not ready")
        return deepcopy(status["cloud_state"])

    def get_connection_status(self) -> Dict[str, Any]:
        """Return safe offline connection status without external checks."""
        state = self.get_cloud_state()
        return {
            "success": True,
            "connected": False,
            "status": "offline",
            "account_status": state.get("account_status", "not_connected"),
            "cloud_enabled": False,
            "message": "Cloud connection is not implemented and remains offline.",
            "external_apis_called": False,
        }

    def connect_account(self) -> Dict[str, Any]:
        """Future login seam; intentionally disabled."""
        return self._disabled_response("connect_account", "Website login is not implemented.")

    def disconnect_account(self) -> Dict[str, Any]:
        """Future logout/disconnect seam; intentionally disabled."""
        return self._disabled_response("disconnect_account", "Account disconnect is not implemented.")

    def get_sync_status(self) -> Dict[str, Any]:
        """Return local-only sync status without starting sync."""
        status = self.ensure_cloud_architecture()
        queue = status.get("sync_queue") or self.default_sync_queue()
        cloud_state = status.get("cloud_state") or self.default_cloud_state()
        return {
            "success": True,
            "enabled": False,
            "sync_status": cloud_state.get("sync_status", "disabled"),
            "pending_sync_count": self._pending_count(queue),
            "last_sync_at": cloud_state.get("last_sync_at"),
            "message": "Cloud sync is disabled and not implemented.",
            "external_apis_called": False,
        }

    def queue_sync_operation(self, operation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Future queue seam; does not enqueue operations."""
        _ = operation
        return self._disabled_response("queue_sync_operation", "Sync queueing is disabled and no operation was enqueued.")

    def get_pending_sync_count(self) -> int:
        """Return local pending sync count from sync_queue.json."""
        status = self.ensure_cloud_architecture()
        return self._pending_count(status.get("sync_queue"))

    def upload_actions_events(self) -> Dict[str, Any]:
        """Future upload seam; intentionally disabled."""
        return self._disabled_response("upload_actions_events", "Upload is not implemented.")

    def download_actions_events(self) -> Dict[str, Any]:
        """Future download seam; intentionally disabled."""
        return self._disabled_response("download_actions_events", "Download is not implemented.")

    def get_entitlements(self) -> Dict[str, Any]:
        """Future subscription entitlement seam; intentionally local-only."""
        return {
            "success": True,
            "implemented": False,
            "enabled": False,
            "subscription_system": "not_implemented",
            "entitlements": [],
            "message": "Subscription entitlements are not implemented.",
            "external_apis_called": False,
        }

    def _load_json_object(self, path: Path, filename: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return None, f"{filename} must contain a JSON object"
            return data, None
        except Exception as exc:
            return None, str(exc)

    def _validate_cloud_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        critical_errors = []
        warnings = []
        if state.get("schema_version") != CLOUD_STATE_SCHEMA_VERSION:
            critical_errors.append(f"Unsupported schema_version: {state.get('schema_version')}")
        if "cloud_enabled" in state and state.get("cloud_enabled") is not False:
            critical_errors.append("cloud_enabled must remain false")
        if "account_status" in state and state.get("account_status") not in VALID_ACCOUNT_STATUSES:
            critical_errors.append("account_status must be a supported cloud account status")
        if "sync_status" in state and state.get("sync_status") not in VALID_SYNC_STATUSES:
            critical_errors.append("sync_status must be a supported cloud sync status")
        for field in ("api_base_url", "app_base_url", "widgets_base_url", "last_connection_check_at", "last_sync_at"):
            value = state.get(field)
            if value is not None and not isinstance(value, str):
                critical_errors.append(f"{field} must be null or a string")
        for field in DEFAULT_CLOUD_STATE:
            if field not in state:
                warnings.append(f"Missing safe cloud state field: {field}")
        return {"critical_errors": critical_errors, "warnings": warnings}

    def _validate_sync_queue(self, queue: Dict[str, Any]) -> Dict[str, Any]:
        critical_errors = []
        warnings = []
        if queue.get("schema_version") != SYNC_QUEUE_SCHEMA_VERSION:
            critical_errors.append(f"Unsupported sync queue schema_version: {queue.get('schema_version')}")
        if "enabled" in queue and queue.get("enabled") is not False:
            critical_errors.append("sync queue enabled must remain false")
        if "pending_operations" in queue and not isinstance(queue.get("pending_operations"), list):
            critical_errors.append("pending_operations must be a list")
        if "last_updated_at" in queue and queue.get("last_updated_at") is not None and not isinstance(queue.get("last_updated_at"), str):
            critical_errors.append("last_updated_at must be null or a string")
        for field in DEFAULT_SYNC_QUEUE:
            if field not in queue:
                warnings.append(f"Missing safe sync queue field: {field}")
        return {"critical_errors": critical_errors, "warnings": warnings}

    @staticmethod
    def _repair_missing_fields(target: Dict[str, Any], defaults: Dict[str, Any]) -> bool:
        changed = False
        for field, default_value in defaults.items():
            if field not in target:
                target[field] = deepcopy(default_value)
                changed = True
        return changed

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        self.cloud_dir.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")

    @staticmethod
    def _pending_count(queue: Optional[Dict[str, Any]]) -> int:
        if not queue or not isinstance(queue.get("pending_operations"), list):
            return 0
        return len(queue["pending_operations"])

    def _disabled_response(self, method: str, message: str) -> Dict[str, Any]:
        return {
            "success": False,
            "method": method,
            "implemented": False,
            "enabled": False,
            "message": message,
            "external_apis_called": False,
            "data_uploaded": False,
            "data_downloaded": False,
            "quiz_data_mutated": False,
        }

    def _status(
        self,
        cloud_state: Optional[Dict[str, Any]],
        sync_queue: Optional[Dict[str, Any]],
        *,
        cloud_created: bool = False,
        queue_created: bool = False,
        cloud_repaired: bool = False,
        queue_repaired: bool = False,
        errors: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        errors = errors or []
        warnings = warnings or []
        cloud_validation = self._validate_cloud_state(cloud_state) if cloud_state else {"critical_errors": [], "warnings": []}
        queue_validation = self._validate_sync_queue(sync_queue) if sync_queue else {"critical_errors": [], "warnings": []}
        ready = bool(cloud_state) and bool(sync_queue) and not errors and not cloud_validation["critical_errors"] and not queue_validation["critical_errors"]
        return {
            "exists": self.cloud_state_path.exists() and self.sync_queue_path.exists(),
            "ready": ready,
            "created": {"cloud_state": cloud_created, "sync_queue": queue_created},
            "repaired": {"cloud_state": cloud_repaired, "sync_queue": queue_repaired},
            "warning": "; ".join(warnings) if warnings else None,
            "error": "; ".join(errors) if errors else None,
            "cloud_state": cloud_state,
            "sync_queue": sync_queue,
            "cloud_state_schema": deepcopy(DEFAULT_CLOUD_STATE),
            "sync_queue_schema": deepcopy(DEFAULT_SYNC_QUEUE),
            "validation": {"cloud_state": cloud_validation, "sync_queue": queue_validation},
            "pending_sync_count": self._pending_count(sync_queue),
            "paths": {
                "appdata_root": str(self.appdata_root),
                "quizmaster_root": str(self.quizmaster_root),
                "cloud_folder": str(self.cloud_dir),
                "cloud_state_json": str(self.cloud_state_path),
                "sync_queue_json": str(self.sync_queue_path),
            },
            "startup_behavior": {
                "ensure_cloud_folder_exists": True,
                "ensure_cloud_state_json_exists": True,
                "ensure_sync_queue_json_exists": True,
                "validate_state_safely": True,
                "requires_internet": False,
                "blocks_startup_on_failure": False,
                "logs_cloud_architecture_status": True,
            },
            "future_extension_points": {
                "website_login": "reserved_not_implemented",
                "cloud_sync": "reserved_not_implemented",
                "hosted_widgets": "reserved_not_implemented",
                "subscriptions": "reserved_not_implemented",
            },
            "safety": {
                "local_only": True,
                "login_added": False,
                "cloud_sync_enabled": False,
                "uploads_downloads_enabled": False,
                "external_apis_called": False,
                "subscriptions_added": False,
                "settings_ini_mutated": False,
                "quiz_data_mutated": False,
                "media_references_mutated": False,
                "database_schema_changed": False,
                "packaging_build_installer_changed": False,
            },
        }
