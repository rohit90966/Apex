with open("app/execution/executor.py", "r", encoding="utf-8") as f:
    text = f.read()

old_code = """        if step.action == ActionType.NAVIGATE:
            if not step.value:
                raise ValueError("NAVIGATE action requires 'value' (target URL)")
            await page.goto(step.value, wait_until="domcontentloaded", timeout=timeout_ms)"""

new_code = """        if step.action == ActionType.NAVIGATE:
            if not step.value:
                raise ValueError("NAVIGATE action requires 'value' (target URL)")
            target_nav = step.value
            if target_nav.startswith("/"):
                cur_url = page.url
                if cur_url and not cur_url.startswith("about:"):
                    from urllib.parse import urlparse
                    p = urlparse(cur_url)
                    target_nav = f"{p.scheme}://{p.netloc}{target_nav}"
            await page.goto(target_nav, wait_until="domcontentloaded", timeout=timeout_ms)"""

if old_code not in text and old_code.replace("\n", "\r\n") in text:
    old_code = old_code.replace("\n", "\r\n")
    new_code = new_code.replace("\n", "\r\n")

assert old_code in text, "old_code not found"
text = text.replace(old_code, new_code, 1)

with open("app/execution/executor.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Successfully updated executor.py navigation handling")
