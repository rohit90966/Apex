"""
Perception v2 Module for Stateful Investigation.
Provides robust, site-agnostic DOM extraction, accessibility snapshots,
network ring-buffer event tracking, mutation observation, text anomaly scanning,
and two-level state identity computation (class + instance).
"""

import re
import time
import hashlib
import urllib.parse
from typing import Dict, List, Optional, Any, Tuple
from app.investigation_agent.models import (
    NetworkEvent,
    ConsoleOrPageError,
    ElementReference,
)

AUTH_PARAM_PATTERN = re.compile(
    r"^(token|password|auth|api_key|key|secret|jwt|session|code|access_token|refresh_token)$",
    re.IGNORECASE
)

# Placeholder values are anomalies only where a value is rendered (line start, after ':' or '=', after a
# currency symbol): "Fee: $NaN" is a broken value, "cannot be null" is prose.
VALUE_POSITION = r"(?:(?<=^)|(?<=\n)|(?<=[:=])\s*|(?<=[$\u20ac\u00a3\u20b9])\s*)"
ANOMALY_PATTERNS = [
    re.compile(VALUE_POSITION + r"(undefined)\b", re.MULTILINE),
    re.compile(VALUE_POSITION + r"(NaN)\b", re.MULTILINE),
    re.compile(VALUE_POSITION + r"(null)\b", re.MULTILINE),
    re.compile(r"\[object Object\]"),
    re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
    re.compile(r"Internal Server Error", re.IGNORECASE),
    re.compile(r"ReferenceError:", re.IGNORECASE),
    re.compile(r"TypeError:", re.IGNORECASE),
    re.compile(r"Uncaught Exception", re.IGNORECASE),
    re.compile(r'File ".*?", line \d+'),
    re.compile(r'at [a-zA-Z0-9_\.]+\s+\(.*:\d+:\d+\)'),
]

MUTATION_OBSERVER_INIT_SCRIPT = """
(function() {
    window.__vqa_mut = window.__vqa_mut || [];
    if (window.__vqa_observer_installed) return;
    window.__vqa_observer_installed = true;

    function record(entry) {
        window.__vqa_mut.push(entry);
        if (window.__vqa_mut.length > 300) {
            window.__vqa_mut.shift();
        }
    }

    const observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
            if (m.type === 'childList') {
                for (const node of m.addedNodes) {
                    if (node.nodeType === 1) {
                        record({
                            type: 'added',
                            tag: node.tagName.toLowerCase(),
                            role: node.getAttribute('role') || '',
                            text: (node.innerText || node.textContent || '').trim().slice(0, 80),
                            className: (typeof node.className === 'string' ? node.className : '')
                        });
                    }
                }
                for (const node of m.removedNodes) {
                    if (node.nodeType === 1) {
                        record({
                            type: 'removed',
                            tag: node.tagName.toLowerCase(),
                            role: node.getAttribute('role') || '',
                            text: (node.innerText || node.textContent || '').trim().slice(0, 80)
                        });
                    }
                }
            } else if (m.type === 'characterData') {
                record({
                    type: 'text_change',
                    text: (m.target.nodeValue || '').trim().slice(0, 80)
                });
            }
        }
    });

    function init() {
        const root = document.documentElement || document.body;
        if (root) {
            observer.observe(root, { childList: true, subtree: true, characterData: true });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
"""

