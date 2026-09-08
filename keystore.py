"""Optional OS-keyring storage for API keys.

The keyring is a *best-effort* enhancement: on a machine without a usable
backend (headless Linux, missing `keyring` package) every function fails soft
and the caller falls back to plaintext storage in ``settings.json``.
"""

from __future__ import annotations

try:
    import keyring
    from keyring.errors import KeyringError
    _KEYRING_IMPORTED = True
except Exception:  # ImportError or a broken backend import
    keyring = None
    KeyringError = Exception
    _KEYRING_IMPORTED = False

SERVICE = "pyqoa"

_available: bool | None = None


def available() -> bool:
    """True if a usable keyring backend is present (probed once, then cached)."""
    global _available
    if _available is not None:
        return _available
    if not _KEYRING_IMPORTED:
        _available = False
        return False
    try:
        backend = keyring.get_keyring()
        name = type(backend).__module__ + "." + type(backend).__name__
        # `fail.Keyring` is the sentinel backend installed when nothing works.
        _available = "fail" not in name.lower()
    except Exception:
        _available = False
    return _available


def account_for(profile: str) -> str:
    """Keyring account name holding the API key of a provider profile."""
    return f"profile:{profile or 'Default'}"


def get_key(account: str) -> str:
    if not available():
        return ""
    try:
        return keyring.get_password(SERVICE, account) or ""
    except Exception:
        return ""


def set_key(account: str, value: str) -> bool:
    if not available():
        return False
    try:
        if value:
            keyring.set_password(SERVICE, account, value)
        else:
            delete_key(account)
        return True
    except Exception:
        return False


def delete_key(account: str) -> bool:
    if not available():
        return False
    try:
        keyring.delete_password(SERVICE, account)
        return True
    except Exception:
        return False
