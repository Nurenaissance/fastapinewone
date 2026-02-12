"""
Tests for FastAPI health/root endpoints (public, no auth required).
"""
import pytest


class TestHealthEndpoints:
    def test_health_endpoint(self, client):
        """GET /health returns 200 with status info."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert "healthy" in body["status"].lower()

    def test_root_endpoint(self, client):
        """GET / returns API info."""
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert "message" in body
        assert "running" in body["message"].lower()

    def test_docs_accessible(self, client):
        """GET /docs returns 200 (Swagger UI)."""
        resp = client.get("/docs")
        assert resp.status_code == 200
