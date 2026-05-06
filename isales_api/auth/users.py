"""Admin user storage.

v1 stage 2 stores a single admin in environment variables (matches the
proposal: ``ISALES_ADMIN_USER`` + ``ISALES_ADMIN_PASSWORD_HASH``). The hash
is bcrypt; bare ``bcrypt.checkpw`` does the verify so the value SHALL be
generated with ``bcrypt.hashpw`` (or any compatible CLI).

Multi-user support is a v2 concern — when it lands the seam is here.
"""

from __future__ import annotations

import os

import bcrypt


def authenticate(username: str, password: str) -> bool:
    """Return True iff the provided credentials match the configured admin."""
    expected_user = os.environ.get("ISALES_ADMIN_USER")
    expected_hash = os.environ.get("ISALES_ADMIN_PASSWORD_HASH")
    if not expected_user or not expected_hash:
        return False
    if username != expected_user:
        return False
    try:
        return bcrypt.checkpw(password.encode(), expected_hash.encode())
    except (ValueError, TypeError):
        return False
