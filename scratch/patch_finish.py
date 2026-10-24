with open("app/test_planning/planner.py", "r", encoding="utf-8") as f:
    text = f.read()

old1 = """                            # For tax invariant, replace redundant navigate with assertion
                            action_steps = [
                                ActionStep(
                                    step_number=len(prelude) + 8,
                                    action=ActionType.CLICK,
                                    selector="#finish",
                                    description="Verify finish button is present and enabled",
                                    assertion=ExecutionAssertion(
                                        assertion_type=AssertionType.ELEMENT_VISIBLE,
                                        target_selector="#finish",
                                        description="Verify finish button visible",
                                    ),
                                )
                            ]"""

new1 = """                            # For tax invariant, advance through checkout and verify order completion
                            action_steps = [
                                ActionStep(
                                    step_number=len(prelude) + 8,
                                    action=ActionType.CLICK,
                                    selector="#finish",
                                    description="Click finish button to complete purchase order",
                                    assertion=ExecutionAssertion(
                                        assertion_type=AssertionType.URL_CONTAINS,
                                        expected_value="checkout-complete",
                                        description="Verify order completed and reached checkout complete",
                                    ),
                                )
                            ]"""

old2 = """                    ActionStep(
                        step_number=len(auth_prelude) + 8,
                        action=ActionType.CLICK,
                        selector="#finish",
                        description="Verify finish button is present and enabled",
                        assertion=ExecutionAssertion(
                            assertion_type=AssertionType.ELEMENT_VISIBLE,
                            target_selector="#finish",
                            description="Verify finish order button visibility",
                        ),
                    ),"""

new2 = """                    ActionStep(
                        step_number=len(auth_prelude) + 8,
                        action=ActionType.CLICK,
                        selector="#finish",
                        description="Click finish button to complete purchase order",
                        assertion=ExecutionAssertion(
                            assertion_type=AssertionType.URL_CONTAINS,
                            expected_value="checkout-complete",
                            description="Verify order completed and reached checkout complete",
                        ),
                    ),"""

# Normalize \r\n to match
if old1 not in text and old1.replace("\n", "\r\n") in text:
    old1 = old1.replace("\n", "\r\n")
    new1 = new1.replace("\n", "\r\n")

if old2 not in text and old2.replace("\n", "\r\n") in text:
    old2 = old2.replace("\n", "\r\n")
    new2 = new2.replace("\n", "\r\n")

assert old1 in text, "old1 not found"
assert old2 in text, "old2 not found"

text = text.replace(old1, new1, 1)
text = text.replace(old2, new2, 1)

with open("app/test_planning/planner.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Successfully replaced finish steps in planner.py")
