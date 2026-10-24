import os
import time
import uuid
import asyncio
import logging
from typing import List, Optional, Set
from collections import deque
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Error as PlaywrightError
from app.config import get_settings
from app.safety import validate_url_safety, SafetyViolation
from app.page_classifier import DeterministicClassifier
from app.investigation.url_normalizer import normalize_url, is_same_origin
from app.investigation.models import (
    PageEvidence,
    DiscoveredLink,
    ObservedElement,
    ObservedFeature,
    PageInventory,
    InvestigationReport,
)
from app.investigation.page_inventory import PageInventoryManager
from app.investigation.feature_detector import FeatureDetector
from app.schemas import FormMetadata, FormFieldMetadata, InspectionResult

logger = logging.getLogger(__name__)


class WebsiteCrawler:
    """
    Deterministic, bounded website crawler using Playwright in sync mode (run via asyncio.to_thread).
    Adheres strictly to read-only constraints:
    - Never submits forms or clicks action controls
    - Enforces same-origin bounds, depth caps, and max page limits
    - Validates safety and resolves DNS before every navigation
    """

    def __init__(self):
        self.settings = get_settings()

    def _sync_crawl(self, start_url: str, max_pages: Optional[int] = None, max_depth: Optional[int] = None) -> InvestigationReport:
        start_time = time.time()
        investigation_id = f"inv-{uuid.uuid4().hex[:10]}"
        inventory_mgr = PageInventoryManager()

        budget_pages = max_pages or self.settings.investigation_max_pages
        budget_depth = max_depth or self.settings.investigation_max_depth
        allowed_hosts = self.settings.allowed_hosts_list

        # Validate start URL safety
        validate_url_safety(start_url, allowed_hosts)

        norm_start = normalize_url(start_url)
        if not norm_start:
            raise ValueError(f"Invalid or unsupported start URL: {start_url}")

        queue = deque([(norm_start, 0, start_url)])
        inventory_mgr.queued_urls.add(norm_start)

        artifacts_dir = os.path.abspath(self.settings.verityqa_artifacts_dir)
        os.makedirs(artifacts_dir, exist_ok=True)

        features: List[ObservedFeature] = []
        warnings: List[str] = []
        unresolved: List[str] = []

        with sync_playwright() as p:
            browser: Browser = p.chromium.launch(
                headless=self.settings.verityqa_headless,
            )
            context: BrowserContext = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="VerityQA-DeepInvestigator/1.0",
                ignore_https_errors=False,
            )

            while queue and len(inventory_mgr.pages) < budget_pages:
                curr_url, curr_depth, src_url = queue.popleft()

                # Safety re-validation
                try:
                    validate_url_safety(curr_url, allowed_hosts)
                except SafetyViolation as sv:
                    inventory_mgr.record_skipped(curr_url, f"Safety violation: {sv}")
                    continue

                if not is_same_origin(start_url, curr_url):
                    inventory_mgr.record_skipped(curr_url, "Cross-origin destination")
                    continue

                page_id = f"p-{uuid.uuid4().hex[:8]}"
                screenshot_id = f"screenshot-{investigation_id}-{page_id}.png"
                screenshot_path = os.path.join(artifacts_dir, screenshot_id)

                page_obj = context.new_page()
                console_errs: List[str] = []
                page_obj.on("console", lambda msg: console_errs.append(msg.text) if msg.type == "error" else None)

                try:
                    nav_timeout = self.settings.investigation_nav_timeout_ms
                    res = page_obj.goto(curr_url, wait_until="load", timeout=nav_timeout)
                    http_status = res.status if res else None
                except PlaywrightError as pe:
                    inventory_mgr.record_failed(curr_url, str(pe)[:80])
                    page_obj.close()
                    continue

                final_url = page_obj.url or curr_url
                page_title = page_obj.title() or ""

                # Extract headings
                headings_data = page_obj.evaluate("""() => {
                    const els = Array.from(document.querySelectorAll('h1, h2, h3'));
                    return els.slice(0, 15).map(e => (e.innerText || '').trim()).filter(t => t.length > 0);
                }""")
                headings = headings_data if isinstance(headings_data, list) else []

                # Extract text snippet
                text_snippet = page_obj.evaluate("""() => {
                    const b = document.body;
                    return b ? (b.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 1000) : '';
                }""")

                # Extract buttons
                buttons_data = page_obj.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button, input[type=button], input[type=submit], [role=button]'));
                    return btns.slice(0, 20).map(b => (b.innerText || b.value || b.getAttribute('aria-label') || '').trim()).filter(Boolean);
                }""")
                buttons = buttons_data if isinstance(buttons_data, list) else []

                # Extract structural flags
                struct_flags = page_obj.evaluate("""() => {
                    return {
                        has_tables: Boolean(document.querySelector('table')),
                        has_pagination: Boolean(document.querySelector('.pagination, [role=navigation] [aria-label*=page], ul.pager')),
                        has_dialogs: Boolean(document.querySelector('dialog, [role=dialog], .modal')),
                        has_tabs: Boolean(document.querySelector('[role=tablist], [role=tab]'))
                    };
                }""")

                # Extract forms
                forms_data = page_obj.evaluate("""() => {
                    const forms = Array.from(document.querySelectorAll('form'));
                    return forms.slice(0, 8).map((f, idx) => {
                        const fields = Array.from(f.querySelectorAll('input, select, textarea')).slice(0, 20).map(inp => {
                            let labelText = '';
                            if (inp.id) {
                                const lbl = document.querySelector(`label[for="${inp.id}"]`);
                                if (lbl) labelText = lbl.innerText.trim();
                            }
                            if (!labelText && inp.closest('label')) {
                                labelText = inp.closest('label').innerText.trim();
                            }

                            // Capture HTML constraint attributes
                            const maxlen = inp.maxLength >= 0 ? inp.maxLength : null;
                            const minlen = inp.minLength >= 0 ? inp.minLength : null;

                            return {
                                name: inp.name || inp.id || '',
                                field_id: inp.id || null,
                                input_type: (inp.tagName.toLowerCase() === 'textarea') ? 'textarea' : (inp.type || 'text'),
                                label: labelText || inp.getAttribute('aria-label') || '',
                                placeholder: inp.placeholder || '',
                                is_required: Boolean(inp.required || inp.getAttribute('aria-required') === 'true'),
                                maxlength: (maxlen !== null && maxlen > 0) ? maxlen : null,
                                minlength: (minlen !== null && minlen > 0) ? minlen : null,
                                min_value: inp.getAttribute('min') || null,
                                max_value: inp.getAttribute('max') || null,
                                step: inp.getAttribute('step') || null,
                                pattern: inp.getAttribute('pattern') || null,
                                autocomplete: inp.getAttribute('autocomplete') || null,
                                disabled: inp.disabled || false,
                                readonly: inp.readOnly || false,
                                multiple: inp.multiple || false,
                                checked: inp.type === 'checkbox' || inp.type === 'radio' ? inp.checked : null,
                            };
                        });
                        return {
                            form_id: f.id || `form-${idx+1}`,
                            action: f.getAttribute('action') || '',
                            method: (f.getAttribute('method') || 'GET').toUpperCase(),
                            fields: fields
                        };
                    });
                }""")

                forms: List[FormMetadata] = []
                if isinstance(forms_data, list):
                    for f in forms_data:
                        fields = [FormFieldMetadata(**fld) for fld in f.get("fields", [])]
                        forms.append(FormMetadata(
                            form_id=f.get("form_id"),
                            action=f.get("action"),
                            method=f.get("method"),
                            fields=fields,
                        ))

                # Screenshot capture
                screenshot_saved = None
                try:
                    page_obj.screenshot(path=screenshot_path, full_page=False)
                    screenshot_saved = screenshot_id
                except Exception:
                    screenshot_saved = None

                # Extract eligible links for discovery
                raw_links = page_obj.evaluate("""() => {
                    const aTags = Array.from(document.querySelectorAll('a[href]'));
                    return aTags.slice(0, 40).map(a => ({
                        href: a.getAttribute('href') || '',
                        text: (a.innerText || a.getAttribute('aria-label') || '').trim()
                    }));
                }""")

                discovered_page_links: List[str] = []
                if isinstance(raw_links, list) and curr_depth < budget_depth:
                    for link_item in raw_links:
                        raw_href = link_item.get("href", "")
                        norm_href = normalize_url(raw_href, base_url=final_url)
                        if norm_href and is_same_origin(start_url, norm_href):
                            discovered_page_links.append(norm_href)
                            link_entry = DiscoveredLink(
                                raw_url=raw_href,
                                normalized_url=norm_href,
                                source_url=curr_url,
                                link_text=link_item.get("text"),
                                depth=curr_depth + 1,
                                is_same_origin=True,
                                status="DISCOVERED",
                            )
                            if not inventory_mgr.is_already_seen(norm_href):
                                inventory_mgr.record_discovered_link(link_entry)
                                queue.append((norm_href, curr_depth + 1, curr_url))

                # Deterministic page classification
                insp_stub = InspectionResult(
                    inspection_id=page_id,
                    target_url=curr_url,
                    final_url=final_url,
                    page_title=page_title,
                    http_status=http_status,
                    headings=headings,
                    buttons=buttons,
                    links=discovered_page_links,
                    forms=forms,
                )
                cls_res = DeterministicClassifier.classify(insp_stub)

                evidence = PageEvidence(
                    page_id=page_id,
                    url=curr_url,
                    final_url=final_url,
                    page_title=page_title,
                    depth=curr_depth,
                    http_status=http_status,
                    page_category=cls_res.page_category,
                    headings=headings,
                    visible_text_snippet=text_snippet or "",
                    buttons=buttons,
                    forms=forms,
                    discovered_links=discovered_page_links,
                    has_tables=struct_flags.get("has_tables", False) if isinstance(struct_flags, dict) else False,
                    has_pagination=struct_flags.get("has_pagination", False) if isinstance(struct_flags, dict) else False,
                    has_dialogs=struct_flags.get("has_dialogs", False) if isinstance(struct_flags, dict) else False,
                    has_tabs=struct_flags.get("has_tabs", False) if isinstance(struct_flags, dict) else False,
                    screenshot_artifact_id=screenshot_saved,
                    console_errors=console_errs[:5],
                )

                inventory_mgr.record_inspected_page(evidence)

                # Feature detection on page
                page_features = FeatureDetector.detect_features(evidence)
                features.extend(page_features)

                page_obj.close()

            context.close()
            browser.close()

        # Deduplicate features across pages
        dedup_features: List[ObservedFeature] = []
        seen_feat_keys: Set[str] = set()
        for f in features:
            key = f"{f.category}:{f.name}"
            if key not in seen_feat_keys:
                seen_feat_keys.add(key)
                dedup_features.append(f)
            else:
                # Merge associated urls and evidence
                for existing in dedup_features:
                    if f"{existing.category}:{existing.name}" == key:
                        existing.associated_urls = list(set(existing.associated_urls + f.associated_urls))
                        existing.supporting_evidence_ids = list(set(existing.supporting_evidence_ids + f.supporting_evidence_ids))

        duration = round(time.time() - start_time, 2)
        completeness = "PARTIAL_BOUNDED" if queue else "COMPLETE_WITHIN_BUDGET"
        if queue:
            warnings.append(f"Discovery stopped at budget limit ({budget_pages} pages). {len(queue)} eligible link(s) remaining in queue.")

        return InvestigationReport(
            investigation_id=investigation_id,
            starting_url=start_url,
            origin=start_url,
            inventory=inventory_mgr.build_inventory(),
            features=dedup_features,
            discovery_completeness=completeness,
            unresolved_questions=unresolved,
            warnings=warnings,
            duration_seconds=duration,
        )

    async def investigate(self, start_url: str, max_pages: Optional[int] = None, max_depth: Optional[int] = None) -> InvestigationReport:
        """Asynchronously executes bounded sync website crawler in a background thread."""
        return await asyncio.to_thread(self._sync_crawl, start_url, max_pages, max_depth)
