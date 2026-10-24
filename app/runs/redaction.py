"""
Secret redaction applied to everything a run persists or streams.

Two layers:
- Key-based: string values stored under credential-like keys are replaced.
- Value-based: any occurrence of a known secret value (the target's test password,
  configured API keys) is replaced wherever it appears, e.g. inside journey steps
  such as {"action": "FILL", "value": "<password>"} whose key is not sensitive.
"""

import os
import re
from typing import Any, Iterable, Optional, Set

from app.investigation_agent.handoff_contract import SENSITIVE_KEY_PATTERN, sanitize_text_secrets

REDACTED = "[REDACTED_SECRET]"

# Extends the handoff contract's key pattern with credential containers and cookie headers.
_EXTRA_SENSITIVE_KEYS = re.compile(r"(credential|set-cookie|private_key)", re.IGNORECASE)

_MIN_SECRET_LENGTH = 4


def _is_sensitive_key(key: Any) -> bool:
    return isinstance(key, str) and bool(SENSITIVE_KEY_PATTERN.search(key) or _EXTRA_SENSITIVE_KEYS.search(key))


def redact_secrets(obj: Any, secrets: Optional[Iterable[str]] = None) -> Any:
    """
    Returns a deep copy of obj with secrets redacted.
    Non-string values under sensitive keys (booleans, counts) are kept because they are
    not secrets themselves; nested containers under sensitive keys are still walked.
    """
    secret_set: Set[str] = {s for s in (secrets or []) if isinstance(s, str) and len(s) >= _MIN_SECRET_LENGTH}
    return _redact(obj, secret_set)


def _redact(obj: Any, secrets: Set[str]) -> Any:
    if isinstance(obj, str):
        return sanitize_text_secrets(obj, secrets)
    if isinstance(obj, (list, tuple)):
        return [_redact(item, secrets) for item in obj]
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if _is_sensitive_key(key) and isinstance(value, str) and value:
                out[key] = REDACTED
            else:
                out[key] = _redact(value, secrets)
        return out
    return obj


def collect_known_secrets(target_url: str) -> Set[str]:
    """
    Gathers secret values the investigation engine may have used for this target:
    the credentials AuthenticationManager would discover, every demo-registry password,
    environment-provided test passwords, and configured LLM API keys.
    """
    from app.config import get_settings
    from app.investigation_agent.authenticator import AuthenticationManager

    secrets: Set[str] = set()
    try:
        creds = AuthenticationManager.discover_credentials(target_url or "", None) or {}
        if creds.get("password"):
            secrets.add(creds["password"])
    except Exception:
        pass
    try:
        for entry in AuthenticationManager._get_registry().values():
            if isinstance(entry, dict) and entry.get("pass"):
                secrets.add(entry["pass"])
    except Exception:
        pass
    for env_name in ("VERITYQA_TEST_PASSWORD", "TARGET_PASSWORD"):
        if os.environ.get(env_name):
            secrets.add(os.environ[env_name])
    settings = get_settings()
    for key in (settings.groq_api_key, settings.google_api_key):
        if key:
            secrets.add(key)
    return {s for s in secrets if len(s) >= _MIN_SECRET_LENGTH}
