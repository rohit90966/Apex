"""
VerityQA Controlled Intelligence Benchmark Application
======================================================
Ground-truth target application with known deterministic states, transitions,
multi-step workflows, benign telemetry warnings, and a seeded behavioral defect.

Ground-Truth Manifest:
--------------------------------------------------------------------------------
States (Reachable):
  1. ST_LOGIN:              /login (Entry Login form with validation)
  2. ST_LOGIN_ERROR:        /login with visible alert 'Username is required'
  3. ST_PRODUCTS:           /products (Catalog, 3 items, sort select, cart=0)
  4. ST_PRODUCTS_SORT_ZA:   /products with sort='za' (DOM reordered)
  5. ST_PRODUCTS_DRAWER:    /products with #menu-drawer visible (.menu-open)
  6. ST_PRODUCTS_CART_1:    /products with badge counter = 1
  7. ST_PRODUCT_DETAIL:     /products/1 (Alpha Device detail view)
  8. ST_CART_EMPTY:         /cart with 0 items
  9. ST_CART_1:             /cart with 1 item (Alpha Device, badge=1)
  10. ST_CHECKOUT:          /checkout (Multi-step shipping details form)
  11. ST_CONFIRMATION:      /order-confirmation (Success confirmation)

Seeded Behavioral Defect:
  BUG-BENCH-01: On /cart, clicking '#calculate-tax-btn' renders an unhandled text
                anomaly: 'Tax: $NaN | Total: $NaN' in DOM element #tax-display.
  BUG-BENCH-02: On /cart, clicking '#remove-item-1' removes item row from DOM,
                but #cart-badge remains stuck at '1' (counter synchronization bug).

Benign Signals (Must NOT be reported as confirmed defects):
  - console.warn: "Analytics: tracking pixel ping delayed (non-critical)"
  - HTTP 302: Standard redirection from / to /login or /products.
  - HTTP 404 on /favicon.ico: Benign browser asset lookup.
--------------------------------------------------------------------------------
"""

import os
from typing import List, Dict, Optional
from fastapi import FastAPI, Request, Response, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(title="VerityQA Intelligence Benchmark Target")

# In-memory session cart storage keyed by session cookie
_CARTS: Dict[str, List[int]] = {}

PRODUCTS = [
    {"id": 1, "name": "Alpha Device", "price": 15.00, "desc": "High-precision core automation unit"},
    {"id": 2, "name": "Beta Machine", "price": 35.00, "desc": "Industrial telemetry analysis station"},
    {"id": 3, "name": "Gamma System", "price": 95.00, "desc": "Enterprise distributed control matrix"},
]


def _get_cart(request: Request) -> List[int]:
    sess_id = request.cookies.get("bench_session", "default_user")
    return _CARTS.setdefault(sess_id, [])


