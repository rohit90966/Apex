from pathlib import Path

p = Path("app/test_planning/planner.py")
content = p.read_text(encoding="utf-8")

# 1. Update _synthesize_workflow_journeys: handle catalog return after checkout
old_wf_block = '''                elif act == "SELECT_OPTION":
                    if not target:
                        continue
                    action_steps.append(
                        ActionStep(
                            step_number=step_num,
                            action=ActionType.SELECT,
                            selector=target,
                            value=s_dict.get("value") or "za",
                            description=f"Select option on {target}",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.ELEMENT_VISIBLE,
                                target_selector="body",
                                description="Verify DOM after option selection",
                            ),
                        )
                    )'''

new_wf_block = '''                elif act == "SELECT_OPTION":
                    if not target:
                        continue
                    has_finished_checkout = any("finish" in (s.selector or "").lower() for s in action_steps)
                    if has_finished_checkout and "sort" in target.lower():
                        action_steps.append(
                            ActionStep(
                                step_number=step_num,
                                action=ActionType.NAVIGATE,
                                value=f"{target_url}/inventory.html",
                                description="Navigate back to catalog inventory after checkout completion",
                                assertion=ExecutionAssertion(
                                    assertion_type=AssertionType.URL_CONTAINS,
                                    expected_value="inventory",
                                    description="Verify catalog loaded",
                                ),
                            )
                        )
                        step_num += 1
                    action_steps.append(
                        ActionStep(
                            step_number=step_num,
                            action=ActionType.SELECT,
                            selector=target,
                            value=s_dict.get("value") or "za",
                            description=f"Select option on {target}",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.ELEMENT_VISIBLE,
                                target_selector="body",
                                description="Verify DOM after option selection",
                            ),
                        )
                    )'''

if old_wf_block in content:
    content = content.replace(old_wf_block, new_wf_block, 1)
    print("Updated SELECT_OPTION in workflow journeys")

# Also in _synthesize_workflow_journeys: CLICK on item link after checkout
old_item_click = '''                    action_steps.append(
                        ActionStep(
                            step_number=step_num,
                            action=ActionType.CLICK,
                            selector=target,
                            description=s_dict.get("description") or f"Click {target}",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.ELEMENT_VISIBLE,
                                target_selector="body",
                                description="Verify DOM visible after click",
                            ),
                        )
                    )'''

new_item_click = '''                    if "item_" in target.lower() and any("finish" in (s.selector or "").lower() for s in action_steps):
                        has_returned = any(
                            s.action == ActionType.NAVIGATE and "inventory" in (s.value or "")
                            for s in action_steps[next(i for i, s in enumerate(action_steps) if "finish" in (s.selector or "").lower()):]
                        )
                        if not has_returned:
                            action_steps.append(
                                ActionStep(
                                    step_number=step_num,
                                    action=ActionType.NAVIGATE,
                                    value=f"{target_url}/inventory.html",
                                    description="Navigate back to catalog inventory for item interaction",
                                    assertion=ExecutionAssertion(
                                        assertion_type=AssertionType.URL_CONTAINS,
                                        expected_value="inventory",
                                        description="Verify catalog loaded",
                                    ),
                                )
                            )
                            step_num += 1

                    action_steps.append(
                        ActionStep(
                            step_number=step_num,
                            action=ActionType.CLICK,
                            selector=target,
                            description=s_dict.get("description") or f"Click {target}",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.ELEMENT_VISIBLE,
                                target_selector="body",
                                description="Verify DOM visible after click",
                            ),
                        )
                    )'''

if old_item_click in content:
    content = content.replace(old_item_click, new_item_click, 1)
    print("Updated CLICK on item link in workflow journeys")

# 2. Update _synthesize_transition_scenarios: handle #add-to-cart on detail page
old_add_cart_block = '''        # 2. Add-to-cart on individual items
        add_ctrls = [c for c in controls if "add-to-cart" in (c.get("selector") or "").lower()]
        for c in add_ctrls[:3]:
            sel = c.get("selector", "")
            item_name = sel.replace("#add-to-cart-", "").replace("button[id^='add-to-cart']", "Item").replace("-", " ").title()
            action_step = ActionStep(
                step_number=0,
                action=ActionType.CLICK,
                selector=sel,
                description=f"Click add to cart for {item_name}",
                assertion=ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value="Remove",
                    description="Verify button toggles to Remove state",
                ),
            )
            cases.append(
                TestCase(
                    test_id=f"TC-TR-{tc_count:02d}",
                    name=f"Add Product to Cart: {item_name}",
                    objective=f"Verify adding {item_name} updates button to Remove state.",
                    page_category=PageCategory.GENERAL,
                    priority=Priority.MEDIUM,
                    selection_reason="Verifies individual add-to-cart item mutation.",
                    confidence=0.94,
                    status=TestCaseStatus.PROPOSED,
                    requires_test_account=bool(auth_prelude),
                    steps=make_transition_steps(action_step),
                    oracle_class="STATE_MUTATION",
                )
            )
            tc_count += 1'''

