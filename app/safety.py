import ipaddress
import socket
from typing import List, Tuple
from urllib.parse import urlparse


class SafetyViolation(Exception):
    """Raised when a URL violates safety policies."""
    pass


def validate_url_safety(url_str: str, allowed_hosts: List[str]) -> Tuple[bool, str]:
    """
    Validates target URL against safety policies.
    - Protocol must be http or https
    - No credentials (username:password@) in URL
    - Hostname must be in allowed hosts list or resolved safely
    - Rejects private, loopback, link-local, multicast unless explicit localhost/127.0.0.1 is in allowed_hosts
    """
    if not url_str or not isinstance(url_str, str):
        raise SafetyViolation("URL cannot be empty")

    url_str = url_str.strip()
    try:
        parsed = urlparse(url_str)
    except Exception as e:
        raise SafetyViolation(f"Malformed URL: {e}")

    # 1. Enforce scheme
    if parsed.scheme.lower() not in ("http", "https"):
        raise SafetyViolation("Only HTTP and HTTPS protocols are permitted.")

    # 2. Reject embedded credentials
    if parsed.username or parsed.password:
        raise SafetyViolation("URLs with embedded username or password are strictly forbidden.")

    hostname = parsed.hostname
    if not hostname:
        raise SafetyViolation("Invalid URL: Missing hostname.")

    hostname_clean = hostname.lower()
    allowed_hosts_clean = [h.lower() for h in allowed_hosts]

    # Check explicit allowlist first - reject disallowed hosts before unnecessary DNS lookups
    is_directly_allowed = hostname_clean in allowed_hosts_clean
    if not is_directly_allowed:
        raise SafetyViolation(
            f"Host '{hostname_clean}' is not in the configured allowed hosts: {allowed_hosts_clean}. "
            "Add it to VERITYQA_ALLOWED_HOSTS in .env if authorized."
        )

    # 3. DNS resolution & IP safety check (prevent DNS rebinding, cloud metadata, link-local)
    try:
        # Resolve hostname to IP addresses
        addr_infos = socket.getaddrinfo(hostname_clean, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise SafetyViolation(f"Failed to resolve hostname '{hostname_clean}': {e}")

    for family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        # Check loopback exception for demo app / local dev
        is_loopback = ip_obj.is_loopback or ip_str in ("127.0.0.1", "::1", "localhost")
        if is_loopback:
            # Only permit loopback if localhost or 127.0.0.1 is in allowed_hosts
            if not any(lh in allowed_hosts_clean for lh in ("localhost", "127.0.0.1", "::1")):
                raise SafetyViolation(f"Loopback destination {ip_str} is disallowed by current host policy.")
            continue

        # Check cloud metadata endpoints (e.g., 169.254.169.254)
        if ip_obj.is_link_local:
            raise SafetyViolation(f"Link-local / cloud metadata destination {ip_str} is forbidden.")

        # Reject private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) unless explicitly allowlisted
        if ip_obj.is_private and not is_directly_allowed:
            raise SafetyViolation(f"Private network address {ip_str} is not in the explicit allowlist.")

        if ip_obj.is_multicast or ip_obj.is_reserved:
            raise SafetyViolation(f"Multicast/reserved IP destination {ip_str} is forbidden.")

    return True, hostname_clean
