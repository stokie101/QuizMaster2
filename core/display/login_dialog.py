"""QuizMaster startup account login dialog."""

from __future__ import annotations

import importlib
import json
import logging
import os
import traceback
import webbrowser
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.desktop_auth import get_turnstile_site_key
from core.services.auth_service import (
    AuthLoginError,
    AuthService,
    DASHBOARD_URL,
    FORGOT_PASSWORD_URL,
    REGISTER_URL,
    WEBSITE_BASE_URL,
)
from core.utils.resource_loader import get_resource_path

logger = logging.getLogger(__name__)


def _auth_debug_enabled() -> bool:
    return os.environ.get("LIVEFORGE_AUTH_DEBUG", "").strip() == "1"


def _debug_event(name: str, value: bool) -> None:
    if _auth_debug_enabled():
        logger.debug("Desktop security verification: %s %s", name, str(bool(value)).lower())


def _log_exception(context: str) -> None:
    logger.error(
        "Desktop security verification exception during %s:\n%s",
        context,
        traceback.format_exc(),
    )


QWebChannel = None
QWebEngineView = None
WEBCHANNEL_AVAILABLE = False
WEBENGINE_AVAILABLE = False
_DEPENDENCIES_CHECKED = False


def _load_verification_dependencies() -> None:
    """Load optional Qt modules only when verification is requested."""
    global QWebChannel, QWebEngineView
    global WEBCHANNEL_AVAILABLE, WEBENGINE_AVAILABLE, _DEPENDENCIES_CHECKED

    if _DEPENDENCIES_CHECKED:
        return
    _DEPENDENCIES_CHECKED = True

    try:
        QWebChannel = importlib.import_module("PySide6.QtWebChannel").QWebChannel
        WEBCHANNEL_AVAILABLE = True
    except Exception:
        QWebChannel = None
        WEBCHANNEL_AVAILABLE = False
        _log_exception("WebChannel import")
    _debug_event("webchannel_import_ok", WEBCHANNEL_AVAILABLE)

    try:
        QWebEngineView = importlib.import_module(
            "PySide6.QtWebEngineWidgets"
        ).QWebEngineView
        WEBENGINE_AVAILABLE = True
    except Exception:
        QWebEngineView = None
        WEBENGINE_AVAILABLE = False
        _log_exception("WebEngine import")
    _debug_event("webengine_import_ok", WEBENGINE_AVAILABLE)


class SecurityVerificationStartError(RuntimeError):
    """A safe, user-facing verification startup failure."""


class TurnstileBridge(QObject):
    """Receive Turnstile completion and error events from the embedded page."""

    token_received = Signal(str)
    verification_error = Signal(str)

    @Slot(str)
    def receiveToken(self, token: str) -> None:
        try:
            token = token.strip() if isinstance(token, str) else ""
            _debug_event("token_received", bool(token))
            if token and len(token) <= 4096:
                self.token_received.emit(token)
            else:
                self.verification_error.emit("turnstile_empty_token")
        except Exception:
            _log_exception("token callback")
            self.verification_error.emit("turnstile_callback_error")

    @Slot(str)
    def reportError(self, error_code: str) -> None:
        try:
            error_code = error_code.strip() if isinstance(error_code, str) else ""
            self.verification_error.emit(error_code or "turnstile_error")
        except Exception:
            _log_exception("error callback")
            self.verification_error.emit("turnstile_callback_error")


