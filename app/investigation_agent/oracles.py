"""
Always-On Oracles for Stateful Investigation Probes.
Evaluates every ProbeResult against technical, behavioral, and contradiction invariants:
- Console / page exceptions
- HTTP 5xx server errors
- Failed network requests (excluding intentional user aborts)
- Rendered text anomalies (undefined, NaN, [object Object], Tracebacks)
- Broken internal resources
"""

from typing import List, Dict, Any, Optional
from app.investigation_agent.models import (
    ProbeResult,
    OracleFinding,
    OracleType,
    OracleStrength,
)


def _same_origin(url: str, page_url: str) -> bool:
    from urllib.parse import urlsplit
    a, b = urlsplit(url or ""), urlsplit(page_url or "")
    if not a.netloc or not b.netloc:
        return True  # relative or unknown: treat as the application's own
    return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


def evaluate_always_on_oracles(result: ProbeResult) -> List[OracleFinding]:
    """
    Evaluates always-on oracles on a completed probe execution result.
    Returns a list of structured OracleFinding instances.
    """
    findings: List[OracleFinding] = []
    # Network oracles judge the target application only: requests to other origins (telemetry, analytics, ads,
    # CDNs) can fail for reasons unrelated to the site under test and are never product evidence.
    page_url = (result.before_obs.url if result.before_obs else "") or (result.after_obs.url if result.after_obs else "")
    first_party = lambda net: _same_origin(getattr(net, "url", ""), page_url)

    # 1. Console or Page Errors
    pre_existing_msgs = set()
    if result.before_obs and getattr(result.before_obs, "console_error_messages", None):
        pre_existing_msgs = set(result.before_obs.console_error_messages)

    for err in result.console_errors:
        err_msg = err.message or ""
        # Distinguish pre-existing errors from newly triggered errors
        if err_msg in pre_existing_msgs:
            continue

        # Filter out benign resource/navigation loading 404s and telemetry pings
        err_lower = err_msg.lower()
        if "failed to load resource" in err_lower and ("404" in err_lower or "favicon" in err_lower):
            continue
        if any(k in err_lower for k in ("tracking pixel", "analytics", "favicon.ico", "ping delayed")):
            continue

        findings.append(
            OracleFinding(
                oracle_type=OracleType.TECHNICAL,
                strength=OracleStrength.HIGH,
                title=f"Uncaught {err.error_type.capitalize()} Error",
                description=f"Browser emitted an uncaught error: {err_msg[:200]}",
                evidence_refs=[f"err_seq_{err.seq}"],
                details={"error_type": err.error_type, "message": err_msg, "timestamp": err.timestamp},
            )
        )

    # 2. HTTP 5xx Server Errors
    for net in result.network_events:
        if net.status and 500 <= net.status < 600 and first_party(net):
            snippet = f" Body: {net.response_snippet}" if net.response_snippet else ""
            findings.append(
                OracleFinding(
                    oracle_type=OracleType.TECHNICAL,
                    strength=OracleStrength.HIGH,
                    title=f"Server 5xx Response ({net.status})",
                    description=f"{net.method} {net.url} returned HTTP {net.status}.{snippet}",
                    evidence_refs=[f"net_seq_{net.seq}"],
                    details={
                        "status": net.status,
                        "url": net.url,
                        "method": net.method,
                        "snippet": net.response_snippet,
                    },
                )
            )

    # 3. Failed Network Requests (exclude deliberate aborts)
    for net in result.network_events:
        if not net.ok and net.failure and first_party(net):
            fail_msg = str(net.failure).lower()
            if "abort" in fail_msg or "cancel" in fail_msg:
                continue
            findings.append(
                OracleFinding(
                    oracle_type=OracleType.TECHNICAL,
                    strength=OracleStrength.MEDIUM,
                    title="Network Request Failure",
                    description=f"{net.method} {net.url} failed: {net.failure}",
                    evidence_refs=[f"net_seq_{net.seq}"],
                    details={"url": net.url, "method": net.method, "failure": net.failure},
                )
            )

    # 4. Text Anomalies in rendered DOM
    if result.diff and result.diff.text_anomalies_added:
        for anomaly in result.diff.text_anomalies_added:
            is_high = any(k in anomaly.lower() for k in ("traceback", "internal server error", "[object object]", "referenceerror"))
            from app.investigation_agent.perception import anomaly_context
            context = anomaly_context(getattr(result.after_obs, "text_snippet", "") or "", anomaly) if result.after_obs else anomaly
            findings.append(
                OracleFinding(
                    oracle_type=OracleType.TECHNICAL,
                    strength=OracleStrength.HIGH if is_high else OracleStrength.MEDIUM,
                    title=f"Text Anomaly: '{anomaly}'",
                    description=(f"Detected anomaly '{anomaly}' rendered in visible text after interaction "
                                 f"(rendered: '{context}'). Expected property: displayed values are well-formed; "
                                 f"'{anomaly}' was absent before the interaction and appeared in a value position."),
                    evidence_refs=[f"script_{result.script_id}"],
                    details={"anomaly": anomaly, "rendered_context": context},
                )
            )

    # 5. Broken Internal Resources (4xx responses)
    for net in result.network_events:
        # 401/403 are access control answering an unauthorised request: expected behaviour, not a defect.
        if net.status and 400 <= net.status < 500 and net.status not in (401, 403) and first_party(net):
            findings.append(
                OracleFinding(
                    oracle_type=OracleType.TECHNICAL,
                    strength=OracleStrength.LOW if net.status == 404 else OracleStrength.MEDIUM,
                    title=f"Resource Not Found ({net.status})" if net.status == 404 else f"Client Error Response ({net.status})",
                    description=f"{net.method} {net.url} returned HTTP {net.status}.",
                    evidence_refs=[f"net_seq_{net.seq}"],
                    details={"url": net.url, "status": net.status, "method": net.method},
                )
            )

    return findings
