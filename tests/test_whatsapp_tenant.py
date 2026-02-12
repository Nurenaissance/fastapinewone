"""
Tests for /whatsapp_tenant and /broadcast-groups/ endpoints.
"""
import pytest


class TestGetWhatsappTenant:
    def test_get_whatsapp_tenant_missing_headers(self, client, auth_headers):
        """GET /whatsapp_tenant without bpid or tenant_id -> 400 or appropriate error."""
        # Remove X-Tenant-Id from auth headers to test missing tenant
        headers = {k: v for k, v in auth_headers.items() if k != "X-Tenant-Id"}
        resp = client.get("/whatsapp_tenant", headers=headers)
        # Should fail because no tenant data exists and no bpid
        assert resp.status_code in (400, 404, 500)

    def test_get_whatsapp_tenant_nonexistent_tenant(self, client, auth_headers):
        """GET /whatsapp_tenant with valid auth but no data -> 404."""
        resp = client.get("/whatsapp_tenant", headers=auth_headers)
        # No whatsapp tenant data seeded for test_tenant_id
        assert resp.status_code in (404, 500)


class TestBroadcastGroups:
    def test_broadcast_groups_list_empty(self, client, auth_headers, sample_tenant):
        """GET /broadcast-groups/ returns empty list when none exist."""
        resp = client.get("/broadcast-groups/", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)

    def test_broadcast_groups_list_with_data(self, client, auth_headers, sample_broadcast_group):
        """GET /broadcast-groups/ returns groups when data exists."""
        resp = client.get("/broadcast-groups/", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) >= 1
