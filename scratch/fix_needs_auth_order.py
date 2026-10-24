from pathlib import Path

p = Path("app/test_planning/planner.py")
content = p.read_text(encoding="utf-8")

old_block = '''                # Ensure initial entry navigation for unauthenticated patterns (e.g. login boundaries)
                if not needs_auth and action_steps and not any(s.action == ActionType.NAVIGATE for s in action_steps):
                    action_steps.insert(
                        0,
                        ActionStep(
                            step_number=1,
                            action=ActionType.NAVIGATE,
                            value=f"{target_url}/",
                            description=f"Navigate to {target_url}",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.ELEMENT_VISIBLE,
                                target_selector="body",
                                description="Verify page loaded",
                            ),
                        )
                    )
                    for i, s in enumerate(action_steps):
                        s.step_number = i + 1

                # If authenticated app and pattern targets authenticated area, inject prelude
                needs_auth = bool(auth_prelude) and not any(
                    (s.action == ActionType.FILL and "password" in (s.selector or "").lower())
                    or (s.action == ActionType.CLICK and any(k in (s.selector or "").lower() for k in ("login", "submit")))
                    for s in action_steps
                )'''

new_block = '''                # If authenticated app and pattern targets authenticated area, inject prelude
                needs_auth = bool(auth_prelude) and not any(
                    (s.action == ActionType.FILL and "password" in (s.selector or "").lower())
                    or (s.action == ActionType.CLICK and any(k in (s.selector or "").lower() for k in ("login", "submit")))
                    for s in action_steps
                )

                # Ensure initial entry navigation for unauthenticated patterns (e.g. login boundaries)
                if not needs_auth and action_steps and not any(s.action == ActionType.NAVIGATE for s in action_steps):
                    action_steps.insert(
                        0,
                        ActionStep(
                            step_number=1,
                            action=ActionType.NAVIGATE,
                            value=f"{target_url}/",
                            description=f"Navigate to {target_url}",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.ELEMENT_VISIBLE,
                                target_selector="body",
                                description="Verify page loaded",
                            ),
                        )
                    )
                    for i, s in enumerate(action_steps):
                        s.step_number = i + 1'''

if old_block in content:
    content = content.replace(old_block, new_block, 1)
    p.write_text(content, encoding="utf-8")
    print("Fixed needs_auth order successfully!")
else:
    print("old_block not found!")
