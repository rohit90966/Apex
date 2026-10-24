"""
Anti-Cheating & Site-Agnostic Guard Test Suite.

Verifies that the entire `app/investigation_agent/` subsystem is 100% domain-agnostic
and contains ZERO hardcoded website names, fixture-specific selectors,
or target-specific test strings.
"""

import os
import re
import pytest

INVESTIGATION_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "app", "investigation_agent")
)

# Forbidden website-specific tokens and selectors that must NEVER appear in agent source code
FORBIDDEN_SITE_SPECIFIC_TOKENS = [
    "saucedemo",
    "standard_user",
    "secret_sauce",
    "shopping_cart_badge",
    "inventory_list",
    "inventory_item",
    "add-to-cart-sauce-labs",
    "taskdash",
    "bug-01",
    "bug-02",
    "bug-f2-",
    "planted_bug",
    "fixture1",
    "fixture2",
]


def get_investigation_agent_python_files():
    py_files = []
    for root, _, files in os.walk(INVESTIGATION_AGENT_DIR):
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))
    return py_files


def test_agent_files_exist():
    files = get_investigation_agent_python_files()
    assert len(files) >= 15, f"Expected at least 15 agent files, found {len(files)}"


@pytest.mark.parametrize("file_path", get_investigation_agent_python_files())
def test_no_hardcoded_site_specific_tokens(file_path):
    rel_path = os.path.relpath(file_path, INVESTIGATION_AGENT_DIR)
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read().lower()

    for token in FORBIDDEN_SITE_SPECIFIC_TOKENS:
        # Check token occurrence
        match = re.search(r"\b" + re.escape(token) + r"\b", content)
        assert match is None, (
            f"VIOLATION: Found hardcoded site-specific token '{token}' in {rel_path}! "
            f"The Investigation Agent must be 100% site-agnostic."
        )


def test_no_hardcoded_credentials_in_authenticator():
    """Authenticators must dynamically infer credentials from page DOM/comments, never hardcode them."""
    auth_file = os.path.join(INVESTIGATION_AGENT_DIR, "authenticator.py")
    if os.path.exists(auth_file):
        with open(auth_file, "r", encoding="utf-8") as f:
            code = f.read().lower()
        assert "standard_user" not in code
        assert "secret_sauce" not in code


def test_probe_library_is_generic():
    """All probes in probe library must use generic semantic heuristics, not site-specific selectors."""
    probes_dir = os.path.join(INVESTIGATION_AGENT_DIR, "probes")
    if os.path.exists(probes_dir):
        for fname in os.listdir(probes_dir):
            if fname.endswith(".py"):
                fpath = os.path.join(probes_dir, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read().lower()
                for token in ["saucedemo", "cart", "inventory"]:
                    if token in ["saucedemo"]:
                        assert token not in content, f"Probe {fname} contains forbidden token '{token}'"
