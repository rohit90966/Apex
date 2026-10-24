with open("app/test_planning/planner.py", "r", encoding="utf-8") as f:
    text = f.read()

old_nav = """                if act in ("NAVIGATE", "GOTO"):
                    if not target or target.rstrip("/") == target_url.rstrip("/"):
                        continue
                    action_steps.append(
                        ActionStep(
                            step_number=step_num,
                            action=ActionType.NAVIGATE,
                            value=target,
                            description=f"Navigate to {target}",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.URL_CONTAINS,
                                expected_value=target.split("/")[-1].split("?")[0] or target,
                                description=f"Verify navigated to {target}",
                            ),
                        )
                    )"""

new_nav = """                if act in ("NAVIGATE", "GOTO"):
                    if not target:
                        continue
                    nav_url = target
                    if nav_url.startswith("/"):
                        nav_url = f"{target_url.rstrip('/')}{nav_url}"
                    elif not nav_url.startswith("http://") and not nav_url.startswith("https://"):
                        nav_url = f"{target_url.rstrip('/')}/{nav_url}"
                    if nav_url.rstrip("/") == target_url.rstrip("/"):
                        continue
                    action_steps.append(
                        ActionStep(
                            step_number=step_num,
                            action=ActionType.NAVIGATE,
                            value=nav_url,
                            description=f"Navigate to {nav_url}",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.URL_CONTAINS,
                                expected_value=target.split("/")[-1].split("?")[0] or "inventory",
                                description=f"Verify navigated to {target}",
                            ),
                        )
                    )"""

if old_nav not in text and old_nav.replace("\n", "\r\n") in text:
    old_nav = old_nav.replace("\n", "\r\n")
    new_nav = new_nav.replace("\n", "\r\n")

assert old_nav in text, "old_nav not found"
text = text.replace(old_nav, new_nav, 1)

with open("app/test_planning/planner.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Successfully updated planner.py navigation handling")
