"""QuizMaster website account authentication foundation.

This module implements only account login/session/profile plumbing through the
shared LiveForge website API. It does not read Supabase configuration directly
and does not implement billing, Pro-plan enforcement, or cloud data sync.

The website backend is shared with LiveForge, but all desktop storage, keyring
entries, logs, and session files are QuizMaster-specific.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
import logging
import os
import re
import stat
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

WEBSITE_BASE_URL = "https://liveforge.online"
REGISTER_URL = f"{WEBSITE_BASE_URL}/register"
FORGOT_PASSWORD_URL = f"{WEBSITE_BASE_URL}/forgot-password"
DASHBOARD_URL = f"{WEBSITE_BASE_URL}/dashboard"
LOGIN_PAGE_URL = f"{WEBSITE_BASE_URL}/login"
DESKTOP_LOGIN_URL = f"{WEBSITE_BASE_URL}/api/auth/login"
AUTH_REFRESH_URL = f"{WEBSITE_BASE_URL}/api/auth/refresh"
AUTH_LOGOUT_URL = f"{WEBSITE_BASE_URL}/api/auth/logout"
ACCOUNT_ME_URL = f"{WEBSITE_BASE_URL}/api/account/me"
DESKTOP_LOGIN_EXCHANGE_URL = f"{WEBSITE_BASE_URL}/api/auth/desktop/exchange"
DESKTOP_AUTH_TIMEOUT = (10, 30)
APP_SLUG = "quizmaster"
APP_SLUG_HEADER = "x-app-slug"
CAPTCHA_LOGIN_MESSAGE = "Security verification is required."
CAPTCHA_FAILED_MESSAGE = "Security verification failed. Please try again."
CAPTCHA_FAILURE_TYPES = {"captcha_required", "captcha_failed", "captcha"}
EMAIL_NOT_CONFIRMED_TYPES = {"email_not_confirmed"}
KEYRING_SERVICE = "QuizMaster Desktop"
KEYRING_USERNAME = "quizmaster_website_session"
REDACTED_LOG_VALUE = "[redacted]"


class AuthLoginError(RuntimeError):
    """Login failure with a safe, user-facing message and debug details."""

    def __init__(
        self,
        user_message: str,
        *,
        status_code: Optional[int] = None,
        parsed_error: Optional[str] = None,
        failure_type: str = "temporarily_unavailable",
    ):
        super().__init__(user_message)
        self.user_message = user_message
        self.status_code = status_code
        self.parsed_error = parsed_error
        self.failure_type = failure_type


@dataclass
class AuthSession:
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[int]
    expires_in: Optional[int] = None
    token_type: str = "bearer"
    user: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_website_api(cls, payload: Dict[str, Any]) -> "AuthSession":
        """Build a session from the shared website API's token contract."""
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        expires_at = payload.get("expires_at")
        expires_in = payload.get("expires_in")
        if expires_at is None and expires_in is not None:
            expires_at = int(datetime.now(timezone.utc).timestamp()) + int(expires_in)
        return cls(
            access_token=payload.get("access_token") if isinstance(payload.get("access_token"), str) else "",
            refresh_token=payload.get("refresh_token") if isinstance(payload.get("refresh_token"), str) else None,
            expires_at=int(expires_at) if expires_at is not None else None,
            expires_in=int(expires_in) if expires_in is not None else None,
            token_type=payload.get("token_type") if isinstance(payload.get("token_type"), str) else "bearer",
            user=dict(profile),
        )

    @classmethod
    def from_liveforge_api(cls, payload: Dict[str, Any]) -> "AuthSession":
        """Compatibility alias for the shared website API contract."""
        return cls.from_website_api(payload)

    def is_present(self) -> bool:
        return isinstance(self.access_token, str) and bool(self.access_token.strip())

    def is_expired(self, skew_seconds: int = 60) -> bool:
        if not self.expires_at:
            return False
        return int(datetime.now(timezone.utc).timestamp()) >= (self.expires_at - skew_seconds)


@dataclass
class QuizMasterProfile:
    id: Optional[str] = None
    email: Optional[str] = None
    display_name: Optional[str] = None
    plan: str = "Free"
    public_widget_id: Optional[str] = None
    app_link_status: str = "Linked"
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.extra_fields,
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "plan": self.plan,
            "public_widget_id": self.public_widget_id,
            "app_link_status": self.app_link_status,
        }