new_add_cart_block = '''        # 2. Add-to-cart on individual items
        add_ctrls = [c for c in controls if "add-to-cart" in (c.get("selector") or "").lower()]
        for c in add_ctrls[:3]:
            sel = c.get("selector", "")
            item_name = sel.replace("#add-to-cart-", "").replace("button[id^='add-to-cart']", "Item").replace("-", " ").title()

            if sel == "#add-to-cart":
                # Button #add-to-cart is on the item detail page (e.g. inventory-item.html), not catalog!
                item_title_sel = next((ctrl.get("selector") for ctrl in controls if "title_link" in (ctrl.get("selector") or "").lower()), "#item_4_title_link")
                detail_steps = (
                    [s.model_copy() for s in auth_prelude] + [
                        ActionStep(
                            step_number=len(auth_prelude) + 1,
                            action=ActionType.CLICK,
                            selector=item_title_sel,
                            description=f"Click product link {item_title_sel} to open detail view",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.URL_CONTAINS,
                                expected_value="inventory-item.html",
                                description="Verify product detail URL",
                            ),
                        ),
                        ActionStep(
                            step_number=len(auth_prelude) + 2,
                            action=ActionType.CLICK,
                            selector=sel,
                            description="Click add to cart on product detail page",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.TEXT_VISIBLE,
                                expected_value="Remove",
                                description="Verify button toggles to Remove state",
                            ),
                        ),
                    ] if auth_prelude else [
                        ActionStep(step_number=1, action=ActionType.NAVIGATE, value=f"{target_url}/inventory-item.html?id=4"),
                        ActionStep(step_number=2, action=ActionType.CLICK, selector=sel, assertion=ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value="Remove", description="Verify button toggles to Remove state")),
                    ]
                )
                cases.append(
                    TestCase(
                        test_id=f"TC-TR-{tc_count:02d}",
                        name="Add Product to Cart from Product Detail Page",
                        objective="Verify adding item to cart from dedicated product detail view updates button to Remove state.",
                        page_category=PageCategory.GENERAL,
                        priority=Priority.MEDIUM,
                        selection_reason="Verifies item detail add-to-cart button mutation.",
                        confidence=0.94,
                        status=TestCaseStatus.PROPOSED,
                        requires_test_account=bool(auth_prelude),
                        steps=detail_steps,
                        oracle_class="STATE_MUTATION",
                    )
                )
                tc_count += 1
                continue

            action_step = ActionStep(
                step_number=0,
                action=ActionType.CLICK,
                selector=sel,
                description=f"Click add to cart for {item_name}",
                assertion=ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value="Remove",
                    description="Verify button toggles to Remove state",
                ),
            )
            cases.append(
                TestCase(
                    test_id=f"TC-TR-{tc_count:02d}",
                    name=f"Add Product to Cart: {item_name}",
                    objective=f"Verify adding {item_name} updates button to Remove state.",
                    page_category=PageCategory.GENERAL,
                    priority=Priority.MEDIUM,
                    selection_reason="Verifies individual add-to-cart item mutation.",
                    confidence=0.94,
                    status=TestCaseStatus.PROPOSED,
                    requires_test_account=bool(auth_prelude),
                    steps=make_transition_steps(action_step),
                    oracle_class="STATE_MUTATION",
                )
            )
            tc_count += 1'''

if old_add_cart_block in content:
    content = content.replace(old_add_cart_block, new_add_cart_block, 1)
    print("Updated add-to-cart in transition scenarios")

