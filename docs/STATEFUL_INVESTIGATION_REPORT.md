# VerityQA — Stateful Investigation, Frontier Intelligence & End-to-End Application Understanding

**Project:** VerityQA — AI-Powered Autonomous QA Engineering  
**Problem Statement:** iMocha CODE APEX 3.0 — IM-02, Autonomous AI QA Engineer  
**Execution Scope:** Investigation Only (`User Input → Website Investigation → Application Intelligence Model → Investigation Report`)  
**Status:** Complete, Generalized, Verified & Green (310/310 Passing Tests)

---

## 1. Initial Audit Findings & Root Causes

### 1.1 Root Causes of Previous Shallow Investigation (Halting at 2 States)
1. **Volatile / URL-Only State Identifiers**:
   - The system previously identified states with simplistic URL hashing. Because clicking *"Add to cart"* does not change the URL (`/inventory.html`), the before-and-after states collapsed into the exact same node, obscuring meaningful dynamic in-page progress.
2. **Anchor Link Extraction Failure**:
   - High-value application landmarks (like the shopping cart icon `a.shopping_cart_link`) had no static `href` attribute (operating via JavaScript router navigation). The DOM extractor was looking solely for `a[href]`, thus missing the cart entirely.
3. **Overly Restrictive Action Filtering**:
   - The safety filter previously contained a blanket block on the word `"checkout"`. While designed to prevent real payment execution, it prematurely halted exploration before the agent could inspect the preliminary customer information form (`checkout-step-one.html`).
4. **Frontier Scheduling Blindness**:
   - The frontier prioritized candidate actions in isolation without considering dynamic application landmarks. Once on the inventory page, it repeatedly clicked generic product titles or low-value links instead of progressing from adding an item $\to$ viewing the populated cart $\to$ inspecting the checkout flow.

---

## 2. Architecture & Modules Changed

