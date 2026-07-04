"""
core/services/subscription_gate.py — QuizMaster paid-subscription gate.

Single source of truth for deciding whether a signed-in account is entitled to
run QuizMaster. It is intentionally small and dependency-free (no PySide6, no
network, only attribute access on a profile object) so that release builds can
compile it to a native extension (Cython) — keeping the entitlement decision as
machine code instead of decompilable bytecode. Fails closed on anything odd.

Developing: this stays a normal .py you edit and run interpreted in PyCharm. The
compiled .pyd is produced only at release build time (see scripts/
build_quizmaster.ps1 and setup_cython.py) and never replaces this source.
"""

from __future__ import annotations

# Subscription statuses that count as a live, paid entitlement.
_ACTIVE_STATUSES = frozenset({"active", "trialing"})


def is_quizmaster_pro(profile) -> bool:
    """Return True only if *profile* holds an active QuizMaster subscription.

    QuizMaster is a paid app: the account must be on plan ``pro``, and if a
    subscription status is present it must be live (``active``/``trialing``).
    Any missing or unexpected data fails closed to ``False``.
    """
    try:
        plan = str(getattr(profile, "plan", "") or "").strip().lower()
        if plan != "pro":
            return False
        extra = getattr(profile, "extra_fields", {}) or {}
        status = str(extra.get("subscription_status", "") or "").strip().lower()
        if status and status not in _ACTIVE_STATUSES:
            return False
        return True
    except Exception:
        return False