DYNAMIC_SIGNALS_SCRIPT = """
() => {
    const signals = {};

    // 1. Generic badge or counter extraction (heuristic)
    const badgeEl = document.querySelector('.badge, [class*="badge"], [class*="counter"], [class*="count"], [aria-label*="badge"], [aria-label*="count"]');
    if (badgeEl) {
        const text = (badgeEl.innerText || badgeEl.getAttribute('aria-label') || '').trim();
        const match = text.match(/\\b\\d+\\b/);
        if (match) {
            signals['counter_value'] = match[0];
            signals['counter_is_heuristic'] = true;
        }
    }

    // 2. Generic Modal / Dialog check
    const modal = document.querySelector('dialog[open], [role="dialog"], [aria-modal="true"], .modal.show, .dialog.open');
    signals['has_active_modal'] = !!modal;

    // 3. Generic Active tab or navigation state
    const activeTab = document.querySelector('[role="tab"][aria-selected="true"], .tab.active, .nav-link.active, [aria-current="page"]');
    if (activeTab) {
        signals['active_tab'] = (activeTab.innerText || activeTab.getAttribute('aria-label') || '').trim();
    }

    // 4. Alerts and status live regions
    const alerts = [];
    let alertLocator = '[role="alert"]';
    const alertSelectors = '[role="alert"], [role="status"], [aria-live="assertive"], [aria-live="polite"], .alert-danger, .error-message, .error-message-container, .invalid-feedback, .field-error, .error, .errors, .alert-error';
    for (const el of document.querySelectorAll(alertSelectors)) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        const t = (el.innerText || '').trim();
        if (t && t.length < 200 && !alerts.includes(t)) {
            alerts.push(t);
            if (alerts.length === 1) {
                if (el.id) alertLocator = '#' + el.id;
                else if (el.getAttribute('role')) alertLocator = `[role="${el.getAttribute('role')}"]`;
                else if (el.className && typeof el.className === 'string') alertLocator = '.' + el.className.trim().split(/\\s+/)[0];
            }
        }
    }
    signals['alerts'] = alerts.slice(0, 5);

    // 5. Fields marked invalid
    // Use :user-invalid (only after user interaction) instead of :invalid, because untouched empty
    // `required` inputs match :invalid at page load and would falsely report a validation error.
    const invalidFields = [];
    let invalidNodes = [];
    try {
        invalidNodes = document.querySelectorAll('[aria-invalid="true"], :user-invalid:not(form)');
    } catch (e) {
        invalidNodes = document.querySelectorAll('[aria-invalid="true"]');
    }
    for (const el of invalidNodes) {
        const sel = el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : el.tagName.toLowerCase());
        if (!invalidFields.includes(sel)) invalidFields.push(sel);
    }
    signals['aria_invalid_fields'] = invalidFields.slice(0, 10);

    // 6. Validation Error message resolution (only active if an actual visible error/alert is present)
    if (alerts.length > 0) {
        signals['has_validation_error'] = true;
        signals['validation_error_message'] = alerts[0];
        signals['validation_error_locator'] = alertLocator;
    } else if (invalidFields.length > 0) {
        // Only flag if there is an accompanying visible non-empty error container
        const hasVisibleErrorContainer = !!document.querySelector('.error-message-container:not(:empty), [role="alert"]:not(:empty), .alert-danger:not(:empty), .invalid-feedback:not(:empty)');
        if (hasVisibleErrorContainer) {
            signals['has_validation_error'] = true;
            signals['validation_error_message'] = 'Form contains invalid fields: ' + invalidFields.join(', ');
            signals['validation_error_locator'] = invalidFields[0];
        }
    }

    // 7. Generic navigation drawer / menu expansion
    const ariaExpBtn = document.querySelector('[aria-expanded="true"]');
    const openDrawer = document.querySelector('.sidebar.open, .nav-drawer.open, [role="navigation"].open, .menu-open, .bm-menu-wrap[aria-hidden="false"]');
    signals['menu_open'] = !!(ariaExpBtn || openDrawer);

    // 8. Active non-default select/dropdown state (e.g. sort/filter controls)
    const activeSelects = [];
    for (const sel of document.querySelectorAll('select')) {
        const rect = sel.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        if (sel.selectedIndex > 0 && sel.value) {
            activeSelects.push(String(sel.value).trim().slice(0, 20));
        }
    }
    if (activeSelects.length > 0) {
        signals['selected_option'] = activeSelects[0];
    }

    // 9. Generic cart item quantity / item count
    const qtyValEl = document.querySelector('.qty-value, [class*="qty-value"], [class*="qty-val"], [aria-label*="quantity value"]');
    if (qtyValEl) {
        const qText = (qtyValEl.innerText || '').trim();
        const qMatch = qText.match(/\\b\\d+\\b/);
        if (qMatch) {
            signals['cart_item_quantity'] = qMatch[0];
        }
    }
    const cartHeading = document.querySelector('[class*="cart-heading"], .cart-title, h1, h2');
    if (cartHeading) {
        const hText = (cartHeading.innerText || '').trim();
        const hMatch = hText.match(/\\((\\d+)\\s+items?\\)/i);
        if (hMatch) {
            signals['cart_item_count'] = hMatch[1];
        }
    }

    return signals;
}
"""