# Compatibility for older imports/type checks inside the app.
LiveForgeProfile = QuizMasterProfile


class AuthService:
    """QuizMaster website authentication and local session persistence."""

    _instance: Optional["AuthService"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "AuthService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def __init__(self, appdata_root: Optional[Path] = None):
        self.appdata_root = Path(appdata_root) if appdata_root else self._resolve_appdata_root()
        self.auth_dir = self.appdata_root / "auth"
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self._configure_auth_file_logging()
        self.session_path = self.auth_dir / "session.json"
        self.current_session: Optional[AuthSession] = None
        self.current_profile: Optional[QuizMasterProfile] = None
        self.last_error: Optional[str] = None

    def _configure_auth_file_logging(self) -> None:
        """Mirror auth diagnostics to the QuizMaster log directory without secrets."""
        try:
            log_dir = self.appdata_root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "auth.log"
            if any(
                isinstance(handler, logging.FileHandler)
                and Path(getattr(handler, "baseFilename", "")) == log_path
                for handler in logger.handlers
            ):
                return
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
            logger.addHandler(file_handler)
            logger.setLevel(logging.DEBUG)
        except Exception as exc:
            logger.debug("Could not configure auth file logging: %s", exc)

    @staticmethod
    def _resolve_appdata_root() -> Path:
        if os.environ.get("QUIZMASTER_DATA_DIR"):
            return Path(os.environ["QUIZMASTER_DATA_DIR"]).expanduser()
        env_appdata = os.environ.get("APPDATA")
        if env_appdata:
            return Path(env_appdata).expanduser() / "QuizMaster"
        if sys.platform == "win32":
            return Path.home() / "AppData" / "Roaming" / "QuizMaster"
        return Path.home() / ".quizmaster"

    @property
    def login_url(self) -> str:
        return DESKTOP_LOGIN_URL

    @property
    def desktop_login_exchange_url(self) -> str:
        """Website endpoint that exchanges a short-lived desktop login code."""
        return DESKTOP_LOGIN_EXCHANGE_URL

    def is_configured(self) -> bool:
        # QuizMaster uses the shared public website origin for desktop account login.
        return bool(WEBSITE_BASE_URL)

    def config_error_message(self) -> str:
        return "QuizMaster account login is temporarily unavailable. Please try again later."

    @staticmethod
    def _app_version() -> str:
        """Return a non-sensitive desktop build identifier for backend diagnostics."""
        configured = (os.environ.get("QUIZMASTER_APP_VERSION") or "").strip()
        if configured:
            return configured
        try:
            return version("quizmaster")
        except PackageNotFoundError:
            return "unknown"

    @staticmethod
    def _auth_debug_enabled() -> bool:
        return (os.environ.get("QUIZMASTER_AUTH_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _base_headers() -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            APP_SLUG_HEADER: APP_SLUG,
            "x-liveforge-app-slug": APP_SLUG,
        }

    @classmethod
    def _headers(cls) -> Dict[str, str]:
        headers = cls._base_headers()
        headers["User-Agent"] = f"QuizMasterDesktop/{cls._app_version()}"
        return headers

    @classmethod
    def _desktop_headers(cls, app_version: str) -> Dict[str, str]:
        headers = cls._base_headers()
        headers["User-Agent"] = f"QuizMasterDesktop/{app_version}"
        return headers

    @classmethod
    def _authorized_headers(cls, access_token: str) -> Dict[str, str]:
        headers = cls._headers()
        headers["Authorization"] = f"Bearer {access_token}"
        return headers

    @staticmethod
    def _app_payload(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"app_slug": APP_SLUG, **(extra or {})}

    def sign_in(
        self,
        email: str,
        password: str,
        remember: bool = False,
        captcha_token: Optional[str] = None,
    ) -> QuizMasterProfile:
        email = (email or "").strip()
        captcha_token = (captcha_token or "").strip()
        if self._auth_debug_enabled():
            logger.debug("Desktop auth login: captcha_token_present=%s", bool(captcha_token))
        if not email or not password:
            raise AuthLoginError("Email and password are required.", failure_type="validation")
        if not captcha_token:
            raise AuthLoginError(CAPTCHA_LOGIN_MESSAGE, failure_type="captcha_required")

        response, payload = self._request_json(
            "POST",
            self.login_url,
            headers=self._desktop_headers(self._app_version()),
            json_payload=self._app_payload({"email": email, "password": password, "captcha_token": captcha_token}),
            operation="login",
        )
        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(access_token, str) or not access_token.strip():
            self.last_error = "QuizMaster received an incomplete session."
            raise AuthLoginError(self.last_error, status_code=response.status_code, failure_type="missing_access_token")

        session = AuthSession.from_website_api(payload)
        return self._store_website_auth(payload, session, remember=remember)

    def exchange_desktop_login_code(
        self,
        code: str,
        redirect_uri: str,
        remember: bool = True,
    ) -> QuizMasterProfile:
        """Exchange a one-time website code for the desktop auth contract."""
        code = (code or "").strip()
        redirect_uri = (redirect_uri or "").strip()
        if not code:
            raise AuthLoginError("Desktop login code is required.", failure_type="validation")
        if not redirect_uri:
            raise AuthLoginError("Desktop login redirect URI is required.", failure_type="validation")

        try:
            response, payload = self._request_json(
                "POST",
                self.desktop_login_exchange_url,
                headers=self._headers(),
                json_payload=self._app_payload({"code": code, "redirect_uri": redirect_uri}),
                operation="desktop login exchange",
            )
            session = AuthSession.from_website_api(payload)
            if not session.is_present():
                self.last_error = "Sign-in temporarily unavailable"
                raise AuthLoginError(self.last_error, status_code=response.status_code, failure_type="missing_session")
            return self._store_website_auth(payload, session, remember=remember)
        except AuthLoginError:
            raise
        except Exception as exc:
            self.last_error = "Sign-in temporarily unavailable"
            raise AuthLoginError(self.last_error, failure_type="unexpected") from exc

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        json_payload: Optional[Dict[str, Any]] = None,
        operation: str,
    ) -> Tuple[requests.Response, Dict[str, Any]]:
        debug_enabled = self._auth_debug_enabled()
        if debug_enabled:
            logger.debug(
                "Desktop auth request: operation=%s url=%s method=%s json_keys=%s header_keys=%s",
                operation,
                url,
                method,
                sorted(json_payload.keys()) if json_payload is not None else [],
                sorted(headers.keys()),
            )
        try:
            request_kwargs: Dict[str, Any] = {"headers": headers, "timeout": DESKTOP_AUTH_TIMEOUT}
            if json_payload is not None:
                request_kwargs["json"] = json_payload
            if method == "POST":
                response = requests.post(url, **request_kwargs)
            elif method == "GET":
                response = requests.get(url, **request_kwargs)
            else:
                response = requests.request(method, url, **request_kwargs)
        except requests.exceptions.SSLError as exc:
            raise AuthLoginError("Secure connection failed.", failure_type="ssl") from exc
        except requests.Timeout as exc:
            raise AuthLoginError("Network timeout. Please try again.", failure_type="timeout") from exc
        except requests.ConnectionError as exc:
            raise AuthLoginError("Cannot reach the account service. Check your connection.", failure_type="connection") from exc
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            if response is None:
                raise AuthLoginError("Cannot reach the account service. Check your connection.", failure_type="connection") from exc

        payload, response_text = self._parse_desktop_auth_response(response)
        error_code, error_message = self._desktop_error_details(payload)
        if response.status_code >= 400:
            base_message, failure_type = self._desktop_user_message_for_failure(
                response.status_code, error_code, error_message, response_text
            )
            message = self._format_http_failure_message(base_message, response.status_code, error_code, error_message)
            self.last_error = message
            raise AuthLoginError(
                message,
                status_code=response.status_code,
                parsed_error=error_message or error_code,
                failure_type=failure_type,
            )
        if response_text:
            message = f"HTTP {response.status_code}: server returned non-JSON response."
            self.last_error = message
            raise AuthLoginError(message, status_code=response.status_code, failure_type="non_json_response")
        return response, payload

    def _store_website_auth(
        self,
        payload: Dict[str, Any],
        session: AuthSession,
        *,
        remember: bool,
    ) -> QuizMasterProfile:
        """Store the website session and profile in QuizMaster-local storage."""
        self.current_session = session
        profile = self._profile_from_login_payload(payload, session)
        self.current_profile = profile
        self.last_error = None
        self._update_local_account_state(profile)
        if remember:
            self.save_session(session, profile)
        else:
            self.clear_saved_session()
        if self._auth_debug_enabled():
            logger.debug(
                "Profile accepted: id_present=%s email_present=%s plan_present=%s widget_id_prefix=%s",
                bool(profile.id),
                bool(profile.email),
                bool(profile.plan),
                (profile.public_widget_id or "")[:4],
            )
        return profile

    def restore_saved_session(self) -> Optional[QuizMasterProfile]:
        saved = self.load_saved_auth()
        if not saved:
            return None
        session = saved.get("session")
        profile = saved.get("profile")
        if not isinstance(session, AuthSession) or not session.is_present():
            self.clear_saved_session()
            return None
        if session.is_expired():
            if session.refresh_token:
                try:
                    self.refresh_session(session.refresh_token)
                    return self.current_profile
                except Exception as exc:
                    logger.warning("Could not refresh saved QuizMaster session: %s", exc)
            self.last_error = "Saved QuizMaster session expired. Please sign in again."
            self.clear_saved_session()
            return None
        try:
            self.current_session = session
            self.current_profile = profile if isinstance(profile, QuizMasterProfile) else self.fetch_profile(session)
            self._update_local_account_state(self.current_profile)
            return self.current_profile
        except Exception as exc:
            logger.warning("Saved QuizMaster session is invalid or unavailable: %s", exc)
            self.last_error = str(exc)
            self.clear_saved_session()
            return None

    def refresh_session(self, refresh_token: str) -> AuthSession:
        if not refresh_token:
            raise AuthLoginError("Sign-in temporarily unavailable.", failure_type="missing_refresh_token")
        response, payload = self._request_json(
            "POST",
            AUTH_REFRESH_URL,
            headers=self._headers(),
            json_payload=self._app_payload({"refresh_token": refresh_token}),
            operation="refresh",
        )
        session = AuthSession.from_website_api(payload)
        if not session.is_present() or not session.refresh_token:
            raise AuthLoginError(
                "QuizMaster received an incomplete session.",
                status_code=response.status_code,
                failure_type="missing_session",
            )
        self.current_session = session
        self.current_profile = self._profile_from_login_payload(payload, session)
        self._update_local_account_state(self.current_profile)
        self.save_session(session, self.current_profile)
        return session

    def fetch_profile(self, session: Optional[AuthSession] = None) -> QuizMasterProfile:
        session = session or self.current_session
        if not session or not session.access_token:
            raise RuntimeError("No active QuizMaster account session.")
        _, payload = self._request_json(
            "GET",
            ACCOUNT_ME_URL,
            headers=self._authorized_headers(session.access_token),
            operation="account validation",
        )
        profile_payload = payload.get("profile") if isinstance(payload.get("profile"), dict) else None
        if profile_payload is None:
            raise AuthLoginError("QuizMaster received an incomplete profile.", failure_type="missing_profile")
        session.user = dict(profile_payload)
        profile = self._profile_from_user(profile_payload)
        self.current_profile = profile
        return profile

    def _profile_from_login_payload(self, payload: Dict[str, Any], session: AuthSession) -> QuizMasterProfile:
        data_payload = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        profile_payload = payload.get("profile") if isinstance(payload.get("profile"), dict) else None
        if profile_payload is None and isinstance(data_payload.get("profile"), dict):
            profile_payload = data_payload["profile"]
        user_payload = payload.get("user") if isinstance(payload.get("user"), dict) else data_payload.get("user")
        if profile_payload is None and isinstance(user_payload, dict):
            user_profile = user_payload.get("profile")
            profile_payload = user_profile if isinstance(user_profile, dict) else None
        if profile_payload:
            return self._profile_from_user({**session.user, **profile_payload})
        return self._profile_from_user(session.user)

    @staticmethod
    def _profile_from_user(user: Optional[Dict[str, Any]]) -> QuizMasterProfile:
        user = user or {}
        metadata = user.get("user_metadata") or user.get("metadata") or {}
        email = user.get("email") or metadata.get("email")
        display_name = (
            user.get("display_name")
            or user.get("displayName")
            or user.get("full_name")
            or user.get("name")
            or metadata.get("display_name")
            or metadata.get("full_name")
            or (email.split("@")[0] if email else None)
        )
        known_fields = {
            "id", "user_id", "userId", "sub", "email", "display_name", "displayName",
            "full_name", "name", "plan", "subscription_tier", "subscriptionTier",
            "public_widget_id", "publicWidgetId", "widget_id", "widgetId",
            "app_link_status", "desktop_app_status", "appLinkStatus",
            "app_slug", "appSlug", "app_name", "appName", "app_entitlements",
        }
        return QuizMasterProfile(
            id=user.get("id") or user.get("user_id") or user.get("userId") or user.get("sub"),
            email=email,
            display_name=display_name,
            plan=user.get("plan") or user.get("subscription_tier") or user.get("subscriptionTier") or "Free",
            public_widget_id=user.get("public_widget_id") or user.get("publicWidgetId") or user.get("widget_id") or user.get("widgetId"),
            app_link_status=user.get("app_link_status") or user.get("desktop_app_status") or user.get("appLinkStatus") or "Linked",
            extra_fields={key: value for key, value in user.items() if key not in known_fields},
        )

    def get_profile_status(self) -> Dict[str, Any]:
        if self.current_profile:
            return {"authenticated": True, "profile": self.current_profile.to_dict(), "dashboard_url": DASHBOARD_URL}
        if self.current_session:
            try:
                self.current_profile = self.fetch_profile(self.current_session)
                return {"authenticated": True, "profile": self.current_profile.to_dict(), "dashboard_url": DASHBOARD_URL}
            except Exception as exc:
                return {"authenticated": False, "error": str(exc), "dashboard_url": DASHBOARD_URL}
        return {"authenticated": False, "error": self.last_error, "dashboard_url": DASHBOARD_URL}

    def logout(self) -> None:
        session = self.current_session
        try:
            if session and session.access_token:
                self._request_json(
                    "POST",
                    AUTH_LOGOUT_URL,
                    headers=self._authorized_headers(session.access_token),
                    operation="logout",
                )
        finally:
            self.current_session = None
            self.current_profile = None
            self.clear_saved_session()
            try:
                from core.services.account_service import AccountService
                AccountService().update_local_state({
                    "account_status": "not_connected",
                    "cloud_user_id": None,
                    "email": None,
                    "subscription_tier": "free",
                    "subscription_status": "inactive",
                    "account_linked_at": None,
                    "sync_enabled": False,
                })
            except Exception as exc:
                logger.warning("Could not update local account state on logout: %s", exc)

    def save_session(self, session: AuthSession, profile: Optional[QuizMasterProfile] = None) -> None:
        data = {"session": asdict(session), "profile": profile.to_dict() if profile else None}
        payload = json.dumps(data)
        if self._keyring_set(payload):
            self._write_session_marker({"storage": "keyring", "saved_at": self._now_iso()})
            return
        self._write_session_marker({"storage": "file", "saved_at": self._now_iso(), **data})

    def load_saved_auth(self) -> Optional[Dict[str, Any]]:
        marker = self._read_session_marker()
        if not marker:
            return None
        try:
            if marker.get("storage") == "keyring":
                payload = self._keyring_get()
                if not payload:
                    return None
                data = json.loads(payload)
            else:
                data = marker

            session_data = data.get("session") if isinstance(data.get("session"), dict) else data
            profile_data = data.get("profile") if isinstance(data.get("profile"), dict) else None
            session = AuthSession(**session_data)
            if not session.is_present():
                return None
            profile = self._profile_from_user(profile_data) if profile_data else None
            return {"session": session, "profile": profile}
        except Exception as exc:
            logger.warning("Could not load saved auth session: %s", exc)
            return None

    def load_saved_session(self) -> Optional[AuthSession]:
        saved = self.load_saved_auth()
        if not saved:
            return None
        session = saved.get("session")
        return session if isinstance(session, AuthSession) and session.is_present() else None

    def clear_saved_session(self) -> None:
        self._keyring_delete()
        try:
            self.session_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Could not remove saved auth session marker: %s", exc)

    def _write_session_marker(self, payload: Dict[str, Any]) -> None:
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.session_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        tmp_path.replace(self.session_path)

    def _read_session_marker(self) -> Optional[Dict[str, Any]]:
        if not self.session_path.exists():
            return None
        try:
            data = json.loads(self.session_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("Could not read auth session marker: %s", exc)
            return None

    def _keyring_set(self, payload: str) -> bool:
        try:
            import keyring  # type: ignore
            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, payload)
            return True
        except Exception as exc:
            logger.info("OS keyring unavailable; using restricted session file fallback: %s", exc)
            return False

    def _keyring_get(self) -> Optional[str]:
        try:
            import keyring  # type: ignore
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            return None

    def _keyring_delete(self) -> None:
        try:
            import keyring  # type: ignore
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            pass

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_desktop_auth_response(response: requests.Response) -> Tuple[Dict[str, Any], str]:
        try:
            payload = response.json()
            return (payload if isinstance(payload, dict) else {}), ""
        except (ValueError, TypeError):
            text = AuthService._redact_text_for_log(getattr(response, "text", "") or "")[:1000]
            return {}, text

    @staticmethod
    def _desktop_error_details(payload: Dict[str, Any]) -> Tuple[str, str]:
        if not isinstance(payload, dict):
            return "", ""
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code") if isinstance(error.get("code"), str) else ""
            message = error.get("message") if isinstance(error.get("message"), str) else ""
            return code.strip(), AuthService._sanitize_error_message(message) if message else ""
        if isinstance(error, str):
            return error.strip(), AuthService._sanitize_error_message(error)
        return "", AuthService._extract_error_message(payload)

    @staticmethod
    def _format_http_failure_message(
        base_message: str,
        status_code: int,
        error_code: str,
        error_message: str,
    ) -> str:
        safe_code = (error_code or "").strip() or "<missing>"
        safe_message = AuthService._sanitize_error_message(error_message) if error_message else "<missing>"
        return f"{base_message} [HTTP {status_code}; error.code={safe_code}; error.message={safe_message}]"

    @classmethod
    def _desktop_user_message_for_failure(
        cls,
        status_code: Optional[int],
        error_code: str,
        error_message: str,
        response_text: str,
    ) -> Tuple[str, str]:
        code = (error_code or "").lower()
        detail = f"{code} {error_message or ''}".lower()
        if response_text:
            return f"HTTP {status_code}: server returned non-JSON response.", "non_json_response"
        if status_code == 403 and code == "origin_not_allowed":
            return "Desktop request sent forbidden browser Origin headers.", "origin_not_allowed"
        if status_code == 405:
            return "The account service rejected the HTTP method or endpoint.", "method_endpoint_mismatch"
        if status_code == 415:
            return "Desktop login request must be JSON.", "unsupported_media_type"
        if status_code == 429 and code == "rate_limit_exceeded":
            return "Too many login attempts. Please try again later.", "rate_limit_exceeded"
        if status_code == 503 and code == "auth_not_configured":
            return "QuizMaster authentication is not configured on the server.", "auth_not_configured"
        if status_code == 400 and code == "invalid_credentials" and error_message == "Email and password are required.":
            return "Email and password are required.", "validation"
        if status_code == 502 and code == "invalid_supabase_session":
            return "QuizMaster received an incomplete session.", "invalid_supabase_session"
        if "captcha" in detail:
            return CAPTCHA_FAILED_MESSAGE, "captcha_required"
        if "email_not_confirmed" in detail or "email not confirmed" in detail:
            return "Please confirm your email before signing in.", "email_not_confirmed"
        if status_code == 401 and code == "invalid_credentials":
            return "Invalid email or password.", "invalid_credentials"
        if status_code in {400, 401, 403}:
            return "Sign-in temporarily unavailable.", "temporarily_unavailable"
        if status_code is not None and status_code >= 500:
            return "Account service unavailable.", "server_unavailable"
        safe_message = cls._sanitize_error_message(error_message) if error_message else ""
        return safe_message or "Sign-in temporarily unavailable.", code or "temporarily_unavailable"

    @staticmethod
    def _extract_error_message(body: Any) -> str:
        captcha_marker = AuthService._captcha_failure_marker(body)
        if captcha_marker:
            return captcha_marker
        email_confirmation_marker = AuthService._email_not_confirmed_marker(body)
        if email_confirmation_marker:
            return email_confirmation_marker
        if isinstance(body, str):
            return AuthService._sanitize_error_message(body)
        if isinstance(body, list):
            for item in body:
                message = AuthService._extract_error_message(item)
                if message:
                    return message
            return ""
        if not isinstance(body, dict):
            return ""
        for key in ("msg", "message", "error_description", "description", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return AuthService._sanitize_error_message(value)
        error = body.get("error")
        if isinstance(error, str) and error.strip():
            return AuthService._sanitize_error_message(error)
        if isinstance(error, dict):
            message = AuthService._extract_error_message(error)
            if message:
                return message
        for key in ("errors", "errorDetails"):
            message = AuthService._extract_error_message(body.get(key))
            if message:
                return message
        return ""

    @staticmethod
    def _captcha_failure_marker(body: Any) -> str:
        if isinstance(body, str):
            body_lower = body.strip().lower()
            for failure_type in CAPTCHA_FAILURE_TYPES:
                if failure_type in body_lower:
                    return failure_type
            if "captcha" in body_lower:
                return "captcha"
            return ""
        if isinstance(body, list):
            for item in body:
                marker = AuthService._captcha_failure_marker(item)
                if marker:
                    return marker
            return ""
        if isinstance(body, dict):
            for value in body.values():
                marker = AuthService._captcha_failure_marker(value)
                if marker:
                    return marker
        return ""

    @staticmethod
    def _email_not_confirmed_marker(body: Any) -> str:
        if isinstance(body, str):
            body_lower = body.strip().lower()
            if any(failure_type in body_lower for failure_type in EMAIL_NOT_CONFIRMED_TYPES) or "email not confirmed" in body_lower:
                return "email_not_confirmed"
            return ""
        if isinstance(body, list):
            for item in body:
                marker = AuthService._email_not_confirmed_marker(item)
                if marker:
                    return marker
            return ""
        if isinstance(body, dict):
            for value in body.values():
                marker = AuthService._email_not_confirmed_marker(value)
                if marker:
                    return marker
        return ""

    @staticmethod
    def _redact_for_log(value: Any) -> Any:
        sensitive_names = ("token", "password", "secret", "authorization", "apikey", "api_key", "refresh")
        if isinstance(value, dict):
            return {
                key: (REDACTED_LOG_VALUE if any(name in str(key).lower() for name in sensitive_names) else AuthService._redact_for_log(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [AuthService._redact_for_log(item) for item in value]
        return value

    @staticmethod
    def _safe_raw_response_text_for_log(response: requests.Response) -> str:
        return AuthService._redact_text_for_log(getattr(response, "text", "") or "")

    @staticmethod
    def _safe_response_json_for_log(response: requests.Response) -> str:
        try:
            body = response.json()
        except Exception:
            return "<not JSON>"
        try:
            return json.dumps(AuthService._redact_for_log(body), ensure_ascii=False)
        except Exception:
            return AuthService._redact_text_for_log(str(body))

    @staticmethod
    def _redact_text_for_log(message: str) -> str:
        redacted = message or ""
        sensitive_names = r"(?:access[_-]?token|refresh[_-]?token|token|password|secret|authorization|apikey|api[_-]?key)"
        redacted = re.sub(
            rf'("{sensitive_names}"\s*:\s*")[^"\\]*(?:\\.[^"\\]*)*(")',
            rf'\1{REDACTED_LOG_VALUE}\2',
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            rf"('{sensitive_names}'\s*:\s*')[^']*(')",
            rf"\1{REDACTED_LOG_VALUE}\2",
            redacted,
            flags=re.IGNORECASE,
        )
        return redacted

    @staticmethod
    def _sanitize_error_message(message: str) -> str:
        cleaned = (message or "").strip()
        for forbidden in ("Supabase", "supabase"):
            cleaned = cleaned.replace(forbidden, "account service")
        return cleaned or "QuizMaster account service is unavailable. Check your connection and try again."

    def _update_local_account_state(self, profile: QuizMasterProfile) -> None:
        try:
            from core.services.account_service import AccountService
            AccountService().update_local_state({
                "account_status": "linked",
                "cloud_user_id": profile.id,
                "email": profile.email,
                "subscription_tier": (profile.plan or "free").lower(),
                "subscription_status": "active",
                "account_linked_at": self._now_iso(),
                "sync_enabled": False,
            })
        except Exception as exc:
            logger.warning("Could not update local account state after login: %s", exc)