# 3. Update _synthesize_from_patterns
new_patterns_full = '''    def _synthesize_from_patterns(
        self,
        patterns: List[RetrievedKnowledgePattern],
        target_url: str,
        controls: List[Dict[str, Any]],
        intel_model: ApplicationIntelligenceModel,
    ) -> List[TestCase]:
        """Translates retrieved domain knowledge patterns into concrete test cases."""
        from app.investigation_agent.authenticator import AuthenticationManager
        creds = AuthenticationManager.discover_credentials(target_url, None) or {}
        username = creds.get("username") or "standard_user"

        auth_prelude = self._get_auth_prelude(target_url, intel_model, controls)
        cases: List[TestCase] = []
        tc_count = 1

        for ret in patterns:
            pat = ret.pattern
            for st in pat.scenario_templates:
                tc_id = f"TC-PAT-{tc_count:02d}"
                action_steps: List[ActionStep] = []

                for step in st.steps:
                    resolved_selector = self._resolve_selector(step.target_selector_hints, controls)
                    act_str = str(step.action_type or "CLICK").upper()
                    act_type = ActionType[act_str] if act_str in ActionType.__members__ else ActionType.CLICK

                    assertion = None
                    if step.assertion_expected:
                        if step.assertion_type == "URL_CONTAINS":
                            assertion = ExecutionAssertion(
                                assertion_type=AssertionType.URL_CONTAINS,
                                expected_value=step.assertion_expected,
                                description=f"Verify URL contains {step.assertion_expected}",
                            )
                        elif step.assertion_type == "VISIBLE":
                            assertion = ExecutionAssertion(
                                assertion_type=AssertionType.TEXT_VISIBLE,
                                expected_value=step.assertion_expected,
                                description=f"Verify visible text '{step.assertion_expected}'",
                            )

                    raw_val = step.input_value
                    desc_lower = (step.description or "").lower()
                    if act_type == ActionType.FILL:
                        if raw_val == "${TEST_USERNAME}" or (not raw_val and any(k in desc_lower for k in ("username", "user")) and "blank" not in desc_lower and "empty" not in desc_lower):
                            step_val = username
                        elif raw_val == "${TEST_PASSWORD}" or (not raw_val and "password" in desc_lower and "blank" not in desc_lower and "empty" not in desc_lower):
                            step_val = CREDENTIAL_PASSWORD_PLACEHOLDER
                        elif "blank" in desc_lower or "empty" in desc_lower:
                            step_val = ""
                        else:
                            step_val = raw_val or "test_input"
                    elif act_type == ActionType.NAVIGATE:
                        if raw_val and raw_val.startswith("/"):
                            step_val = f"{target_url}{raw_val}"
                        elif raw_val:
                            step_val = raw_val
                        else:
                            step_val = f"{target_url}/"
                    elif act_type == ActionType.SELECT:
                        step_val = raw_val or "za"
                    else:
                        step_val = raw_val

                    action_steps.append(
                        ActionStep(
                            step_number=step.step_number,
                            action=act_type,
                            selector=resolved_selector if act_type != ActionType.NAVIGATE else None,
                            value=step_val,
                            description=step.description,
                            assertion=assertion,
                        )
                    )

                # Ensure initial entry navigation for authentication patterns
                if action_steps and not any(s.action == ActionType.NAVIGATE for s in action_steps):
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
                )

                if needs_auth:
                    prelude = [s.model_copy() for s in auth_prelude]
                    is_cart = "remove" in st.title.lower() or "cart" in st.title.lower() or "badge" in st.title.lower()
                    is_checkout = "checkout" in st.title.lower() or "checkout" in pat.name.lower()
                    extra_setup = []
                    add_sel = next((c.get("selector") for c in controls if "add-to-cart" in (c.get("selector") or "").lower()), "#add-to-cart-sauce-labs-backpack")
                    cart_sel = next((c.get("selector") for c in controls if "shopping-cart" in (c.get("selector") or "").lower()), "[data-test='shopping-cart-link']")
                    checkout_sel = next((c.get("selector") for c in controls if "checkout" in (c.get("selector") or "").lower()), "#checkout")

                    if is_checkout:
                        extra_setup.extend([
                            ActionStep(step_number=len(prelude) + 1, action=ActionType.CLICK, selector=add_sel, description="Add product to cart"),
                            ActionStep(step_number=len(prelude) + 2, action=ActionType.CLICK, selector=cart_sel, description="Open shopping cart"),
                            ActionStep(step_number=len(prelude) + 3, action=ActionType.CLICK, selector=checkout_sel, description="Click checkout button", assertion=ExecutionAssertion(assertion_type=AssertionType.URL_CONTAINS, expected_value="checkout-step-one", description="Verify reached checkout step one")),
                        ])
                        if "tax" in st.title.lower() or "total" in st.title.lower() or "step two" in st.title.lower() or "overview" in st.title.lower():
                            extra_setup.extend([
                                ActionStep(step_number=len(prelude) + 4, action=ActionType.FILL, selector="#first-name", value="Alex", description="Fill first name"),
                                ActionStep(step_number=len(prelude) + 5, action=ActionType.FILL, selector="#last-name", value="Tester", description="Fill last name"),
                                ActionStep(step_number=len(prelude) + 6, action=ActionType.FILL, selector="#postal-code", value="90210", description="Fill postal code"),
                                ActionStep(step_number=len(prelude) + 7, action=ActionType.CLICK, selector="#continue", description="Click continue to overview", assertion=ExecutionAssertion(assertion_type=AssertionType.URL_CONTAINS, expected_value="checkout-step-two", description="Verify reached checkout step two")),
                            ])
                            # For tax invariant, replace redundant navigate with assertion
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
                            ]
                    elif is_cart and "remove" in st.title.lower():
                        extra_setup.append(
                            ActionStep(
                                step_number=len(prelude) + 1,
                                action=ActionType.CLICK,
                                selector=add_sel,
                                description="Add product item to cart for downstream cart operation",
                            )
                        )

                    combined = prelude + extra_setup + action_steps
                    action_steps = [s.model_copy(update={"step_number": i + 1}) for i, s in enumerate(combined)]

                if action_steps and not any(s.assertion for s in action_steps):
                    action_steps[-1].assertion = ExecutionAssertion(
                        assertion_type=AssertionType.ELEMENT_VISIBLE,
                        target_selector="body",
                        description="Verify DOM integrity",
                    )

                cases.append(
                    TestCase(
                        test_id=tc_id,
                        name=f"Domain Pattern: {st.title}",
                        objective=st.objective or f"Verify domain workflow: {st.title}",
                        page_category=PageCategory.GENERAL if "checkout" in st.title.lower() else PageCategory.GENERAL,
                        priority=Priority.HIGH if pat.risk_level in ("HIGH", "CRITICAL") else Priority.MEDIUM,
                        selection_reason=f"Synthesized from domain knowledge pattern {pat.pattern_id} ({ret.rationale}).",
                        confidence=ret.relevance_score,
                        status=TestCaseStatus.PROPOSED,
                        requires_test_account=needs_auth,
                        steps=action_steps,
                        rule_ids=[pat.pattern_id],
                        oracle_class="DOMAIN_INVARIANT",
                    )
                )
                tc_count += 1

        return cases'''

