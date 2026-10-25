# Dynamic, site-agnostic authentication and session management

## Root causes of the earlier authentication failures
1. A built-in "demo registry" keyed only by `host:port` handed the old Apex account to Harbor Events on port 8001
   (all 35 tests BLOCKED). Fixed earlier: the registry is never applied to localhost; now replaced by origin-bound
   website profiles.
2. Login verification treated any page with `<main>` as authenticated. Fixed: evidence-based (no error, no visible
   password field after submitting; logout control as supporting evidence).
3. Login used fixed selectors (`#user-name`, `#password`, `#login-button`) and a static login prelude written into
   every test; a missing account was only detectable as a failed step. Now: DOM-discovered login at run time,
   explicit authentication states, and session reuse.

## Architecture
| Module | Role |
|---|---|
| `app/auth/credentials.py` | Profiles `VERITYQA_CREDENTIALS_<PROFILE>_*` from env + `.env`, bound to an exact origin; legacy `VERITYQA_TEST_*` read as profile `TEST`; ambiguous origin → no guess |
| `app/auth/login.py` | One discovery script (login forms, DOM-derived selectors incl. structural paths, CAPTCHA/MFA, errors, logout/login links); `choose_login_form`, `judge_submission`; sync + async login; `AuthState` enum |
| `app/auth/session.py` | `ensure_page()`: open page → detect gate (redirect, or in-place form on a page known to need a session) → login with the origin's profile → retry once → verify the page was reached; `SessionCache` (in-memory storage state per origin+profile, per run) |
| `app/execution/executor.py` | Setup `NAVIGATE` steps go through `ensure_page`; fresh context per test, started from the cached session of the same origin/profile; `AuthSetupFailed` → BLOCKED with state; `evidence_retained["auth"]` |
| `app/execution/runner.py` | Session cache cleared at the start of each run |
| `app/test_planning/planner.py` | Entry navigation = one setup `NAVIGATE` with `requires_login` (investigation evidence) and `login_url` (observed login page); no credential placeholders in plans |
| `app/investigation_agent/authenticator.py` | `discover_credentials` → profiles first; `attempt_authentication` → shared DOM-discovery login (investigation + reproducer) |
| `app/export/playwright_exporter.py` | `open_page()` helper with the same rules; `credential()` reads origin-bound profiles |
| `app/runs/redaction.py` | All profile passwords are known secrets |
| `.env.example`, `.gitignore` | Placeholders only; `.env.*`, storage-state and HAR files ignored |

## States
`UNKNOWN, AUTH_NOT_REQUIRED, AUTH_REQUIRED, AUTH_CREDENTIALS_MISSING, LOGIN_IN_PROGRESS, AUTHENTICATED, LOGIN_FAILED,
SESSION_EXPIRED, AUTH_UNRESOLVED`. Any state other than AUTHENTICATED/AUTH_NOT_REQUIRED on a setup step makes the
test **BLOCKED** with `SETUP_FAILED: … <STATE>: <reason>` (no assertion counted, no password in the reason).

## Tests (`tests/test_dynamic_auth.py`, 21 tests, synthetic "Vault" fixture)
login with profile · missing credentials (no login attempted) · wrong password · public page not forced through
login · public page containing a login form · protected page redirecting to login · in-place login gate · session
reuse (1 server login for 2 tests) · session expiry → one re-login · different form structure (no ids, input
submit) · two origins with separate passwords and sessions · exact-origin resolution / ambiguity · cart item created
as a prerequisite (and missing prerequisite ≠ product failure) · CAPTCHA / MFA / ambiguous forms → AUTH_UNRESOLVED ·
redaction of results, evidence and registry description · logout in one test does not break the next · reuse
disabled → fresh login per test · investigation login via profile + discovery · planner emits auth-aware entries.

## Per-site validation (`reports/audit/validate_auth.py`, real executor, `reports/audit/auth_validation.json`)
| Site | Credentials | Protected route | Login | Session reused | Result |
|---|---|---|---|---|---|
| Harbor Events (fixture) | profile HARBOR | `/bookings` (redirects to /login) | yes | yes (2nd test) | 3/3 PASS |
| SauceDemo (public) | profile SAUCEDEMO (published demo account) | `/inventory.html` | yes | yes | 3/3 PASS |
| Benchmark Shop (legacy) | profile | none — `/products` is not protected in this fixture | not needed | – | 3/3 PASS |
| Apex (legacy) | profile | none observed (login only shows a page) | not needed | – | 1/1 PASS |
| TaskDash (legacy) | none | none | not needed | – | 1/1 PASS |

## Limitations
* `requires_login` hints come from investigation states, which do not yet distinguish anonymous and authenticated
  views of the same page; redirect-based protection is always detected at run time.
* Session reuse is per run and in memory; nothing is persisted (by design). Exported scripts log in per test.
* CAPTCHA/MFA are detected and reported, never solved. Multiple accounts per origin require an explicit profile name.
