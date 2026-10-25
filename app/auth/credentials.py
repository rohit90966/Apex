"""
Credential registry: website profiles bound to exact origins.

Profiles are read from the environment and the git-ignored local `.env` file (environment wins):

    VERITYQA_CREDENTIALS_<PROFILE>_USERNAME / _PASSWORD          the account (required)
    VERITYQA_CREDENTIALS_<PROFILE>_ORIGIN                        scheme://host[:port] it is valid for (required,
                                                                 except for profiles with a built-in public origin)
    VERITYQA_CREDENTIALS_<PROFILE>_LOGIN_URL                     optional: page that hosts the login form
    VERITYQA_CREDENTIALS_<PROFILE>_USERNAME_SELECTOR / _PASSWORD_SELECTOR / _SUBMIT_SELECTOR
                                                                 optional: only when the login form is ambiguous

Resolution is by exact origin (or an explicitly named profile); a similar hostname never matches. Two profiles
for one origin are ambiguous unless a profile name is given. The legacy origin-scoped variables
(VERITYQA_TEST_CREDENTIALS_ORIGIN + VERITYQA_TEST_USERNAME/PASSWORD) are read as a profile named "TEST".
Values are never logged; `describe()` reports profile names and origins only.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PREFIX = "VERITYQA_CREDENTIALS_"
_FIELDS = ("USERNAME_SELECTOR", "PASSWORD_SELECTOR", "SUBMIT_SELECTOR", "LOGIN_URL", "USERNAME", "PASSWORD", "ORIGIN")
# Public demo sites whose origin never changes; local fixtures move between ports, so they need an explicit ORIGIN.
BUILTIN_ORIGINS = {"SAUCEDEMO": "https://www.saucedemo.com"}

FOUND = "FOUND"
MISSING = "AUTH_CREDENTIALS_MISSING"
AMBIGUOUS = "AUTH_CREDENTIALS_AMBIGUOUS"


def canonical_origin(url: str) -> str:
    from app.intelligence.target_context import canonical_origin as _co
    return _co(url or "")


@dataclass
class CredentialProfile:
    name: str
    origin: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    login_url: Optional[str] = None
    username_selector: Optional[str] = None
    password_selector: Optional[str] = None
    submit_selector: Optional[str] = None
    source: str = "ENVIRONMENT"

    def as_credentials(self) -> Dict[str, str]:
        """The dict shape the rest of VerityQA uses (password included: in memory only)."""
        out = {"username": self.username, "password": self.password, "source": self.source, "profile": self.name}
        for k in ("login_url", "username_selector", "password_selector", "submit_selector"):
            if getattr(self, k):
                out[k] = getattr(self, k)
        return out


def _environment() -> Dict[str, str]:
    env: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values
        path = os.environ.get("VERITYQA_ENV_FILE", ".env")
        if os.path.isfile(path):
            env.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    except Exception:
        pass
    env.update(os.environ)
    return env


def load_profiles() -> List[CredentialProfile]:
    env = _environment()
    raw: Dict[str, Dict[str, str]] = {}
    pattern = re.compile(rf"^{PREFIX}(.+?)_({'|'.join(_FIELDS)})$")
    for key, value in env.items():
        m = pattern.match(key)
        if m and value.strip():
            raw.setdefault(m.group(1).upper(), {})[m.group(2)] = value.strip()
    profiles = []
    for name, f in sorted(raw.items()):
        origin = canonical_origin(f.get("ORIGIN") or BUILTIN_ORIGINS.get(name, ""))
        if not (f.get("USERNAME") and f.get("PASSWORD") and origin):
            continue  # incomplete profiles are never used (reported by describe())
        profiles.append(CredentialProfile(
            name=name, origin=origin, username=f["USERNAME"], password=f["PASSWORD"], login_url=f.get("LOGIN_URL"),
            username_selector=f.get("USERNAME_SELECTOR"), password_selector=f.get("PASSWORD_SELECTOR"),
            submit_selector=f.get("SUBMIT_SELECTOR")))
    legacy_origin = canonical_origin(env.get("VERITYQA_TEST_CREDENTIALS_ORIGIN", ""))
    user = env.get("VERITYQA_TEST_USERNAME") or env.get("TARGET_USERNAME")
    pwd = env.get("VERITYQA_TEST_PASSWORD") or env.get("TARGET_PASSWORD")
    if legacy_origin and user and pwd and not any(p.origin == legacy_origin for p in profiles):
        profiles.append(CredentialProfile(name="TEST", origin=legacy_origin, username=user, password=pwd))
    return profiles


def resolve(url: str, profile: Optional[str] = None) -> Tuple[str, Optional[CredentialProfile]]:
    """(FOUND|AUTH_CREDENTIALS_MISSING|AUTH_CREDENTIALS_AMBIGUOUS, profile) for the exact origin of `url`."""
    origin = canonical_origin(url)
    if not origin:
        return MISSING, None
    candidates = [p for p in load_profiles() if p.origin == origin]
    if profile:
        candidates = [p for p in candidates if p.name == profile.upper()]
    if not candidates:
        try:
            from app.investigation_agent.authenticator import AuthenticationManager
            demo = AuthenticationManager.discover_credentials(url, None)
            if demo and demo.get("username") and demo.get("password"):
                return FOUND, CredentialProfile(
                    name=profile.upper() if profile else "DEMO",
                    origin=origin,
                    username=demo["username"],
                    password=demo["password"],
                    source="DEMO_REGISTRY",
                )
        except Exception:
            pass
        return MISSING, None
    if len(candidates) > 1:
        return AMBIGUOUS, None
    return FOUND, candidates[0]


def describe() -> List[Dict[str, str]]:
    """Configured profiles without secrets (for status pages and logs)."""
    env = _environment()
    names = sorted({m.group(1).upper() for k in env for m in [re.match(rf"^{PREFIX}(.+?)_({'|'.join(_FIELDS)})$", k)] if m})
    complete = {p.name: p for p in load_profiles()}
    return [{"profile": n, "origin": complete[n].origin if n in complete else "",
             "status": "complete" if n in complete else "incomplete (needs USERNAME, PASSWORD and ORIGIN)"}
            for n in names] + ([{"profile": "TEST", "origin": complete["TEST"].origin, "status": "complete (legacy variables)"}]
                               if "TEST" in complete and "TEST" not in names else [])


def all_secret_values() -> List[str]:
    """Every configured password (for redaction)."""
    return [p.password for p in load_profiles() if p.password]
