"""
Tests for /notifications endpoints.
"""
import pytest
from datetime import datetime


class TestNotifications:
    def test_create_notification(self, client, auth_headers, sample_tenant):
        """POST /notifications creates a notification."""
        resp = client.post(
            "/notifications",
            json={
                "content": "New message from 91987654321",
                "created_on": datetime.utcnow().isoformat(),
            },
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201)
        body = resp.json()
        assert "id" in body or "notification" in str(body).lower() or "success" in str(body).lower()

    def test_list_notifications(self, client, auth_headers, sample_tenant):
        """GET /notifications returns list."""
        resp = client.get("/notifications", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, (list, dict))

    def test_notification_stats(self, client, auth_headers, sample_tenant):
        """GET /notifications/stats returns statistics."""
        resp = client.get("/notifications/stats", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        # Stats endpoint should return count-like data
        assert isinstance(body, dict)
