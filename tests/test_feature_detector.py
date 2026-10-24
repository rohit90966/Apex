import pytest
from app.investigation.models import PageEvidence
from app.investigation.feature_detector import FeatureDetector
from app.schemas import FormMetadata, FormFieldMetadata, PageCategory


def test_detect_login_feature():
    page = PageEvidence(
        page_id="p-1",
        url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="User Sign In",
        buttons=["Log In"],
        forms=[
            FormMetadata(
                form_id="login-form",
                fields=[
                    FormFieldMetadata(name="username", input_type="text"),
                    FormFieldMetadata(name="password", input_type="password"),
                ],
            )
        ],
    )
    features = FeatureDetector.detect_features(page)
    feat_cats = [f.category for f in features]
    assert "auth" in feat_cats
    assert any("Authentication" in f.name for f in features)


def test_detect_ecommerce_feature():
    page = PageEvidence(
        page_id="p-2",
        url="http://127.0.0.1:8001/cart",
        final_url="http://127.0.0.1:8001/cart",
        page_title="Your Shopping Cart",
        headings=["Order Summary", "Items in Cart"],
        buttons=["Checkout", "Apply Coupon"],
        visible_text_snippet="Subtotal $99.00 Taxes $5.00 Total $104.00",
    )
    features = FeatureDetector.detect_features(page)
    feat_cats = [f.category for f in features]
    assert "ecommerce" in feat_cats


def test_detect_booking_and_tables_features():
    page = PageEvidence(
        page_id="p-3",
        url="http://127.0.0.1:8001/reservations",
        final_url="http://127.0.0.1:8001/reservations",
        page_title="Hotel Room Reservation",
        headings=["Book Your Stay"],
        visible_text_snippet="Select check-in and check-out dates for your room reservation.",
        buttons=["Book Now"],
        has_tables=True,
        has_pagination=True,
    )
    features = FeatureDetector.detect_features(page)
    feat_cats = [f.category for f in features]
    assert "booking" in feat_cats
    assert "table_dashboard" in feat_cats