EXTENDED_FORMS_SCRIPT = """
() => {
    const formsData = [];
    for (const f of document.querySelectorAll('form')) {
        const fields = [];
        // Only include actual data entry fields; buttons and submit triggers are not fillable fields
        for (const input of f.querySelectorAll('input:not([type="submit"]):not([type="button"]):not([type="reset"]):not([type="image"]), select, textarea')) {
            const id = input.id || '';
            const name = input.name || '';
            const tag = input.tagName.toLowerCase();
            const type = input.getAttribute('type') || tag;

            // Resolve label
            let labelText = '';
            if (id) {
                const labelEl = document.querySelector('label[for="' + id + '"]');
                if (labelEl) labelText = (labelEl.innerText || '').trim();
            }
            if (!labelText && input.closest('label')) {
                labelText = (input.closest('label').innerText || '').trim();
            }
            if (!labelText) {
                labelText = input.getAttribute('aria-label') || input.getAttribute('placeholder') || '';
            }

            // Stable selector priority: #id, then [name="..."], then role+label
            let stableSel = '';
            if (id) {
                stableSel = '#' + id;
            } else if (name) {
                stableSel = f.id ? '#' + f.id + ' [name="' + name + '"]' : '[name="' + name + '"]';
            } else if (input.getAttribute('aria-label')) {
                stableSel = tag + '[aria-label="' + input.getAttribute('aria-label') + '"]';
            } else {
                stableSel = tag;
            }

            // Options for select
            let selectOptions = [];
            if (tag === 'select') {
                selectOptions = Array.from(input.options).map(o => (o.value || '').trim()).filter(Boolean);  // empty value = placeholder/reset
            }

            fields.push({
                name: name,
                id: id,
                type: type,
                required: input.hasAttribute('required') || input.getAttribute('aria-required') === 'true',
                placeholder: input.getAttribute('placeholder') || '',
                label: labelText.slice(0, 100),
                minlength: input.getAttribute('minlength') ? parseInt(input.getAttribute('minlength')) : null,
                maxlength: input.getAttribute('maxlength') ? parseInt(input.getAttribute('maxlength')) : null,
                min: input.getAttribute('min') || null,
                max: input.getAttribute('max') || null,
                pattern: input.getAttribute('pattern') || null,
                step: input.getAttribute('step') || null,
                autocomplete: input.getAttribute('autocomplete') || null,
                aria_required: input.getAttribute('aria-required') === 'true',
                stable_selector: stableSel,
                select_options: selectOptions,
                checkbox_state: input.type === 'checkbox' || input.type === 'radio' ? input.checked : null,
            });
        }

        // Resolve submit button
        // Strictly prioritize forward progression submit buttons, excluding cancel/back controls
        let submitBtn = f.querySelector('input[type="submit"]:not([id*="cancel"]):not([name*="cancel"]):not([class*="cancel"]):not([value*="Cancel"]):not([value*="cancel"]), button[type="submit"]:not([id*="cancel"]):not([name*="cancel"]):not([class*="cancel"])');
        if (!submitBtn) {
            submitBtn = f.querySelector('button:not([type="button"]):not([id*="cancel"]):not([id*="back"]):not([name*="cancel"]):not([name*="back"]):not([class*="cancel"]):not([class*="back"])');
        }
        if (!submitBtn) {
            submitBtn = f.querySelector('button, input[type="submit"]');
        }
        let submitSelector = '';
        if (submitBtn) {
            const tag = submitBtn.tagName.toLowerCase();
            if (submitBtn.id) {
                submitSelector = '#' + submitBtn.id;
            } else if (submitBtn.getAttribute('name')) {
                submitSelector = `${tag}[name="${submitBtn.getAttribute('name')}"]`;
            } else if (tag === 'input') {
                submitSelector = 'input[type="submit"]';
            } else {
                submitSelector = 'button[type="submit"], button';
            }
        }

        formsData.push({
            form_id: f.id || f.name || ('form_' + (formsData.length + 1)),
            form_selector: f.id ? '#' + f.id : (f.name ? 'form[name="' + f.name + '"]' : 'form'),
            action: f.getAttribute('action') || '',
            method: (f.getAttribute('method') || 'GET').toUpperCase(),
            novalidate: f.hasAttribute('novalidate') || f.noValidate === true,
            submit_control_selector: submitSelector,
            fields: fields
        });
    }
    return formsData;
}
"""


def redact_url_params(url: str) -> str:
    """Redacts authentication, secret, or session tokens in query parameters."""
    if not url or "?" not in url:
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        qsl = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        redacted_qsl = []
        for key, val in qsl:
            if AUTH_PARAM_PATTERN.match(key):
                redacted_qsl.append((key, "[REDACTED]"))
            else:
                redacted_qsl.append((key, val))
        new_query = urllib.parse.urlencode(redacted_qsl)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def scan_text_anomalies(text: str) -> List[str]:
    """Detects software error signatures, uncaught exceptions, and stack traces."""
    if not text:
        return []
    anomalies = []
    for pat in ANOMALY_PATTERNS:
        match = pat.search(text)
        if match:
            found = (match.group(1) if match.groups() else match.group(0)).strip()
            if found not in anomalies:
                anomalies.append(found)
    return anomalies


