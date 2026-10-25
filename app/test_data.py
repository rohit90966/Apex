"""
Per-execution unique test data.

Planners write UNIQUE_TOKEN inside input values that the application may store as an identity
(an email or username). Every execution attempt replaces it with a fresh token of the same length,
so repeating a test (suite run, confirmation retry, exported script) does not collide with records
an earlier attempt created, and length-based boundary values stay exact.
"""

import re
import secrets
from typing import Any, Optional

UNIQUE_TOKEN = "${RUN_UNIQUE}"

# Field semantics that commonly carry a uniqueness constraint on the server.
_IDENTITY_WORDS = {"email", "mail", "username", "user", "login", "handle", "account", "nickname"}


def new_token() -> str:
    """Fresh lowercase-hex token, exactly as long as the placeholder it replaces."""
    return secrets.token_hex(len(UNIQUE_TOKEN))[: len(UNIQUE_TOKEN)]


def resolve(value: Optional[str], token: str) -> Optional[str]:
    if value is None or UNIQUE_TOKEN not in value:
        return value
    return value.replace(UNIQUE_TOKEN, token)


def has_identity_semantics(field: Any) -> bool:
    """True for an email field or a text field whose name/label/id names an account identifier."""
    if (getattr(field, "field_type", "") or "").lower() == "email":
        return True
    text = " ".join(str(getattr(field, k, "") or "") for k in ("name", "label", "field_id"))
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).lower()
    return bool(set(re.split(r"[^a-z0-9]+", spaced)) & _IDENTITY_WORDS)
