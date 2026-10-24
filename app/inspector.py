import os
import time
import uuid
import asyncio
from typing import List, Optional
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Error as PlaywrightError
from app.config import get_settings
from app.schemas import (
    InspectionResult,
    FormMetadata,
    FormFieldMetadata,
    ElementMetadata,
)


class WebsiteInspector:
    """
    Playwright-based read-only website inspector.
    Uses sync_playwright executed via asyncio.to_thread to avoid Windows asyncio
    subprocess SelectorEventLoop NotImplementedError conflicts with Uvicorn.
    """

    def __init__(self):
        self.settings = get_settings()

    def _sync_inspect(self, url: str) -> InspectionResult:
        start_time = time.time()
        inspection_id = f"insp-{uuid.uuid4().hex[:10]}"
        screenshot_artifact_id = f"screenshot-{inspection_id}.png"

        artifacts_dir = os.path.abspath(self.settings.verityqa_artifacts_dir)
        os.makedirs(artifacts_dir, exist_ok=True)
        screenshot_path = os.path.join(artifacts_dir, screenshot_artifact_id)

        console_errors: List[str] = []
        page_errors: List[str] = []
        http_status: Optional[int] = None

        with sync_playwright() as p:
            # Launch isolated browser
            browser: Browser = p.chromium.launch(
                headless=self.settings.verityqa_headless,
            )
            context: BrowserContext = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="VerityQA-Inspector/1.0",
                ignore_https_errors=False,
            )
            page: Page = context.new_page()

            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            try:
                response = page.goto(url, wait_until="load", timeout=15000)
                if response:
                    http_status = response.status
            except PlaywrightError as e:
                page_errors.append(f"Navigation warning: {str(e)[:120]}")

            final_url = page.url or url
            page_title = page.title() or ""

            # Extract headings
            headings_data = page.evaluate("""() => {
                const elements = Array.from(document.querySelectorAll('h1, h2, h3'));
                return elements.slice(0, 15).map(el => (el.innerText || '').trim()).filter(t => t.length > 0);
            }""")
            headings = headings_data if isinstance(headings_data, list) else []

            # Extract body text snippet
            text_snippet = page.evaluate("""() => {
                const body = document.body;
                if (!body) return '';
                const text = (body.innerText || '').replace(/\\s+/g, ' ').trim();
                return text.slice(0, 1000);
            }""")

            # Extract buttons
            buttons_data = page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, input[type=button], input[type=submit], [role=button]'));
                return btns.slice(0, 20).map(b => {
                    const text = (b.innerText || b.value || b.getAttribute('aria-label') || '').trim();
                    return text || 'Unnamed Button';
                });
            }""")
            buttons = buttons_data if isinstance(buttons_data, list) else []

            # Extract links
            links_data = page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href]'));
                return links.slice(0, 25).map(a => {
                    const text = (a.innerText || a.getAttribute('aria-label') || a.getAttribute('title') || '').trim();
                    return text || a.getAttribute('href') || 'Link';
                });
            }""")
            links = links_data if isinstance(links_data, list) else []

            # Extract forms and fields metadata
            forms_data = page.evaluate("""() => {
                const forms = Array.from(document.querySelectorAll('form'));
                return forms.slice(0, 10).map((f, idx) => {
                    const fields = Array.from(f.querySelectorAll('input, select, textarea')).slice(0, 25).map(inp => {
                        let labelText = '';
                        if (inp.id) {
                            const lbl = document.querySelector(`label[for="${inp.id}"]`);
                            if (lbl) labelText = lbl.innerText.trim();
                        }
                        if (!labelText && inp.closest('label')) {
                            labelText = inp.closest('label').innerText.trim();
                        }

                        let options = [];
                        if (inp.tagName.toLowerCase() === 'select') {
                            options = Array.from(inp.querySelectorAll('option')).slice(0, 10).map(o => o.innerText.trim());
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
                            options: options.length > 0 ? options : null,
                            // HTML constraints
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

            # Compile input fields summary
            inputs_summary: List[FormFieldMetadata] = []
            for form in forms:
                inputs_summary.extend(form.fields)

            # Capture screenshot
            try:
                page.screenshot(path=screenshot_path, full_page=False)
            except Exception as e:
                page_errors.append(f"Failed to capture screenshot: {str(e)[:100]}")
                screenshot_artifact_id = None

            context.close()
            browser.close()

        duration = round(time.time() - start_time, 2)

        return InspectionResult(
            inspection_id=inspection_id,
            target_url=url,
            final_url=final_url,
            page_title=page_title,
            http_status=http_status,
            headings=headings,
            visible_text_snippet=text_snippet or "",
            buttons=buttons,
            links=links,
            forms=forms,
            input_fields_summary=inputs_summary,
            screenshot_artifact_id=screenshot_artifact_id,
            console_errors=console_errors[:10],
            page_errors=page_errors[:10],
            inspection_duration_seconds=duration,
        )

    async def inspect(self, url: str) -> InspectionResult:
        """Asynchronous wrapper that runs sync Playwright cleanly in a background thread."""
        return await asyncio.to_thread(self._sync_inspect, url)
