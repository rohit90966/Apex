from pathlib import Path

p = Path("app/knowledge/patterns/authentication.json")
content = p.read_text(encoding="utf-8")

old_str = '"assertion_expected": "login-button"'
new_str = '"assertion_expected": "Login"'

if old_str in content:
    content = content.replace(old_str, new_str, 1)
    p.write_text(content, encoding="utf-8")
    print("Updated logout assertion in authentication.json")
else:
    print("old_str not found in authentication.json")