def anomaly_context(text: str, token: str, width: int = 40) -> str:
    """The rendered line around the first value-position occurrence of an anomaly token (evidence)."""
    for pat in ANOMALY_PATTERNS:
        m = pat.search(text or "")
        if m and (m.group(1) if m.groups() else m.group(0)).strip() == token:
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line = text[line_start:line_end if line_end != -1 else len(text)]
            return line.strip()[: width * 2]
    return token


def compute_state_class_and_instance(
    url: str,
    headings: List[str],
    forms: List[Dict[str, Any]],
    dynamic_signals: Dict[str, Any],
    a11y_snapshot: Optional[str] = None
) -> Tuple[str, str]:
    """
    Computes two-level state identity:
    1. state_class_id: route template (:n for digits/uuids) + a11y skeleton + form fields signature + modal open flag.
    2. state_instance_id: class + normalized dynamic counters/tabs/validation errors/auth.
    """
    # 1. Route template
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    query = parsed.query or ""
    # Normalize numeric segments and uuids into :n
    path_template = re.sub(r'/[0-9]+(?=/|$)', '/:n', path)
    path_template = re.sub(r'/[0-9a-fA-F-]{32,36}(?=/|$)', '/:n', path_template)

    # Normalize query parameters into templates
    norm_query = ""
    if query:
        norm_query = re.sub(r'=[0-9]+(?=&|$)', '=:n', query)
        norm_query = re.sub(r'=[0-9a-fA-F-]{32,36}(?=&|$)', '=:n', norm_query)

    is_parameterized_resource = (":n" in path_template) or (":n" in norm_query)

    if not path_template or path_template == "/":
        path_template = "root"
    else:
        path_template = path_template.strip("/").replace("/", "_")

    # 2. Structural heading skeleton (excludes transient alerts/counters so in-page mutations keep class stability)
    if is_parameterized_resource:
        skeleton = "resource_view"
    else:
        skeleton = "_".join(re.sub(r'[^a-zA-Z_]', '', h.lower()) for h in headings[:2]) or "view"

    # 3. Form field-set signature
    form_sigs = []
    for f in forms:
        fields = f.get("fields", [])
        sig = tuple(sorted((fld.get("name") or fld.get("id") or "", fld.get("type", "")) for fld in fields))
        form_sigs.append(sig)
    form_signature = str(sorted(form_sigs))

    # 4. Modal flag
    has_modal = bool(dynamic_signals.get("has_active_modal"))

    # Compute Class Hash
    class_raw = f"{path_template}|{norm_query}|{skeleton}|{form_signature}|modal:{has_modal}"
    class_hash = hashlib.sha256(class_raw.encode("utf-8")).hexdigest()[:10]
    state_class_id = f"cls_{class_hash}"

    # 5. Compute Instance Identity
    counter_val = dynamic_signals.get("counter_value", "0")
    active_tab = dynamic_signals.get("active_tab", "")
    selected_opt = dynamic_signals.get("selected_option", "")
    with_error = bool(dynamic_signals.get("has_validation_error"))
    menu_open = bool(dynamic_signals.get("menu_open"))

    # Human-friendly instance slug (route + heading + dynamic modifiers)
    clean_route = re.sub(r'[^a-zA-Z0-9_\-]', '', path_template)[:16] or "root"
    raw_heading = headings[0] if headings else "view"
    clean_heading = re.sub(r'[^a-zA-Z_]', '', raw_heading.lower().replace(" ", "_"))[:14] or "view"

    # Semantic functional landmark (generic)
    if dynamic_signals.get("has_catalog_grid"):
        landmark = f"catalog_c{counter_val}"
    elif dynamic_signals.get("has_item_list"):
        landmark = f"itemlist_c{counter_val}"
    elif dynamic_signals.get("has_checkout_form"):
        landmark = "form_flow"
    elif active_tab:
        clean_tab = re.sub(r'[^a-zA-Z0-9_]', '', active_tab.lower())[:10]
        landmark = f"tab_{clean_tab}"
    else:
        landmark = f"c{counter_val}" if counter_val != "0" else "view"

    modifiers = []
    if norm_query:
        clean_query = re.sub(r'[^a-zA-Z0-9_]', '_', norm_query)[:20].strip('_')
        if clean_query:
            modifiers.append(clean_query)
    if selected_opt:
        clean_opt = re.sub(r'[^a-zA-Z0-9_]', '', str(selected_opt).lower())[:10]
        if clean_opt:
            modifiers.append(f"opt_{clean_opt}")
    if menu_open:
        modifiers.append("menu_open")
    if with_error:
        modifiers.append("with_error")

    mod_str = f"_{'_'.join(modifiers)}" if modifiers else ""
    state_instance_id = f"st_{clean_route}_{clean_heading}_{landmark}{mod_str}"

    return state_class_id, state_instance_id
