import pytest
from app.schemas import InspectionResult, FormMetadata, FormFieldMetadata, PageCategory
from app.page_classifier import DeterministicClassifier


def test_classify_login_page():
    inspection = InspectionResult(
        inspection_id="test-1",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="User Sign In - Apex Portal",
        headings=["Sign In to Apex"],
        buttons=["Log In"],
        forms=[
            FormMetadata(
                form_id="login-form",
                fields=[
                    FormFieldMetadata(name="username", input_type="text", label="Username"),
                    FormFieldMetadata(name="password", input_type="password", label="Password"),
                ],
            )
        ],
    )
    result = DeterministicClassifier.classify(inspection)
    assert result.page_category == PageCategory.LOGIN
    assert result.confidence >= 0.70
    assert len(result.evidence) > 0


def test_classify_registration_page():
    inspection = InspectionResult(
        inspection_id="test-2",
        target_url="http://127.0.0.1:8001/registration",
        final_url="http://127.0.0.1:8001/registration",
        page_title="Create an Account",
        headings=["User Registration"],
        buttons=["Register"],
        forms=[
            FormMetadata(
                form_id="reg-form",
                fields=[
                    FormFieldMetadata(name="email", input_type="email", label="Email"),
                    FormFieldMetadata(name="password", input_type="password", label="Password"),
                    FormFieldMetadata(name="confirm_password", input_type="password", label="Confirm Password"),
                ],
            )
        ],
    )
    result = DeterministicClassifier.classify(inspection)
    assert result.page_category == PageCategory.REGISTRATION
    assert result.confidence >= 0.75


def test_classify_search_page():
    inspection = InspectionResult(
        inspection_id="test-3",
        target_url="http://127.0.0.1:8001/search",
        final_url="http://127.0.0.1:8001/search",
        page_title="Catalog Search",
        headings=["Search Products"],
        buttons=["Search"],
        forms=[
            FormMetadata(
                form_id="search-form",
                fields=[
                    FormFieldMetadata(name="q", input_type="search", label="Search items"),
                ],
            )
        ],
    )
    result = DeterministicClassifier.classify(inspection)
    assert result.page_category == PageCategory.SEARCH
    assert result.confidence >= 0.70


def test_classify_unknown_or_general():
    inspection = InspectionResult(
        inspection_id="test-4",
        target_url="http://127.0.0.1:8001/blank",
        final_url="http://127.0.0.1:8001/blank",
        page_title="",
        headings=[],
        buttons=[],
        links=[],
        forms=[],
    )
    result = DeterministicClassifier.classify(inspection)
    assert result.page_category in (PageCategory.UNKNOWN, PageCategory.GENERAL)
    assert result.confidence <= 0.60