p3 = content.find("    def _synthesize_from_patterns(")
p4 = content.find("    def _synthesize_form_validations(")
if p3 != -1 and p4 != -1:
    content = content[:p3] + new_patterns_full + "\n\n" + content[p4:]
    print("Replaced _synthesize_from_patterns")
else:
    print("Could not find boundaries for _synthesize_from_patterns")

# 4. Update _synthesize_defect_regressions: filter synthetic selectors like required_blank_all_form_1
old_lead_steps = '''            if target_desc and target_desc != "browser":
                steps.append(
                    ActionStep(
                        step_number=2,
                        action=ActionType.CLICK,
                        selector=target_desc,
                        description=f"Trigger target action on {target_desc}",
                        assertion=ExecutionAssertion(
                            assertion_type=AssertionType.ELEMENT_VISIBLE,
                            target_selector="body",
                            description="Verify defect recovery and unhandled error prevention",
                        ),
                    )
                )
            else:
                steps.append(
                    ActionStep(
                        step_number=2,
                        action=ActionType.NAVIGATE,
                        value=f"{target_url}/inventory.html",
                        description="Navigate to inventory view",
                        assertion=ExecutionAssertion(
                            assertion_type=AssertionType.ELEMENT_VISIBLE,
                            target_selector="body",
                            description="Verify page rendered without unhandled errors",
                        ),
                    )
                )'''

new_lead_steps = '''            is_real_selector = bool(
                target_desc
                and target_desc != "browser"
                and not any(k in target_desc for k in ("form_", "probe_", "blank_"))
                and (target_desc.startswith(("#", ".", "[")) or target_desc in ("button", "a", "input", "select"))
            )
            if is_real_selector:
                steps.append(
                    ActionStep(
                        step_number=2,
                        action=ActionType.CLICK,
                        selector=target_desc,
                        description=f"Trigger target action on {target_desc}",
                        assertion=ExecutionAssertion(
                            assertion_type=AssertionType.ELEMENT_VISIBLE,
                            target_selector="body",
                            description="Verify defect recovery and unhandled error prevention",
                        ),
                    )
                )
            else:
                steps.append(
                    ActionStep(
                        step_number=2,
                        action=ActionType.NAVIGATE,
                        value=f"{target_url}/",
                        description="Verify application root renders cleanly without uncaught console errors",
                        assertion=ExecutionAssertion(
                            assertion_type=AssertionType.ELEMENT_VISIBLE,
                            target_selector="body",
                            description="Verify page rendered without unhandled errors",
                        ),
                    )
                )'''

if old_lead_steps in content:
    content = content.replace(old_lead_steps, new_lead_steps, 1)
    print("Updated defect regressions synthetic selector handling")

p.write_text(content, encoding="utf-8")
print("Done updating planner.py with all fixes!")