| File | Purpose of Targeted Modification |
| :--- | :--- |
| [`app/investigation_agent/browser_session.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/browser_session.py) | **Semantic State Identification**: Formulates stable, distinct state signatures based on `Route + Primary Heading + Cart State + Functional Landmark Tag` (`st_{route}_{heading}_{landmark}`). Captures SPA anchor elements even without `href` attributes. |
| [`app/investigation_agent/frontier.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/frontier.py) | **Dynamic Workflow Frontier Prioritization**: Actively schedules progressive workflows. Boosts cart navigation once items are added (`priority 4.0`), promotes checkout forms from the cart (`priority 4.5`), and de-prioritizes redundant repeated clicks. |
| [`app/investigation_agent/browser_actions.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/browser_actions.py) | **Refined Consequential Action Safety**: Permits safe exploration of checkout customer information forms while strictly blocking irreversible payment commitments (`pay now`, `complete purchase`, `confirm payment`). |
| [`app/investigation_agent/models.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/models.py) | **Data Models & State Graphs**: Preserves rich UI state observations, verified transition records, and the 10-section investigation report. |
| [`app/investigation_agent/workflow.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/workflow.py) | **LangGraph 11-Stage Workflow**: Manages cyclic state updates, action proposal, evidence persistence, and budget enforcement. |
| [`tests/test_investigation_agent.py`](file:///d:/Hackathon/Code%20Apex/tests/test_investigation_agent.py) | **Focused Investigation Suite**: 7 unit and integration tests covering auth boundaries, frontier prioritization, safety rejections, and investigation-only guarantees. |

---

## 3. The State Identity Algorithm

To distinguish relevant application conditions without creating an infinite explosion of states:

$$\text{State ID} = \texttt{st\_\{clean\_route\}\_\{clean\_heading\}\_\{landmark\_tag\}}$$

Where:
- **`clean_route`**: The normalized URL path without query params or domain (e.g. `inventory.html`, `cart.html`, `checkout-step-one.html`).
- **`clean_heading`**: The primary semantic landmark text (e.g. `products`, `your_cart`, `checkout:_yo`).
- **`landmark_tag`**: Evaluated directly from dynamic DOM signals:
  - If inventory list present: `inv_c{badge}` (e.g., `inv_c0` vs `inv_c1` when 1 item is added to the cart).
  - If cart list present: `cart_c{badge}` (e.g., `cart_c1`).
  - If checkout inputs present: `chk_info`.
  - If authentication form present: `auth_entry`.

### Example of Meaningful State Distinction:
1. `st_inventory.html_products_inv_c0` $\to$ Authenticated inventory page with **empty cart** (0 items).
2. `st_inventory.html_products_inv_c1` $\to$ Authenticated inventory page with **1 product selected** (cart badge = 1).
3. `st_cart.html_your_cart_cart_c1` $\to$ Dedicated **Cart page** displaying the selected product.
4. `st_checkout-step-one.html_checkout:_yo_chk_info` $\to$ Customer information **Checkout page**.

---

## 4. Empirical Live Investigation Trace on SauceDemo

Running the live investigation against `https://www.saucedemo.com/`:

```
Testing deep stateful investigation against saucedemo.com...
Overview Status: InvestigationStatus.COMPLETED
Pages discovered: 4
States discovered: 5
Transitions observed: 5

State nodes discovered:
  - [st_root_view_auth_entry] Swag Labs (https://www.saucedemo.com/)
  - [st_inventory.html_products_inv_c0] Swag Labs (https://www.saucedemo.com/inventory.html)
  - [st_inventory.html_products_inv_c1] Swag Labs (https://www.saucedemo.com/inventory.html)
  - [st_cart.html_your_cart_cart_c1] Swag Labs (https://www.saucedemo.com/cart.html)
  - [st_checkout-step-one.html_checkout:_yo_chk_info] Swag Labs (https://www.saucedemo.com/checkout-step-one.html)

Workflows observed:
  Step 1: AUTHENTICATE on authentication -> st_inventory.html_products_inv_c0 
          | outcome: Navigated to authenticated route: https://www.saucedemo.com/inventory.html
  Step 2: CLICK_CONTROL on [data-test="add-to-cart-sauce-labs-backpack"] -> st_inventory.html_products_inv_c1 
          | outcome: Clicked control '[data-test="add-to-cart-sauce-labs-backpack"]' (In-place state transition: cart badge 0 -> 1)
  Step 3: CLICK_CONTROL on [data-test="shopping-cart-link"] -> st_cart.html_your_cart_cart_c1 
          | outcome: Clicked control '[data-test="shopping-cart-link"]'
  Step 4: CLICK_CONTROL on [data-test="checkout"] -> st_checkout-step-one.html_checkout:_yo_chk_info 
          | outcome: Clicked control '[data-test="checkout"]'
  Step 5: CLICK_CONTROL on [data-test="shopping-cart-link"] -> st_cart.html_your_cart_cart_c1 
          | outcome: Clicked control '[data-test="shopping-cart-link"]'
```

### Verified Multi-Step Workflow Progression:
$$\text{Login} \longrightarrow \text{Inventory (Empty)} \xrightarrow{\text{Add to Cart}} \text{Inventory (Populated)} \xrightarrow{\text{View Cart}} \text{Cart View} \xrightarrow{\text{Checkout}} \text{Checkout Info}$$

---

## 5. Test Suite Verification

- **Full Suite Status**: **310 passed in 24.88s** with 0 failures and 0 regressions across the entire codebase (`pytest tests/ -q`).
- **Dedicated Investigation Unit Tests ([`tests/test_investigation_agent.py`](file:///d:/Hackathon/Code%20Apex/tests/test_investigation_agent.py))**:
  - `test_auth_boundary_detection_password_field`: **PASSED**
  - `test_auth_boundary_detection_public_page`: **PASSED**
  - `test_credential_discovery_saucedemo`: **PASSED**
  - `test_credential_discovery_missing`: **PASSED**
  - `test_frontier_population_and_prioritization`: **PASSED**
  - `test_action_safety_rejection`: **PASSED**
  - `test_report_builder_investigation_only`: **PASSED** (Strictly asserts that `test_cases`, `bug_reports`, and `execution_results` are absent).
- **Post-Upgrade Generalization & Isolation Suite ([`tests/test_investigation_generalization.py`](file:///d:/Hackathon/Code%20Apex/tests/test_investigation_generalization.py))**:
  - `test_state_identity_same_url_different_state`: **PASSED** (Distinguishes dynamic catalog item counters on the exact same URL).
  - `test_state_identity_stability_under_irrelevant_volatility`: **PASSED** (Strips volatile session tokens and timestamps from state ID).
  - `test_generic_frontier_prioritization_without_hardcoded_keywords`: **PASSED** (Prioritizes workflow tokens `proceed`/`continue`/`next` over static links).
  - `test_primary_path_vs_exploratory_branch_reporting`: **PASSED** (Classifies progression vs return branches).
  - `test_honest_budget_exhaustion_reporting`: **PASSED** (Explicitly distinguishes action budget exhaustion from complete frontier exhaustion).
  - `test_evidence_traceability_for_confirmed_transitions`: **PASSED** (Verifies 100% evidence ledger grounding for every confirmed transition).
  - `test_investigation_only_isolation_contract`: **PASSED** (Guarantees zero downstream test-case or bug-report generation).

---

## 6. Generalization Pass Architecture Upgrades

### 6.1 State Signature Generalization
State identity computation in [`app/investigation_agent/browser_session.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/browser_session.py) was enhanced to discard volatile noise while preserving semantically meaningful state transitions:
1. **Volatile Query Parameter Sanitization**: Query strings like `?ts=1700001&session_id=abc` are removed during route normalization.
2. **Dynamic Signal Aggregation**: Extracts active item counters, form progression markers (`step-1`, `payment-info`), active tabs (`role="tab"`), and grid catalog views dynamically.
3. **Deterministic Slug Synthesis**: Generates clean, human-readable state identifiers: `st_{normalized_route}_{primary_landmark}_{dynamic_signal_tag}`.

### 6.2 Frontier Heuristics Generalization
Frontier scoring in [`app/investigation_agent/frontier.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/frontier.py) was decoupled from website-specific keywords:
1. **Workflow Progression Tokens**: Elevated priority (`4.5`) for semantic terms like `checkout`, `proceed`, `continue`, `next_step`, `submit-order`.
2. **State Selection & Mutation**: Prioritized initial selection (`3.8`) for terms like `add`, `select`, `choose`, `buy`, transitioning smoothly to container progression once selections exist.
3. **Container Landmark Navigation**: Priority scaled dynamically based on container population (`4.0` when counter > 0 vs `1.5` when empty).
4. **Primary Workflow vs Return Branch Labeling**: Evaluated in [`app/investigation_agent/report_builder.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/report_builder.py), accurately cataloging whether a transition discovers an unseen state or returns to an earlier point in the graph.

---

## 7. How to Run the Updated System

### Start the Server
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Option A: Trigger Autonomous Investigation via API
```powershell
curl -X POST http://127.0.0.1:8000/api/investigate `
  -H "Content-Type: application/json" `
  -d '{"url": "https://www.saucedemo.com/", "description": "Sauce Labs E-Commerce Portal"}'
```

### Option B: Trigger via Web Dashboard
1. Navigate to **`http://127.0.0.1:8000/`**.
2. Enter the target URL: `https://www.saucedemo.com/`.
3. Click **🔍 Autonomous Website Investigation (Stateful + Auth-Aware)**.
4. The UI renders the live 10-section report including all discovered states, transition traces, and evidence references.
