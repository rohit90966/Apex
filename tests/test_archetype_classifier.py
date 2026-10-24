"""
Tests for VerityQA Website Archetype Classification & Application Intelligence Model.
"""

import pytest
from app.intelligence.archetypes import (
    WebsiteArchetype,
    ApplicationCapability,
    ArchetypeClassifier,
    ApplicationIntelligenceModel,
)


def test_ecommerce_archetype_classification():
    classifier = ArchetypeClassifier()
    signals = {
        "target_url": "https://www.saucedemo.com/",
        "urls": [
            "https://www.saucedemo.com/inventory.html",
            "https://www.saucedemo.com/cart.html",
            "https://www.saucedemo.com/checkout-step-one.html",
            "https://www.saucedemo.com/checkout-step-two.html",
            "https://www.saucedemo.com/checkout-complete.html",
        ],
        "controls": [
            {"selector": "#user-name", "text": "Username"},
            {"selector": "#password", "text": "Password"},
            {"selector": "#login-button", "text": "Login"},
            {"selector": "#add-to-cart-sauce-labs-backpack", "text": "Add to cart"},
            {"selector": "[data-test='shopping-cart-link']", "text": "Cart"},
            {"selector": "[data-test='product-sort-container']", "text": "Sort"},
            {"selector": "#checkout", "text": "Checkout"},
            {"selector": "#finish", "text": "Finish"},
        ],
        "headings": ["Products", "Your Cart", "Checkout: Your Information", "Checkout: Overview"],
        "workflow_titles": ["Purchase Flow", "Add to Cart"],
        "invariants": ["Subtotal equals sum of product prices"],
    }

    model = classifier.classify_from_signals(**signals)

    assert model.primary_archetype == WebsiteArchetype.ECOMMERCE
    assert model.confidence_scores[WebsiteArchetype.ECOMMERCE] > 0.7
    assert ApplicationCapability.CATALOG_BROWSING in model.capabilities
    assert ApplicationCapability.CART_MANAGEMENT in model.capabilities
    assert ApplicationCapability.CHECKOUT_FLOW in model.capabilities
    assert ApplicationCapability.USER_AUTHENTICATION in model.capabilities
    assert ApplicationCapability.PAGINATION_SORTING in model.capabilities
    assert "ShoppingCart" in model.entities
    assert "ProductItem" in model.entities


def test_banking_archetype_classification():
    classifier = ArchetypeClassifier()
    signals = {
        "target_url": "https://bank.example.com",
        "urls": [
            "https://bank.example.com/accounts",
            "https://bank.example.com/transfers",
            "https://bank.example.com/statements",
        ],
        "controls": [
            {"selector": "#transfer-fund", "text": "Transfer Funds"},
            {"selector": "#account-balance", "text": "Account Balance"},
            {"selector": "#routing-number", "text": "Routing Number"},
        ],
        "headings": ["Account Summary", "Wire Transfer"],
    }

    model = classifier.classify_from_signals(**signals)

    assert model.primary_archetype == WebsiteArchetype.BANKING_FINTECH
    assert ApplicationCapability.FINANCIAL_TRANSACTION in model.capabilities
    assert "BankAccount" in model.entities


def test_saas_crud_archetype_classification():
    classifier = ArchetypeClassifier()
    signals = {
        "target_url": "https://app.workspaces.io",
        "urls": [
            "https://app.workspaces.io/dashboard",
            "https://app.workspaces.io/projects",
            "https://app.workspaces.io/settings/team",
        ],
        "controls": [
            {"selector": "#create-btn", "text": "New Project"},
            {"selector": "#edit-btn", "text": "Edit Record"},
            {"selector": "#delete-btn", "text": "Delete Project"},
        ],
        "headings": ["Dashboard", "Team Settings"],
    }

    model = classifier.classify_from_signals(**signals)

    assert model.primary_archetype == WebsiteArchetype.SAAS_CRUD
    assert ApplicationCapability.CRUD_OPERATIONS in model.capabilities
    assert "Workspace" in model.entities


def test_generic_web_classification():
    classifier = ArchetypeClassifier()
    signals = {
        "target_url": "https://static-blog.org",
        "urls": ["https://static-blog.org/about", "https://static-blog.org/contact"],
        "controls": [{"selector": "a.nav-link", "text": "About"}],
        "headings": ["Welcome to My Blog"],
    }

    model = classifier.classify_from_signals(**signals)

    assert model.primary_archetype == WebsiteArchetype.GENERIC_WEB
