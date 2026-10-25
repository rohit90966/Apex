"""
Independent verification of the seven Harbor Events defects, outside VerityQA (HTTP level + one browser check
for the client-side NaN). Writes reports/audit/defect_verification.json.
Run: .\\venv\\Scripts\\python.exe reports\\audit\\verify_harbor_defects.py
"""
import importlib
import json
import os
import re
import sys

sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient

CREDS = {"email": "demo@harbor.test", "password": "Harbor#2026"}


def run(mode):
    os.environ["VERITYQA_FIXTURE_MODE"] = mode
    import demo_site.main as m
    importlib.reload(m)  # fresh in-memory state per mode
    c = TestClient(m.app)
    out = []

    def rec(defect, inputs, observed, expected, defective):
        out.append({"defect": defect, "mode": mode, "inputs": inputs, "observed": observed,
                    "expected_correct_behaviour": expected, "defect_observed": defective})

    r = c.post("/register", data=dict(full_name="Ver One", email="not-an-email", password="abcdefgh",
                                      confirm_password="abcdefgh", terms="yes"))
    rec("HE-01", {"email": "not-an-email"}, "account created" if "Account created" in r.text else "rejected: " +
        "; ".join(re.findall(r"<li>([^<]+)</li>", r.text)), "server rejects a malformed email", "Account created" in r.text)

    r = c.post("/register", data=dict(full_name="Ver Two", email="ver2@example.com", password="abcdefgh",
                                      confirm_password="zzzzzzzz", terms="yes"))
    rec("HE-02", {"password": "8 chars", "confirm_password": "different 8 chars"},
        "account created" if "Account created" in r.text else "rejected: " + "; ".join(re.findall(r"<li>([^<]+)</li>", r.text)),
        "server rejects mismatched confirmation", "Account created" in r.text)

    prices = [float(p.replace("$", "").replace(",", "")) for p in
              re.findall(r'event-price">([^<]+)', c.get("/events?sort=price_asc").text)]
    rec("HE-03", {"sort": "price_asc"}, prices, "prices ascending", prices != sorted(prices))

    cats = re.findall(r'event-category">([^<]+)', c.get("/events?category=Music").text)
    rec("HE-04", {"category": "Music"}, cats, "only Music events", any(x != "Music" for x in cats))

    c.post("/login", data=CREDS)
    before = len(m.BOOKINGS)
    r = c.post("/events/2/book", data=dict(attendee_name="Ver", quantity="0"))
    created = len(m.BOOKINGS) > before
    rec("HE-05", {"quantity": "0"}, "booking created" if created else "rejected: " +
        "; ".join(re.findall(r"<li>([^<]+)</li>", r.text)), "server rejects quantity outside 1..10", created)

    r = c.post("/bookings/1/cancel")
    status = re.search(r'id="booking-1".*?booking-status">([^<]+)', r.text, re.S)
    msg = re.search(r'role="status">([^<]+)', r.text)
    rec("HE-06", {"cancel": "booking 1"}, {"message": msg.group(1) if msg else None, "status_after": status.group(1) if status else None},
        "booking 1 status becomes Cancelled", bool(status) and status.group(1) == "Confirmed")

    page = c.get("/events/1?breakdown=1").text
    rec("HE-07", {"breakdown": 1}, {"fee_rate_defined": "HARBOR_FEE_RATE = 0.05" in page},
        "service fee computed from a configured rate (browser check below)", "HARBOR_FEE_RATE = 0.05" not in page)
    return out, m.app


def browser_fee(app, mode):
    import threading, time, socket, uvicorn
    from playwright.sync_api import sync_playwright
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page()
        pg.goto(f"http://127.0.0.1:{port}/events/1?breakdown=1")
        fee = pg.inner_text("#bd-fee"); b.close()
    server.should_exit = True
    return fee


results = []
for mode in ("buggy", "baseline"):
    rows, app = run(mode)
    fee = browser_fee(app, mode)
    for r in rows:
        if r["defect"] == "HE-07":
            r["observed"]["rendered_fee"] = fee
            r["defect_observed"] = "NaN" in fee
    results.extend(rows)

json.dump(results, open("reports/audit/defect_verification.json", "w", encoding="utf-8"), indent=2)
for r in results:
    print(r["mode"], r["defect"], "DEFECT PRESENT" if r["defect_observed"] else "correct", "|", r["observed"])
