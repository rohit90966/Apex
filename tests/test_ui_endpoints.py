import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_serve_dashboard():
    response = client.get("/")
    assert response.status_code == 200
    assert "VerityQA" in response.text
    assert "Autonomous QA Platform" in response.text
    assert "Investigation Setup" in response.text
    assert "Generated Tests" in response.text
    assert "Test Execution" in response.text
    assert "Live Activity Stream" in response.text
    assert "Exports & Benchmarks" in response.text

def test_list_reports():
    response = client.get("/api/reports")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    # verify saucedemo handoff contract exists in reports
    filenames = [item["filename"] for item in data]
    assert any("saucedemo_handoff_contract_v2.json" in fn for fn in filenames)

def test_get_report_content():
    response = client.get("/api/reports/saucedemo_handoff_contract_v2.json")
    assert response.status_code == 200
    data = response.json()
    assert "investigated_routes" in data or "target_url" in data

def test_get_latest_report():
    response = client.get("/api/reports/latest")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)

def test_execute_empty_test_suite():
    response = client.post("/api/tests/execute", json={
        "target_url": "https://www.saucedemo.com/",
        "test_cases": [],
        "verify_bugs": False
    })
    assert response.status_code == 200
    data = response.json()
    assert data["total_cases"] == 0
    assert data["executed_count"] == 0
    assert data["passed_count"] == 0



from unittest.mock import patch, MagicMock
from app.execution.runner import ExecutionResult, ExecutionStatus, ExecutionAssertion, AssertionType

def test_execute_test_suite_with_runner_mock():
    mock_res = ExecutionResult(
        test_id="TC-001",
        test_name="Verify Login Page",
        status=ExecutionStatus.PASSED,
        total_steps=1,
        steps_completed=1,
        step_results=[],
        assertions=[ExecutionAssertion(assertion_type=AssertionType.ELEMENT_VISIBLE, description="Login button is visible", selector="#login-button", evidence_id="ev_001", passed=True)],
        duration_ms=120.0
    )
    with patch("app.execution.runner.QAPlanRunner.run_all", return_value=[mock_res]):
        response = client.post("/api/tests/execute", json={
            "target_url": "https://www.saucedemo.com/",
            "test_cases": [
                {
                    "test_id": "TC-001",
                    "name": "Verify Login Page",
                    "objective": "Ensure login page renders properly",
                    "page_category": "LOGIN",
                    "relevant_elements": ["#login-button"],
                    "action_description": "Navigate to login page and check button",
                    "expected_outcome": "Login button is visible",
                    "expected_behavior_source": "INFERRED",
                    "evidence_to_collect": ["login_btn"],
                    "priority": "HIGH",
                    "preconditions": [],
                    "selection_reason": "Smoke check",
                    "confidence": 0.95
                }
            ],
            "verify_bugs": False
        })
        assert response.status_code == 200
        data = response.json()
        assert data["total_cases"] == 1
        assert data["executed_count"] == 1
        assert data["passed_count"] == 1
        assert data["assertion_validity_rate"] == 1.0
        assert len(data["results"]) == 1
        assert data["results"][0]["test_id"] == "TC-001"
