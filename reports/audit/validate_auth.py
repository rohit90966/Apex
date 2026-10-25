"""
End-to-end authentication validation per website (no secrets printed).
For each site: start the fixture (or use the public site), configure its credential profile through
VERITYQA_CREDENTIALS_<PROFILE>_*, then run three tests through the real BrowserExecutor: a protected page,
the same page again (session reuse), and a public page. Prints only auth states, page reached, and counts.

Fixture accounts are the ones defined in each fixture's own source; SauceDemo's are the public demo accounts it
publishes on its login page. Run: .\\venv\\Scripts\\python.exe reports\\audit\\validate_auth.py [--include-public]
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.getcwd())
from app.execution.executor import BrowserExecutor  # noqa: E402
from app.schemas import ActionStep, ActionType, AssertionType, ExecutionAssertion  # noqa: E402

SITES = [
    {"key": "harbor", "module": "demo_site.main:app", "app_dir": None, "port": 8401,
     "creds": ("demo@harbor.test", "Harbor#2026"), "protected": "/bookings", "public": "/events"},
    {"key": "benchmark_shop", "module": "demo_app.benchmark_app.main:app", "app_dir": "archive/legacy_demo_sites", "port": 8403,
     "creds": ("benchmark_user", "secret_benchmark"), "protected": "/products", "public": "/login"},
    {"key": "apex", "module": "demo_app.main:app", "app_dir": "archive/legacy_demo_sites", "port": 8402,
     "creds": ("admin@apex.com", "Secret123!"), "protected": None, "public": "/search"},
    {"key": "taskdash", "module": "demo_app.fixture2.main:app", "app_dir": "archive/legacy_demo_sites", "port": 8404,
     "creds": None, "protected": None, "public": "/tasks"},
]
PUBLIC = {"key": "saucedemo", "base": "https://www.saucedemo.com", "creds": ("standard_user", "secret_sauce"),
          "protected": "/inventory.html", "public": "/"}


def steps(base, path):
    return [ActionStep(step_number=1, action=ActionType.NAVIGATE, value=base + path, setup=True, description="open"),
            ActionStep(step_number=2, action=ActionType.OBSERVE_STATE, description="on page",
                       assertion=ExecutionAssertion(assertion_type=AssertionType.URL_CONTAINS, expected_value=path,
                                                    description="requested page reached"))]


def run_site(site, base):
    profile = site["key"].upper()
    for k in [k for k in os.environ if k.startswith("VERITYQA_CREDENTIALS_") or k.startswith("VERITYQA_TEST_")]:
        del os.environ[k]
    if site["creds"]:
        os.environ[f"VERITYQA_CREDENTIALS_{profile}_ORIGIN"] = base
        os.environ[f"VERITYQA_CREDENTIALS_{profile}_USERNAME"], os.environ[f"VERITYQA_CREDENTIALS_{profile}_PASSWORD"] = site["creds"]
    ex = BrowserExecutor(headless=True)
    ex.session_cache.clear()
    plan = ([("protected", site["protected"]), ("protected again", site["protected"])] if site["protected"] else []) + [("public", site["public"])]
    rows = []
    for label, path in plan:
        res = asyncio.run(ex.execute_steps(f"{site['key']}-{label}", label, steps(base, path)))
        auth = (res.evidence_retained.get("auth") or [{}])[0]
        rows.append({"test": f"{label} {path}", "status": res.status.value, "auth_state": auth.get("state"),
                     "login_performed": auth.get("login_performed"), "session_reused": auth.get("session_reused"),
                     "reason": (res.failure_reason or "")[:100]})
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"site": site["key"], "credentials_configured": bool(site["creds"]), "rows": rows, "counts": counts}


def main():
    out = []
    for site in SITES:
        cmd = [sys.executable, "-m", "uvicorn", site["module"], "--host", "127.0.0.1", "--port", str(site["port"]), "--log-level", "warning"]
        if site["app_dir"]:
            cmd += ["--app-dir", site["app_dir"]]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{site['port']}"
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(base + "/", timeout=1)
                    break
                except Exception:
                    time.sleep(0.25)
            out.append(run_site(site, base))
        finally:
            proc.terminate()
    if "--include-public" in sys.argv:
        try:
            out.append(run_site(PUBLIC, PUBLIC["base"]))
        except Exception as e:
            out.append({"site": "saucedemo", "error": f"not reachable: {type(e).__name__}"})
    os.makedirs("reports/audit", exist_ok=True)
    json.dump(out, open("reports/audit/auth_validation.json", "w", encoding="utf-8"), indent=2)
    for s in out:
        print(f"== {s['site']}: credentials={s.get('credentials_configured')} counts={s.get('counts')} {s.get('error', '')}")
        for r in s.get("rows", []):
            print(f"   {r['test']:<28} {r['status']:<8} auth={r['auth_state']} login={r['login_performed']} reused={r['session_reused']} {r['reason']}")


if __name__ == "__main__":
    main()
