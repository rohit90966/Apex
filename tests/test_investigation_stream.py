"""
Tests for the Real-Time SSE Investigation Streaming Endpoint.
"""

import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from app.main import app
from app.investigation_agent.models import (
    InvestigationReport,
    ReportOverview,
    InvestigationStatus,
    ReportAuthentication,
)

client = TestClient(app)


def test_investigate_stream_missing_url():
    response = client.get("/api/investigate/stream")
    assert response.status_code == 400
    assert "Missing required 'url' parameter" in response.json()["detail"]


def test_investigate_stream_safety_violation():
    response = client.get("/api/investigate/stream?url=https://malicious-disallowed-site.com")
    assert response.status_code == 400
    assert "Safety Policy Rejection" in response.json()["detail"]


def test_investigate_stream_sse_response_headers():
    dummy_report = InvestigationReport(
        overview=ReportOverview(
            target_url="http://127.0.0.1:8001/login",
            investigation_id="inv-stream-test",
            start_time="2026-10-21T12:00:00Z",
            end_time="2026-10-21T12:00:10Z",
            status=InvestigationStatus.COMPLETED,
            pages_discovered=1,
            meaningful_states_discovered=1,
            stopping_reason="Complete",
        ),
        application_understanding="Test understanding",
        user_provided_context_distinction="Distinct",
        authentication_access=ReportAuthentication(
            boundary_observed=False,
            credentials_available=False,
            authentication_attempted=False,
            authentication_verified=False,
        ),
    )

    with patch("app.investigation_agent.run_stateful_investigation", new=AsyncMock(return_value=dummy_report)):
        with client.stream("GET", "/api/investigate/stream?url=http://127.0.0.1:8001/login") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            lines = []
            for line in response.iter_lines():
                if line:
                    lines.append(line)
                    if "COMPLETE" in line:
                        break
            assert any("COMPLETE" in l for l in lines)
