"""
Safety Governor for Investigation Probes and Actions.
Enforces strict boundaries:
- Evaluates action risk as the stricter of LLM-proposed class and DOM/keyword classification.
- Blocks hazardous commands (financial transactions, data wiping, administrative sabotage, purchasing).
- Enforces host safety, subdomain policy, and mutating action budgets.
- Reconciles configured allowed target hosts with safety rules.
"""

import re
import urllib.parse
from typing import Optional, Tuple, List, Dict, Set, Any
from app.config import get_settings
from app.investigation_agent.models import ActionRisk
from app.safety import validate_url_safety, SafetyViolation


class SafetyGovernor:
    """
    Deterministic safety governor.
    Guarantees probe actions adhere to security boundaries, domain allowlists, and mutation budgets.
    """

    # Action semantics are classified from the action's target text (selector, label, href):
    #   IRREVERSIBLE_COMMIT   - never permitted, on any host
    #   TRANSACTION_COMMIT    - completes a (simulated) transaction; only on trusted/sandbox hosts,
    #                           and only outside a transaction-entry route
    #   TRANSACTION_ENTRY     - enters a transaction flow; only on trusted/sandbox hosts, never
    #                           re-submitted from inside the flow
    # Each class can be extended per deployment with a comma-separated env var, e.g.
    # VERITYQA_TRANSACTION_COMMIT_KEYWORDS="confirm booking". Policy only ever becomes stricter.
    TERMINAL_FORBIDDEN_KEYWORDS = [
        "pay now", "complete purchase", "confirm payment", "delete account",
        "wipe", "drop database", "sudo", "transfer money", "execute code",
        "place order", "buy now", "cancel account",
        "remove user", "terminate subscription"
    ]

    # TRANSACTION_COMMIT semantics (a control that finalises a simulated order or booking)
    CHECKOUT_COMPLETION_KEYWORDS = [
        "finish"
    ]

    # TRANSACTION_ENTRY semantics (a control that enters a checkout / payment flow)
    SANDBOX_SYNTHETIC_CHECKOUT_KEYWORDS = [
        "checkout"
    ]

    TRANSACTION_COMMIT_KEYWORDS = CHECKOUT_COMPLETION_KEYWORDS
    TRANSACTION_ENTRY_KEYWORDS = SANDBOX_SYNTHETIC_CHECKOUT_KEYWORDS

    # High-impact actions that must be blocked by default on all domains
    FORBIDDEN_KEYWORDS = TERMINAL_FORBIDDEN_KEYWORDS + SANDBOX_SYNTHETIC_CHECKOUT_KEYWORDS + CHECKOUT_COMPLETION_KEYWORDS

    MUTATING_KEYWORDS = [
        "delete", "remove", "pay", "order", "purchase", "send", "transfer", "wipe"
    ]

    ALLOWED_SANDBOX_LOCAL_HOSTS = {
        "127.0.0.1", "localhost", "::1", "[::1]", "testserver"
    }

    RISK_HIERARCHY = {
        ActionRisk.SAFE_READ: 1,
        ActionRisk.REVERSIBLE_INTERACTION: 2,
        ActionRisk.POTENTIALLY_MUTATING: 3,
        ActionRisk.UNSAFE_EXTERNAL: 4,
    }

    def __init__(
        self,
        allow_mutating: Optional[bool] = None,
        mutation_budget: int = 50,
        trusted_hosts: Optional[List[str]] = None,
        sandbox_mode: bool = False,
        sandbox_origins: Optional[List[str]] = None,
    ):
        self.settings = get_settings()
        self.mutation_budget = mutation_budget
        self.mutations_executed = 0
        self.rejected_actions: Set[Tuple[str, str, str]] = set()
        self.sandbox_mode: bool = bool(sandbox_mode)
        self.sandbox_origins: List[str] = self._normalize_approved_sandbox_origins(sandbox_origins or [])

        # Deployment-specific action semantics extend (never relax) the built-in classes.
        self.TERMINAL_FORBIDDEN_KEYWORDS = self.TERMINAL_FORBIDDEN_KEYWORDS + self._env_keywords("VERITYQA_IRREVERSIBLE_ACTION_KEYWORDS")
        self.CHECKOUT_COMPLETION_KEYWORDS = self.CHECKOUT_COMPLETION_KEYWORDS + self._env_keywords("VERITYQA_TRANSACTION_COMMIT_KEYWORDS")
        self.SANDBOX_SYNTHETIC_CHECKOUT_KEYWORDS = self.SANDBOX_SYNTHETIC_CHECKOUT_KEYWORDS + self._env_keywords("VERITYQA_TRANSACTION_ENTRY_KEYWORDS")

        # Reconcile configured allowlist from settings and local dev defaults
        base_hosts = [
            "localhost", "127.0.0.1", "127.0.0.1:8001", "127.0.0.1:8002",
            "localhost:8001", "localhost:8002"
        ]
        configured = self.settings.allowed_hosts_list
        all_hosts = base_hosts + configured + (trusted_hosts or [])
        # Deduplicate while preserving lowercased normalized form
        self.trusted_hosts: List[str] = list(dict.fromkeys(h.strip().lower() for h in all_hosts if h.strip()))
        self._allow_mutating = allow_mutating

    @staticmethod
    def _env_keywords(name: str) -> List[str]:
        import os
        return [k.strip().lower() for k in os.environ.get(name, "").split(",") if k.strip()]

    @classmethod
    def _is_local_or_test_hostname(cls, hostname: Optional[str]) -> bool:
        """Verifies that a hostname is strictly a local loopback or designated synthetic test host."""
        if not hostname:
            return False
        h = hostname.strip().lower()
        if h in cls.ALLOWED_SANDBOX_LOCAL_HOSTS:
            return True
        if h.endswith(".local") or h.endswith(".test") or h.endswith(".localhost"):
            return True
        return False

    @classmethod
    def _normalize_approved_sandbox_origins(cls, origins: List[str]) -> List[str]:
        """
        Normalizes and validates explicit sandbox origins.
        Only explicitly approved local or test origins may receive sandbox permissions.
        Wildcards, malformed origins, credentials, or external production hosts are rejected.
        """
        approved: List[str] = []
        for raw in origins:
            if not raw or not isinstance(raw, str):
                continue
            cleaned = raw.strip().lower()
            if not cleaned or "*" in cleaned:
                continue
            try:
                if "://" not in cleaned:
                    continue
                parsed = urllib.parse.urlparse(cleaned)
                if parsed.scheme not in ("http", "https"):
                    continue
                if parsed.username or parsed.password:
                    continue
                if not parsed.netloc or not parsed.hostname:
                    continue
                if not cls._is_local_or_test_hostname(parsed.hostname):
                    continue
                normalized_origin = f"{parsed.scheme}://{parsed.netloc}"
                if normalized_origin not in approved:
                    approved.append(normalized_origin)
            except Exception:
                continue
        return approved

    def is_verified_sandbox_origin(self, current_url: str, target: str = "") -> bool:
        """
        Validates that sandbox mode is explicitly enabled, the current browser origin
        matches an approved local/test sandbox origin, and any navigation/href target
        resolves to the exact same authorized sandbox origin (failing closed on ambiguity).
        """
        if not self.sandbox_mode or not self.sandbox_origins or not current_url or not isinstance(current_url, str):
            return False
        try:
            curr_parsed = urllib.parse.urlparse(current_url.strip())
            if curr_parsed.scheme.lower() not in ("http", "https"):
                return False
            if curr_parsed.username or curr_parsed.password:
                return False
            if not curr_parsed.netloc or not curr_parsed.hostname:
                return False
            if not self._is_local_or_test_hostname(curr_parsed.hostname):
                return False

            curr_origin = f"{curr_parsed.scheme.lower()}://{curr_parsed.netloc.lower()}"
            if curr_origin not in self.sandbox_origins:
                return False

            # Validate destination target if it specifies a URL, relative path, or href attribute
            if target and isinstance(target, str):
                target_clean = target.strip()
                candidate_urls: List[str] = []
                if target_clean.startswith(("http://", "https://", "//", "/")):
                    candidate_urls.append(target_clean)
                for match in re.finditer(r'href=["\']([^"\']+)["\']', target_clean, flags=re.IGNORECASE):
                    candidate_urls.append(match.group(1))
                for match in re.finditer(r'https?://[^\s"\'<>]+', target_clean, flags=re.IGNORECASE):
                    candidate_urls.append(match.group(0))

                for cand in candidate_urls:
                    if any(cand.lower().startswith(p) for p in ("javascript:", "mailto:", "tel:", "data:", "blob:")):
                        return False
                    resolved = urllib.parse.urljoin(current_url, cand)
                    dest_parsed = urllib.parse.urlparse(resolved)
                    if dest_parsed.scheme.lower() not in ("http", "https"):
                        return False
                    if dest_parsed.username or dest_parsed.password:
                        return False
                    dest_origin = f"{dest_parsed.scheme.lower()}://{dest_parsed.netloc.lower()}"
                    if dest_origin != curr_origin or dest_origin not in self.sandbox_origins:
                        return False

            return True
        except Exception:
            return False

    def is_host_trusted(self, host_or_url: str) -> bool:
        """
        Determines if the given host or URL belongs to the authorized allowlist.
        Missing, empty, or malformed URLs fail closed (return False).
        Uses exact matching or verified subdomain policy (not loose substring).
        """
        if not host_or_url or not isinstance(host_or_url, str):
            return False
        host_or_url = host_or_url.strip()
        if not host_or_url:
            return False

        try:
            if "://" in host_or_url:
                parsed = urllib.parse.urlparse(host_or_url)
                netloc = parsed.netloc or parsed.path
            else:
                parsed = urllib.parse.urlparse(f"http://{host_or_url}")
                netloc = parsed.netloc or host_or_url

            if not netloc:
                return False

            host_only = (netloc.split(":")[0]).lower().strip()
            if not host_only:
                return False

            # Check exact match or validated subdomain match
            for th in self.trusted_hosts:
                th_clean = th.lower().strip()
                if not th_clean:
                    continue

                if ":" in th_clean:
                    # Specific port match
                    if netloc.lower() == th_clean:
                        return True
                else:
                    # Hostname match or valid subdomain (.domain.com)
                    if host_only == th_clean:
                        return True
                    if host_only.endswith("." + th_clean):
                        return True

            return False
        except Exception:
            return False

    def allows_mutations_for_url(self, url: str) -> bool:
        """Mutations are allowed if explicitly enabled or on authorized target hosts."""
        if self._allow_mutating is not None:
            return self._allow_mutating
        return self.is_host_trusted(url)

    def assess_risk(
        self,
        action_type: str,
        target_desc: str,
        llm_proposed_risk: Optional[ActionRisk] = None,
        is_post_form: bool = False,
        current_url: str = "",
    ) -> ActionRisk:
        """
        Assesses action risk: returns the stricter of the LLM-proposed class
        and the deterministic DOM/keyword classification.
        """
        target_action_str = f"{action_type} {target_desc}".lower()
        sandbox_verified = self.is_verified_sandbox_origin(current_url, target_desc)

        # Terminal financial, destructive, or administrative actions are always UNSAFE_EXTERNAL
        if any(kw in target_action_str for kw in self.TERMINAL_FORBIDDEN_KEYWORDS):
            dom_risk = ActionRisk.UNSAFE_EXTERNAL
        elif any(kw in target_action_str for kw in self.SANDBOX_SYNTHETIC_CHECKOUT_KEYWORDS) or any(kw in target_action_str for kw in self.CHECKOUT_COMPLETION_KEYWORDS):
            # Checkout progression / completion on authorized test hosts is a reversible interaction;
            # on untrusted or unknown external origins it remains UNSAFE_EXTERNAL
            if self.is_host_trusted(current_url) or sandbox_verified:
                dom_risk = ActionRisk.REVERSIBLE_INTERACTION
            else:
                dom_risk = ActionRisk.UNSAFE_EXTERNAL
        elif action_type in ("FILL", "SUBMIT", "CHECK", "UNCHECK") or is_post_form or any(kw in target_action_str for kw in self.MUTATING_KEYWORDS):
            dom_risk = ActionRisk.POTENTIALLY_MUTATING
        elif action_type in ("CLICK_CONTROL", "PRESS", "SELECT_OPTION", "CLICK", "PRESS_KEY"):
            dom_risk = ActionRisk.REVERSIBLE_INTERACTION
        else:
            dom_risk = ActionRisk.SAFE_READ

        if llm_proposed_risk is None or not isinstance(llm_proposed_risk, ActionRisk):
            return dom_risk

        # Take the stricter (higher integer) risk
        dom_val = self.RISK_HIERARCHY.get(dom_risk, 2)
        llm_val = self.RISK_HIERARCHY.get(llm_proposed_risk, 2)
        return dom_risk if dom_val >= llm_val else llm_proposed_risk

    def can_execute(
        self,
        action_type: str,
        target: str,
        current_url: str,
        llm_proposed_risk: Optional[ActionRisk] = None,
        rationale: str = "",
        dry_run: bool = False,
    ) -> Tuple[bool, str]:
        """
        Evaluates whether an action or probe step is permitted to execute.
        Returns (is_allowed, reason).
        If dry_run=False, records the mutation against budget on approval.
        """
        if isinstance(llm_proposed_risk, str) and not isinstance(llm_proposed_risk, ActionRisk):
            rationale = f"{rationale} {llm_proposed_risk}".strip()
            llm_proposed_risk = None

        target_action_str = f"{action_type} {target}".lower()
        sandbox_verified = self.is_verified_sandbox_origin(current_url, target)

        # 1a. Terminal forbidden keyword guard (unconditionally blocked on all domains)
        for kw in self.TERMINAL_FORBIDDEN_KEYWORDS:
            if kw in target_action_str:
                reason = f"Action blocked by safety policy: high-impact action contains prohibited keyword '{kw}'"
                self.rejected_actions.add((action_type, target, reason))
                return False, reason

        # 1b. Synthetic checkout keyword guard
        # On a checkout route (e.g. /checkout.html), checkout submission is high-impact and strictly blocked.
        # On pre-checkout routes (e.g. /cart.html), navigating to checkout is permitted on authorized hosts/sandboxes.
        for kw in self.SANDBOX_SYNTHETIC_CHECKOUT_KEYWORDS:
            if kw in target_action_str:
                if "/checkout" in (current_url or "").lower():
                    reason = f"Action blocked by safety policy: high-impact action contains prohibited keyword '{kw}'"
                    self.rejected_actions.add((action_type, target, reason))
                    return False, reason
                if not (self.is_host_trusted(current_url) or sandbox_verified):
                    reason = f"Action blocked by safety policy: checkout interaction on untrusted host '{current_url}'"
                    self.rejected_actions.add((action_type, target, reason))
                    return False, reason

        # 1c. Checkout completion keyword guard (e.g. mock #finish button on /checkout-step-two.html)
        for kw in self.CHECKOUT_COMPLETION_KEYWORDS:
            if kw in target_action_str:
                if "/checkout" in (current_url or "").lower():
                    if not (self.is_host_trusted(current_url) or sandbox_verified):
                        reason = f"Action blocked by safety policy: checkout completion interaction on untrusted host '{current_url}'"
                        self.rejected_actions.add((action_type, target, reason))
                        return False, reason

        effective_risk = self.assess_risk(action_type, target, llm_proposed_risk, current_url=current_url)
        is_mutating = (
            effective_risk == ActionRisk.POTENTIALLY_MUTATING
            or action_type in ("FILL", "SUBMIT")
        )

        if not current_url or not self.is_host_trusted(current_url):
            if is_mutating:
                reason = f"SAFETY_BLOCKED: Mutating interactions not permitted on untrusted host ({current_url})"
            else:
                reason = f"SAFETY_BLOCKED: Current page host is not authorized: {current_url}"
            self.rejected_actions.add((action_type, target, reason))
            return False, reason

        # 2. External host & selector validation for navigations
        if action_type in ("NAVIGATE_LINK", "NAVIGATE"):
            target_clean = target.strip()
            # Reject DOM selectors passed as navigation targets
            if target_clean.startswith("[") or target_clean.startswith(".") or (target_clean.startswith("#") and " " in target_clean):
                return False, f"NAVIGATION_REJECTED: Target '{target_clean}' appears to be a DOM selector, not a URL"

            if any(target_clean.startswith(proto) for proto in ("javascript:", "mailto:", "tel:", "data:", "blob:")):
                return False, f"NAVIGATION_REJECTED: Unsupported protocol in navigation target '{target_clean}'"

            if target_clean.startswith("http://") or target_clean.startswith("https://"):
                try:
                    validate_url_safety(target_clean, self.trusted_hosts)
                except SafetyViolation as e:
                    reason = f"SAFETY_BLOCKED: {str(e)}"
                    self.rejected_actions.add((action_type, target, reason))
                    return False, reason

        # 3. Assess combined action risk
        if effective_risk == ActionRisk.UNSAFE_EXTERNAL:
            reason = "SAFETY_BLOCKED: Action classified as UNSAFE_EXTERNAL or high-impact state change"
            self.rejected_actions.add((action_type, target, reason))
            return False, reason

        # 4. Check mutating actions & budget
        if is_mutating:
            if not self.allows_mutations_for_url(current_url):
                reason = f"SAFETY_BLOCKED: Mutating interactions not permitted on untrusted host ({current_url})"
                self.rejected_actions.add((action_type, target, reason))
                return False, reason

            if self.mutations_executed >= self.mutation_budget:
                reason = f"SAFETY_BLOCKED: Mutation budget exhausted ({self.mutations_executed}/{self.mutation_budget})"
                self.rejected_actions.add((action_type, target, reason))
                return False, reason

            if not dry_run:
                self.mutations_executed += 1

        return True, "Action safety approved"

    def can_execute_probe(self, probe: Any, current_url: str) -> Tuple[bool, str]:
        """
        Validates all steps of a probe plan in dry_run mode without consuming budget.
        """
        steps = getattr(probe, "steps", [])
        for step in steps:
            action = getattr(step, "action", None)
            act_type = getattr(step, "action_type", None) or (getattr(action, "action_type", "") if action else "")
            target_str = getattr(step, "raw_target", "") or (
                getattr(action, "target_text", "") or getattr(action, "target_selector", "") if action else ""
            )
            allowed, reason = self.can_execute(
                action_type=str(act_type),
                target=target_str,
                current_url=current_url,
                dry_run=True,
            )
            if not allowed:
                return False, reason
        return True, "Probe approved"

    def record_mutation(self):
        """Records an actual executed mutation against the safety budget."""
        self.mutations_executed += 1
