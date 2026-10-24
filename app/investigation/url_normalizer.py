import urllib.parse
from typing import Optional, Set

# Sensitive query parameter keys that must be redacted
SENSITIVE_PARAM_KEYS: Set[str] = {
    "password", "pass", "pwd", "secret", "token", "auth", "api_key",
    "apikey", "access_token", "session", "sessionid", "cvv", "creditcard"
}

# Static file extensions to skip during discovery
STATIC_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".css", ".js", ".map", ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".tar", ".gz", ".mp4", ".mp3", ".wav"
}


def normalize_url(raw_url: str, base_url: Optional[str] = None) -> Optional[str]:
    """
    Normalizes a URL:
    - Resolves relative URLs using base_url
    - Strips fragment identifiers (#...)
    - Lowercases scheme and hostname
    - Standardizes paths and removes trailing slashes (except root '/')
    - Redacts sensitive query parameters
    Returns None if malformed or non-http(s).
    """
    if not raw_url or not isinstance(raw_url, str):
        return None

    clean = raw_url.strip()
    if clean.startswith("javascript:") or clean.startswith("mailto:") or clean.startswith("tel:"):
        return None

    try:
        if base_url:
            resolved = urllib.parse.urljoin(base_url, clean)
        else:
            resolved = clean

        parsed = urllib.parse.urlparse(resolved)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            return None

        hostname = parsed.hostname
        if not hostname:
            return None

        # Format port
        port_suffix = ""
        if parsed.port and (
            (scheme == "http" and parsed.port != 80) or
            (scheme == "https" and parsed.port != 443)
        ):
            port_suffix = f":{parsed.port}"

        netloc = f"{hostname.lower()}{port_suffix}"

        # Normalize path
        path = parsed.path or "/"
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]

        # Check static file extension
        path_lower = path.lower()
        for ext in STATIC_EXTENSIONS:
            if path_lower.endswith(ext):
                return None

        # Redact query parameters
        clean_query = ""
        if parsed.query:
            query_parts = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            redacted_parts = []
            for k, v in query_parts:
                if k.lower() in SENSITIVE_PARAM_KEYS:
                    redacted_parts.append((k, "[REDACTED]"))
                else:
                    redacted_parts.append((k, v))
            clean_query = urllib.parse.urlencode(redacted_parts)

        result = urllib.parse.urlunparse((scheme, netloc, path, "", clean_query, ""))
        return result
    except Exception:
        return None


def is_same_origin(url_a: str, url_b: str) -> bool:
    """Checks whether two URLs share the identical scheme, host, and port."""
    try:
        p_a = urllib.parse.urlparse(url_a)
        p_b = urllib.parse.urlparse(url_b)
        return (
            p_a.scheme.lower() == p_b.scheme.lower() and
            (p_a.hostname or "").lower() == (p_b.hostname or "").lower() and
            (p_a.port or (443 if p_a.scheme == "https" else 80)) ==
            (p_b.port or (443 if p_b.scheme == "https" else 80))
        )
    except Exception:
        return False