class SecurityVerificationDialog(QDialog):
    """Complete Cloudflare Turnstile inside the desktop application."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        site_key: Optional[str] = None,
    ):
        if QApplication.instance() is None:
            raise SecurityVerificationStartError(
                "Security verification could not start. The application is not ready."
            )
        super().__init__(parent)
        self.captcha_token = ""
        self.web_view = None
        self._channel = None
        self._bridge = None
        self.site_key = (
            get_turnstile_site_key() if site_key is None else site_key
        ).strip()
        _debug_event("site_key_configured", bool(self.site_key))

        if not self.site_key:
            raise SecurityVerificationStartError(
                "Security verification could not start. Turnstile site key is not configured."
            )

        _load_verification_dependencies()
        if not WEBENGINE_AVAILABLE:
            raise SecurityVerificationStartError(
                "Security verification could not start. PySide6-WebEngine is required."
            )
        if not WEBCHANNEL_AVAILABLE:
            raise SecurityVerificationStartError(
                "Security verification could not start. PySide6-WebChannel is required."
            )

        self.setWindowTitle("QuizMaster security verification")
        self.setModal(True)
        self.resize(560, 520)
        layout = QVBoxLayout(self)
        self.status_label = QLabel("Loading QuizMaster security verification…", self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        try:
            self.web_view = QWebEngineView(self)
            layout.addWidget(self.web_view, 1)

            # Keep strong references to both objects for the full dialog lifetime.
            self._bridge = TurnstileBridge(self)
            self._bridge.token_received.connect(self._receive_token)
            self._bridge.verification_error.connect(self._report_error)
            self._channel = QWebChannel(self.web_view.page())
            self._channel.registerObject("turnstileBridge", self._bridge)
            self.web_view.page().setWebChannel(self._channel)
            self.web_view.loadFinished.connect(self._page_loaded)
            self.web_view.setHtml(self._verification_html(), QUrl(WEBSITE_BASE_URL))
        except Exception as exc:
            _debug_event("html_loaded", False)
            _log_exception("dialog setup")
            self.captcha_token = ""
            raise SecurityVerificationStartError(
                "Security verification could not start. Please try again."
            ) from exc

    def _page_loaded(self, succeeded: bool) -> None:
        try:
            _debug_event("html_loaded", succeeded)
            if succeeded:
                self.status_label.setText("Complete the security verification below.")
            else:
                self.captcha_token = ""
                self.status_label.setText(
                    "Security verification could not load. Check your connection and try again."
                )
        except Exception:
            _log_exception("HTML load callback")
            self.captcha_token = ""
            self.reject()

    def _verification_html(self) -> str:
        encoded_key = json.dumps(self.site_key)
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body {{ min-height: 100%; margin: 0; background: #020617; color: #e5f9ff; font-family: Arial, sans-serif; }}
    main {{ min-height: 100vh; display: grid; place-items: center; }}
    section {{ text-align: center; padding: 28px; }}
    p {{ color: #94a3b8; }}
    #turnstile-container {{ min-height: 70px; }}
  </style>
</head>
<body>
  <main><section>
    <h2>Verify you are human</h2>
    <p>This verification is provided by Cloudflare Turnstile.</p>
    <div id="turnstile-container"></div>
  </section></main>
  <script>
    let bridge = null;
    let widgetRendered = false;

    function reportStartupError(code) {{
      if (bridge) bridge.reportError(code);
    }}

    function renderTurnstile() {{
      if (widgetRendered) return;
      if (!window.turnstile || !bridge) {{ window.setTimeout(renderTurnstile, 100); return; }}
      widgetRendered = true;
      window.turnstile.render("#turnstile-container", {{
        sitekey: {encoded_key},
        theme: "dark",
        callback: function(token) {{
          if (token) bridge.receiveToken(token);
          else bridge.reportError("turnstile_empty_token");
        }},
        "error-callback": function() {{
          widgetRendered = false;
          bridge.reportError("turnstile_error");
        }},
        "expired-callback": function() {{
          widgetRendered = false;
          bridge.reportError("turnstile_expired");
        }}
      }});
    }}

    function initializeWebChannel() {{
      if (typeof QWebChannel === "undefined" || !window.qt || !qt.webChannelTransport) {{
        window.setTimeout(initializeWebChannel, 100);
        return;
      }}
      new QWebChannel(qt.webChannelTransport, function(channel) {{
        bridge = channel.objects.turnstileBridge;
        if (!bridge) return;
        renderTurnstile();
      }});
    }}

    const channelScript = document.createElement("script");
    channelScript.src = "qrc:///qtwebchannel/qwebchannel.js";
    channelScript.onload = initializeWebChannel;
    channelScript.onerror = function() {{ reportStartupError("webchannel_script_error"); }};
    document.head.appendChild(channelScript);

    const turnstileScript = document.createElement("script");
    turnstileScript.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    turnstileScript.async = true;
    turnstileScript.defer = true;
    turnstileScript.onerror = function() {{ reportStartupError("turnstile_script_error"); }};
    document.head.appendChild(turnstileScript);
  </script>
</body>
</html>"""

    @Slot(str)
    def _receive_token(self, token: str) -> None:
        try:
            token = token.strip() if isinstance(token, str) else ""
            _debug_event("token_received", bool(token))
            if not token:
                self._report_error("turnstile_empty_token")
                return
            self.captcha_token = token
            self.accept()
        except Exception:
            _log_exception("returning token")
            self.captcha_token = ""
            self.reject()

    @Slot(str)
    def _report_error(self, error_code: str) -> None:
        try:
            self.captcha_token = ""
            if error_code == "turnstile_expired":
                message = "Security verification expired. Please try again."
            else:
                message = "Security verification failed. Please try again."
            self.status_label.setText(message)
        except Exception:
            _log_exception("verification error handling")
            self.captcha_token = ""
            self.reject()