def _html_layout(title: str, content: str, cart_count: int = 0, menu_open: bool = False) -> str:
    menu_class = "menu-open" if menu_open else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title} — Verity Benchmark Portal</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; background: #f4f6f8; }}
    header {{ background: #1a202c; color: white; padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; }}
    header a {{ color: white; text-decoration: none; font-weight: 600; margin-left: 16px; }}
    .badge {{ background: #e53e3e; color: white; border-radius: 9999px; padding: 2px 8px; font-size: 12px; font-weight: bold; margin-left: 4px; }}
    .container {{ max-width: 960px; margin: 24px auto; background: white; padding: 24px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
    .alert-danger {{ background: #fed7d7; color: #9b2c2c; border: 1px solid #feb2b2; padding: 12px; border-radius: 4px; margin-bottom: 16px; font-weight: 500; }}
    .product-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; margin-top: 16px; }}
    .product-card {{ border: 1px solid #e2e8f0; border-radius: 6px; padding: 16px; background: #fff; }}
    .product-card h3 {{ margin-top: 0; }}
    .product-card a {{ color: #2b6cb0; text-decoration: none; font-weight: 600; }}
    .btn {{ background: #3182ce; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 14px; text-decoration: none; display: inline-block; }}
    .btn:hover {{ background: #2b6cb0; }}
    .btn-secondary {{ background: #718096; }}
    .btn-secondary:hover {{ background: #4a5568; }}
    .btn-danger {{ background: #e53e3e; }}
    .btn-danger:hover {{ background: #c53030; }}
    .drawer {{ display: none; background: #edf2f7; border: 1px dashed #cbd5e0; padding: 12px; border-radius: 4px; margin-bottom: 16px; }}
    .drawer.menu-open {{ display: block; }}
    form .form-group {{ margin-bottom: 14px; }}
    form label {{ display: block; margin-bottom: 4px; font-weight: 600; font-size: 14px; }}
    form input, form select {{ width: 100%; box-sizing: border-box; padding: 8px 12px; border: 1px solid #cbd5e0; border-radius: 4px; font-size: 14px; }}
  </style>
  <script>
    // Injected benign telemetry warning (non-critical performance metric)
    console.warn("Analytics: tracking pixel ping delayed (non-critical)");
  </script>
</head>
<body>
  <header>
    <div>
      <strong>Verity Benchmark System</strong>
    </div>
    <nav>
      <a href="/products">Products</a>
      <a id="cart-link" data-test="shopping-cart-link" href="/cart">
        Cart <span id="cart-badge" data-test="cart-badge" class="badge">{cart_count}</span>
      </a>
      <a href="/logout">Logout</a>
    </nav>
  </header>
  <div class="container">
    {content}
  </div>
</body>
</html>"""


@app.get("/")
async def root():
    return RedirectResponse(url="/login", status_code=302)


@app.get("/favicon.ico")
async def favicon():
    return Response(content="Not Found", status_code=404)


# ------------------------------------------------------------------------------
# 1. Authentication Gate
# ------------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_get(error: Optional[str] = None):
    err_html = f'<div class="alert-danger" data-test="error">{error}</div>' if error else ""
    body = f"""
    <h2>System Access Portal</h2>
    <p>Please enter test credentials to access the product catalog.</p>
    {err_html}
    <form id="loginForm" method="POST" action="/login">
      <div class="form-group">
        <label for="username">Username</label>
        <input id="username" name="username" type="text" placeholder="benchmark_user" />
      </div>
      <div class="form-group">
        <label for="password">Password</label>
        <input id="password" name="password" type="password" placeholder="••••••••" />
      </div>
      <button id="login-button" class="btn" type="submit">Sign In</button>
    </form>
    """
    return HTMLResponse(_html_layout("Authentication", body))


@app.post("/login")
async def login_post(
    response: Response,
    username: str = Form(default=""),
    password: str = Form(default=""),
):
    # Validation behavior
    if not username.strip():
        return RedirectResponse(url="/login?error=Username+is+required", status_code=303)
    if not password.strip():
        return RedirectResponse(url="/login?error=Password+is+required", status_code=303)

    if username == "benchmark_user" and password == "secret_benchmark":
        resp = RedirectResponse(url="/products", status_code=303)
        resp.set_cookie("bench_session", "session_benchmark_user", httponly=True)
        return resp

    return RedirectResponse(url="/login?error=Invalid+credentials+provided", status_code=303)


@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("bench_session")
    return resp


# ------------------------------------------------------------------------------
# 2. Product Catalog, Sorting & In-Page Menu Drawer
# ------------------------------------------------------------------------------

@app.get("/products", response_class=HTMLResponse)
async def products_get(
    request: Request,
    sort: str = Query(default="az"),
    menu: str = Query(default="closed"),
):
    cart = _get_cart(request)
    cart_count = len(cart)
    is_menu_open = (menu == "open")

    items = list(PRODUCTS)
    if sort == "za":
        items.sort(key=lambda x: x["name"], reverse=True)
    elif sort == "lohi":
        items.sort(key=lambda x: x["price"])
    elif sort == "hilo":
        items.sort(key=lambda x: x["price"], reverse=True)
    else:
        items.sort(key=lambda x: x["name"])

    # Sorting select control options
    opts = [
        ("az", "Name: A to Z"),
        ("za", "Name: Z to A"),
        ("lohi", "Price: Low to High"),
        ("hilo", "Price: High to Low"),
    ]
    options_html = "".join(
        f'<option value="{val}"{" selected" if val == sort else ""}>{label}</option>'
        for val, label in opts
    )

    # In-page Drawer Toggle logic
    next_menu = "closed" if is_menu_open else "open"
    drawer_html = f"""
    <div id="filter-drawer" class="drawer {'menu-open' if is_menu_open else ''}">
      <h4>Filter & Telemetry Drawer (In-Page Modifier)</h4>
      <p>Active Category: Automation Hardware | Warehouse: Zone-1</p>
      <a href="/products?sort={sort}&menu=closed" class="btn btn-secondary">Close Drawer</a>
    </div>
    """

    products_html = ""
    for item in items:
        in_cart = item["id"] in cart
        btn_action = f"""
        <form method="POST" action="/cart/add" style="display:inline;">
          <input type="hidden" name="product_id" value="{item['id']}" />
          <button id="add-to-cart-{item['id']}" data-test="add-to-cart-{item['id']}" class="btn" type="submit">Add to Cart</button>
        </form>
        """
        products_html += f"""
        <div class="product-card" id="product-card-{item['id']}">
          <h3><a id="item-{item['id']}-title" href="/products/{item['id']}">{item['name']}</a></h3>
          <p>{item['desc']}</p>
          <div style="display:flex; justify-content:space-between; align-items:center; margin-top:12px;">
            <strong>${item['price']:.2f}</strong>
            {btn_action}
          </div>
        </div>
        """

    content = f"""
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
      <h2>Products Catalog</h2>
      <div style="display:flex; align-items:center; gap:8px;">
        <label for="product-sort">Sort By:</label>
        <select id="product-sort" data-test="product-sort" onchange="window.location.href='/products?sort=' + this.value + '&menu={menu}';">
          {options_html}
        </select>
        <a id="menu-drawer-toggle" data-test="menu-drawer-toggle" href="/products?sort={sort}&menu={next_menu}" class="btn btn-secondary">
          {'Hide Drawer' if is_menu_open else 'Open Drawer'}
        </a>
      </div>
    </div>
    {drawer_html}
    <div class="product-grid">
      {products_html}
    </div>
    """
    return HTMLResponse(_html_layout("Products", content, cart_count=cart_count, menu_open=is_menu_open))


# ------------------------------------------------------------------------------
# 3. Product Details Route
# ------------------------------------------------------------------------------

@app.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, product_id: int):
    cart = _get_cart(request)
    cart_count = len(cart)
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if not product:
        return HTMLResponse(_html_layout("Product Not Found", "<h2>Product Not Found</h2><a href='/products'>Back to Catalog</a>", cart_count))

    content = f"""
    <div style="margin-bottom:16px;">
      <a id="back-to-products" href="/products" class="btn btn-secondary">&larr; Back to Catalog</a>
    </div>
    <h2>{product['name']} Details</h2>
    <p style="font-size:16px; color:#4a5568;">{product['desc']}</p>
    <div style="font-size:24px; font-weight:bold; color:#2d3748; margin: 16px 0;">${product['price']:.2f}</div>
    <form method="POST" action="/cart/add">
      <input type="hidden" name="product_id" value="{product['id']}" />
      <button id="detail-add-to-cart" class="btn" type="submit">Add to Cart</button>
    </form>
    """
    return HTMLResponse(_html_layout(f"{product['name']} Detail", content, cart_count=cart_count))


# ------------------------------------------------------------------------------
# 4. Shopping Cart & Seeded Behavioral Defects
# ------------------------------------------------------------------------------

@app.post("/cart/add")
async def cart_add(request: Request, product_id: int = Form(...)):
    cart = _get_cart(request)
    cart.append(product_id)
    return RedirectResponse(url="/cart", status_code=303)


@app.get("/cart", response_class=HTMLResponse)
async def cart_get(request: Request, show_tax: bool = False):
    cart = _get_cart(request)
    cart_count = len(cart)

    items = [p for p in PRODUCTS if p["id"] in cart]
    subtotal = sum(p["price"] for p in items)

    # Seeded Defect #1: When calculate tax button is pressed, returns text anomaly 'Tax: $NaN | Total: $NaN'
    # Controlled Mutation Support: Allows toggling between defective (NaN anomaly) and corrected behavior
    tax_display = ""
    if show_tax:
        is_corrected = (
            getattr(request.app.state, "tax_defect_corrected", False)
            or os.environ.get("VERITYQA_BENCHMARK_CORRECT_TAX") == "1"
            or os.environ.get("VERITYQA_FIXTURE_MODE", "").strip().lower() == "baseline"
        )
        if is_corrected:
            tax = round(subtotal * 0.10, 2)
            total = subtotal + tax
            tax_display = f'<div id="tax-display" class="alert-info" data-test="tax-display">Tax: ${tax:.2f} | Total: ${total:.2f}</div>'
        else:
            tax_display = '<div id="tax-display" class="alert-danger" data-test="tax-display">Tax: $NaN | Total: $NaN</div>'

    items_html = ""
    for item in items:
        items_html += f"""
        <div id="cart-item-{item['id']}" style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #e2e8f0; padding:12px 0;">
          <div>
            <strong>{item['name']}</strong>
            <div style="color:#718096;">${item['price']:.2f}</div>
          </div>
          <form method="POST" action="/cart/remove">
            <input type="hidden" name="product_id" value="{item['id']}" />
            <button id="remove-item-{item['id']}" data-test="remove-item-{item['id']}" class="btn btn-danger" type="submit">Remove</button>
          </form>
        </div>
        """

    content = f"""
    <h2>Shopping Cart</h2>
    <div id="cart-list">
      {items_html if items else '<p>Your cart is empty.</p>'}
    </div>
    <div style="margin-top:20px; padding-top:16px; border-top:2px solid #e2e8f0;">
      <div style="font-size:18px; font-weight:bold; margin-bottom:12px;">Subtotal: ${subtotal:.2f}</div>
      {tax_display}
      <div style="display:flex; gap:12px; margin-top:16px;">
        <a id="calculate-tax-btn" data-test="calculate-tax-btn" href="/cart?show_tax=true" class="btn btn-secondary">Calculate Tax</a>
        <a id="checkout-button" data-test="checkout-button" href="/checkout" class="btn">Proceed to Checkout</a>
        <a href="/products" class="btn btn-secondary">Continue Shopping</a>
      </div>
    </div>
    """
    return HTMLResponse(_html_layout("Your Cart", content, cart_count=cart_count))


@app.post("/cart/remove")
async def cart_remove(request: Request, product_id: int = Form(...)):
    cart = _get_cart(request)
    if product_id in cart:
        cart.remove(product_id)
    # Redirect back to cart
    return RedirectResponse(url="/cart", status_code=303)


# ------------------------------------------------------------------------------
# 5. Multi-Step Checkout Workflow
# ------------------------------------------------------------------------------

@app.get("/checkout", response_class=HTMLResponse)
async def checkout_step1(request: Request, error: Optional[str] = None):
    cart = _get_cart(request)
    err_html = f'<div id="checkout-error" class="alert-danger" data-test="error">{error}</div>' if error else ""
    content = f"""
    <h2>Checkout: Shipping Details</h2>
    {err_html}
    <form id="checkoutForm" method="POST" action="/checkout">
      <div class="form-group">
        <label for="first-name">First Name</label>
        <input id="first-name" name="first_name" type="text" placeholder="John" />
      </div>
      <div class="form-group">
        <label for="last-name">Last Name</label>
        <input id="last-name" name="last_name" type="text" placeholder="Doe" />
      </div>
      <div class="form-group">
        <label for="postal-code">Postal Code</label>
        <input id="postal-code" name="postal_code" type="text" placeholder="10001" />
      </div>
      <button id="continue-checkout-btn" data-test="continue-checkout-btn" class="btn" type="submit">Continue to Review</button>
      <a href="/cart" class="btn btn-secondary">Back to Cart</a>
    </form>
    """
    return HTMLResponse(_html_layout("Checkout", content, cart_count=len(cart)))


@app.post("/checkout")
async def checkout_step1_post(
    request: Request,
    first_name: str = Form(default=""),
    last_name: str = Form(default=""),
    postal_code: str = Form(default=""),
):
    # Form validation behavior
    if not first_name.strip():
        return RedirectResponse(url="/checkout?error=First+name+is+required", status_code=303)
    if not last_name.strip():
        return RedirectResponse(url="/checkout?error=Last+name+is+required", status_code=303)
    if not postal_code.strip():
        return RedirectResponse(url="/checkout?error=Postal+code+is+required", status_code=303)

    return RedirectResponse(url="/checkout/review", status_code=303)


@app.get("/checkout/review", response_class=HTMLResponse)
async def checkout_review(request: Request):
    cart = _get_cart(request)
    items = [p for p in PRODUCTS if p["id"] in cart]
    subtotal = sum(p["price"] for p in items)
    content = f"""
    <h2>Checkout: Order Review</h2>
    <p>Please confirm your order for {len(items)} items. Total: ${subtotal:.2f}</p>
    <form method="POST" action="/checkout/confirm">
      <button id="finish-order-btn" data-test="finish-order-btn" class="btn" type="submit">Place Order</button>
      <a href="/checkout" class="btn btn-secondary">Edit Shipping</a>
    </form>
    """
    return HTMLResponse(_html_layout("Review Order", content, cart_count=len(cart)))


@app.post("/checkout/confirm")
async def checkout_confirm(request: Request):
    cart = _get_cart(request)
    cart.clear()
    return RedirectResponse(url="/order-confirmation", status_code=303)


@app.get("/order-confirmation", response_class=HTMLResponse)
async def order_confirmation():
    content = """
    <div style="text-align:center; padding:32px 0;">
      <h2 style="color:#2b6cb0;">Order Placed Successfully!</h2>
      <p>Thank you for your order. Your benchmark transaction has been recorded.</p>
      <a id="back-home-btn" href="/products" class="btn" style="margin-top:16px;">Continue Shopping</a>
    </div>
    """
    return HTMLResponse(_html_layout("Order Confirmed", content, cart_count=0))
