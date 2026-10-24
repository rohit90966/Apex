import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "VerityQA"
    assert "ollama_configured_model" in data


def test_serve_dashboard():
    response = client.get("/")
    assert response.status_code == 200
    assert "VerityQA" in response.text


def test_artifact_traversal_prevention():
    response = client.get("/artifacts/..%2Fsecret.txt")
    assert response.status_code in (400, 404)


def test_artifact_not_found():
    response = client.get("/artifacts/non-existent-artifact.png")
    assert response.status_code == 404