# Backward-compatible name for callers that imported the earlier class directly.
TurnstileDialog = SecurityVerificationDialog


class LoginDialog(QDialog):
    """Polished desktop login screen shown before the main app when needed."""

    def __init__(
        self,
        auth_service: Optional[AuthService] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.auth_service = auth_service or AuthService.get_instance()
        self.profile = None
        self.captcha_token = ""
        self.setWindowTitle("Sign in to QuizMaster")
        self.setModal(True)
        self.setMinimumSize(520, 670)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._build_ui()
        self._apply_styles()
        self.email_input.textChanged.connect(self._credentials_changed)
        self.password_input.textChanged.connect(self._credentials_changed)
        self._update_sign_in_button_state()
        if not self.auth_service.is_configured():
            self._set_error(self.auth_service.config_error_message())
            self._update_sign_in_button_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame(self)
        card.setObjectName("loginCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(34, 32, 34, 32)
        card_layout.setSpacing(18)

        brand = QLabel(card)
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setStyleSheet("background: transparent;")

        logo_path = get_resource_path("core/assets/images/quizmaster-logo.png")
        logo_pixmap = QPixmap(str(logo_path))

        if logo_pixmap.isNull():
            logger.warning(
                "Could not load QuizMaster login logo from %s",
                logo_path,
            )
            brand.setText("Quiz<span style='color:#06b6d4'>Master</span>")
            brand.setTextFormat(Qt.TextFormat.RichText)
        else:
            scaled_logo = logo_pixmap.scaled(
                360,
                120,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            brand.setPixmap(scaled_logo)
            brand.setMinimumHeight(100)

        subtitle = QLabel("Connect your desktop app to your QuizMaster account.", card)
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)

        self.email_input = QLineEdit(card)
        self.email_input.setPlaceholderText("Email")
        self.email_input.setClearButtonEnabled(True)
        self.email_input.setInputMethodHints(Qt.InputMethodHint.ImhEmailCharactersOnly)

        self.password_input = QLineEdit(card)
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.remember_checkbox = QCheckBox("Remember Me", card)
        self.remember_checkbox.setChecked(True)

        self.error_label = QLabel("", card)
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        self.verify_button = QPushButton("Verify I'm Human", card)
        self.verify_button.setObjectName("secondaryButton")
        self.verify_button.clicked.connect(self._verify_security)

        self.verification_label = QLabel("Security verification required", card)
        self.verification_label.setObjectName("verificationLabel")

        self.sign_in_button = QPushButton("Sign In", card)
        self.sign_in_button.setObjectName("primaryButton")
        self.sign_in_button.clicked.connect(self._sign_in)
        self.password_input.returnPressed.connect(self._handle_password_return)

        link_row = QHBoxLayout()
        link_row.setSpacing(10)
        self.create_account_button = QPushButton("Create Account", card)
        self.create_account_button.setObjectName("linkButton")
        self.create_account_button.clicked.connect(lambda: webbrowser.open(REGISTER_URL))
        self.forgot_password_button = QPushButton("Forgot Password", card)
        self.forgot_password_button.setObjectName("linkButton")
        self.forgot_password_button.clicked.connect(lambda: webbrowser.open(FORGOT_PASSWORD_URL))
        link_row.addWidget(self.create_account_button)
        link_row.addWidget(self.forgot_password_button)

        card_layout.addWidget(brand, alignment=Qt.AlignmentFlag.AlignHCenter)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(10)
        card_layout.addWidget(self.email_input)
        card_layout.addWidget(self.password_input)
        card_layout.addWidget(self.remember_checkbox)
        card_layout.addWidget(self.verify_button)
        card_layout.addWidget(self.verification_label)
        card_layout.addWidget(self.error_label)
        card_layout.addWidget(self.sign_in_button)
        card_layout.addLayout(link_row)

        dashboard_button = QPushButton("Open Web Dashboard", card)
        dashboard_button.setObjectName("secondaryButton")
        dashboard_button.clicked.connect(lambda: webbrowser.open(DASHBOARD_URL))
        card_layout.addWidget(dashboard_button)

        root.addWidget(card)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QDialog {
                background: qradialgradient(cx:0.2, cy:0.1, radius:1.1, stop:0 rgba(8, 145, 178, 80), stop:0.35 #020617, stop:1 #020617);
                color: #e5f9ff;
                font-family: Inter, Arial, sans-serif;
            }
            #loginCard {
                background: rgba(2, 6, 23, 232);
                border: 1px solid rgba(6, 182, 212, 90);
                border-radius: 24px;
            }
            #brand {
                font-family: Orbitron, Arial Black, sans-serif;
                font-size: 34px;
                font-weight: 900;
                letter-spacing: 1px;
                color: white;
                text-transform: uppercase;
            }
            #subtitle {
                color: #94a3b8;
                font-size: 13px;
                line-height: 1.45;
            }
            QLineEdit {
                background: rgba(15, 23, 42, 210);
                border: 1px solid rgba(51, 65, 85, 220);
                border-radius: 12px;
                color: white;
                font-size: 15px;
                padding: 13px 14px;
            }
            QLineEdit:focus {
                border: 1px solid #06b6d4;
            }
            QCheckBox {
                color: #cbd5e1;
                font-weight: 700;
            }
            QCheckBox::indicator {
                width: 17px;
                height: 17px;
            }
            #verificationLabel {
                color: #fbbf24;
                font-weight: 700;
            }
            #errorLabel {
                color: #fecaca;
                background: rgba(127, 29, 29, 90);
                border: 1px solid rgba(248, 113, 113, 120);
                border-radius: 10px;
                padding: 10px;
            }
            QPushButton {
                border-radius: 12px;
                padding: 12px 16px;
                font-weight: 900;
                text-transform: uppercase;
                letter-spacing: 0.08em;
            }
            #primaryButton {
                background: #06b6d4;
                color: #001018;
                border: none;
            }
            #primaryButton:hover { background: #22d3ee; }
            #primaryButton:disabled { background: #334155; color: #94a3b8; }
            #secondaryButton {
                background: rgba(15, 23, 42, 190);
                color: white;
                border: 1px solid rgba(6, 182, 212, 90);
            }
            #secondaryButton:hover { border-color: #06b6d4; }
            #linkButton {
                background: transparent;
                color: #67e8f9;
                border: 1px solid rgba(6, 182, 212, 55);
                text-transform: none;
                letter-spacing: 0;
            }
            #linkButton:hover { background: rgba(6, 182, 212, 35); }
            """
        )

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def _credentials_changed(self) -> None:
        self.captcha_token = ""
        self.verification_label.setText("Security verification required")
        self._update_sign_in_button_state()

    def _update_sign_in_button_state(self) -> None:
        has_credentials = bool(self.email_input.text().strip()) and bool(self.password_input.text())
        configured = self.auth_service.is_configured()
        self.verify_button.setEnabled(configured and has_credentials)
        self.sign_in_button.setEnabled(configured and has_credentials)

    def _handle_password_return(self) -> None:
        self._sign_in()

    def _verify_security(self) -> bool:
        if not self.email_input.text().strip() or not self.password_input.text():
            self._update_sign_in_button_state()
            return False

        self.captcha_token = ""
        captcha_dialog = None
        try:
            captcha_dialog = SecurityVerificationDialog(self)
            _debug_event("verification_dialog_created", True)
            result = captcha_dialog.exec()
            token = captcha_dialog.captcha_token.strip()
            captcha_dialog.captcha_token = ""
            if result == QDialog.DialogCode.Accepted and token:
                self.captcha_token = token
                self.verification_label.setText("Security verification complete")
                self.error_label.hide()
                return True
            self.verification_label.setText("Security verification required")
        except SecurityVerificationStartError as exc:
            _debug_event("verification_dialog_created", False)
            _log_exception("opening verification dialog")
            message = str(exc)
            self._set_error(message)
            QMessageBox.warning(self, "QuizMaster sign in", message)
        except Exception:
            _debug_event("verification_dialog_created", False)
            _log_exception("verification flow")
            message = "Security verification could not start. Please try again."
            self._set_error(message)
            QMessageBox.warning(self, "QuizMaster sign in", message)
        finally:
            if not self.captcha_token:
                self.verification_label.setText("Security verification required")
            if captcha_dialog is not None:
                captcha_dialog.captcha_token = ""
            self._update_sign_in_button_state()
        return False

    @staticmethod
    def _user_facing_error(exc: Exception) -> str:
        if isinstance(exc, AuthLoginError):
            return exc.user_message
        message = str(exc).strip()
        return message or "Sign-in temporarily unavailable"

    def _sign_in(self) -> None:
        if (
            not self.auth_service.is_configured()
            or not self.email_input.text().strip()
            or not self.password_input.text()
        ):
            self._update_sign_in_button_state()
            return
        if not self.captcha_token and not self._verify_security():
            return
        if not self.captcha_token:
            message = "Security verification is required."
            self._set_error(message)
            QMessageBox.warning(self, "QuizMaster sign in", message)
            self._update_sign_in_button_state()
            return
        self._set_error("")
        self.error_label.hide()
        self.sign_in_button.setEnabled(False)
        self.sign_in_button.setText("Signing in…")
        try:
            remember = self.remember_checkbox.isChecked()
            logger.info("Auth diagnostic: remember_checked=%s", remember)
            self.profile = self.auth_service.sign_in(
                self.email_input.text(),
                self.password_input.text(),
                remember=remember,
                captcha_token=self.captcha_token,
            )
            self.captcha_token = ""
            self.accept()
        except Exception as exc:
            message = self._user_facing_error(exc)
            logger.warning("QuizMaster sign in failed: %s", message)
            self._set_error(message)
            if isinstance(exc, AuthLoginError) and exc.failure_type == "captcha_required":
                self.captcha_token = ""
                self.verification_label.setText("Security verification required")
            QMessageBox.warning(self, "QuizMaster sign in", message)
        finally:
            self._update_sign_in_button_state()
            self.sign_in_button.setText("Sign In")
