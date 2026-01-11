"""
Sample tests for Contacts API
Demonstrates testing patterns for FastAPI endpoints
"""
import pytest
from contacts.models import Contact


class TestContactsAPI:
    """Test suite for contact management endpoints"""

    def test_get_contacts_unauthorized(self, client):
        """Test that contacts endpoint requires authentication"""
        response = client.get("/contacts/1")
        assert response.status_code == 401

    def test_get_contacts_with_auth(self, client, auth_headers, sample_contact):
        """Test retrieving contacts with valid authentication"""
        response = client.get(
            f"/contacts/filter/1",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "contacts" in data

    def test_create_contact(self, client, auth_headers, db_session):
        """Test creating a new contact"""
        contact_data = {
            "name": "New Contact",
            "phone": "+19876543210",
            "email": "test@example.com",
            "customField": {
                "company": "Test Corp",
                "role": "Manager"
            }
        }

        response = client.post(
            "/contacts",
            json=contact_data,
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Contact"
        assert data["phone"] == "+19876543210"

        # Verify in database
        db_contact = db_session.query(Contact).filter(
            Contact.phone == "+19876543210"
        ).first()
        assert db_contact is not None
        assert db_contact.name == "New Contact"

    def test_update_contact(self, client, auth_headers, sample_contact, db_session):
        """Test updating an existing contact"""
        update_data = {
            "name": "Updated Name",
            "email": "updated@example.com"
        }

        response = client.put(
            f"/contacts/{sample_contact.id}",
            json=update_data,
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"

        # Verify in database
        db_session.refresh(sample_contact)
        assert sample_contact.name == "Updated Name"

    def test_delete_contact(self, client, auth_headers, sample_contact, db_session):
        """Test deleting a contact"""
        response = client.delete(
            f"/contacts/{sample_contact.id}",
            headers=auth_headers
        )

        assert response.status_code == 200

        # Verify deleted from database
        deleted_contact = db_session.query(Contact).filter(
            Contact.id == sample_contact.id
        ).first()
        assert deleted_contact is None

    def test_filter_contacts_by_status(self, client, auth_headers, db_session):
        """Test filtering contacts by status"""
        # Create contacts with different statuses
        active_contact = Contact(
            name="Active Contact",
            phone="+11111111111",
            tenant_id="test_tenant_id",
            status="active"
        )
        inactive_contact = Contact(
            name="Inactive Contact",
            phone="+12222222222",
            tenant_id="test_tenant_id",
            status="inactive"
        )
        db_session.add_all([active_contact, inactive_contact])
        db_session.commit()

        # Filter by status
        response = client.get(
            "/contacts/filter/1?status=active",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()

        # All contacts should have status "active"
        for contact in data["contacts"]:
            assert contact["status"] == "active"

    @pytest.mark.slow
    def test_bulk_contact_import(self, client, auth_headers, db_session):
        """Test importing multiple contacts at once"""
        contacts_data = [
            {"name": f"Contact {i}", "phone": f"+1555000{i:04d}"}
            for i in range(100)
        ]

        response = client.post(
            "/contacts/bulk",
            json={"contacts": contacts_data},
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["created_count"] == 100

        # Verify in database
        count = db_session.query(Contact).filter(
            Contact.tenant_id == "test_tenant_id"
        ).count()
        assert count >= 100


class TestContactValidation:
    """Test suite for contact data validation"""

    def test_invalid_phone_number(self, client, auth_headers):
        """Test that invalid phone numbers are rejected"""
        contact_data = {
            "name": "Invalid Contact",
            "phone": "not-a-phone-number"
        }

        response = client.post(
            "/contacts",
            json=contact_data,
            headers=auth_headers
        )

        assert response.status_code == 422  # Validation error

    def test_duplicate_phone_number(self, client, auth_headers, sample_contact):
        """Test that duplicate phone numbers are handled"""
        contact_data = {
            "name": "Duplicate Contact",
            "phone": sample_contact.phone  # Same as existing
        }

        response = client.post(
            "/contacts",
            json=contact_data,
            headers=auth_headers
        )

        # Should either reject or update existing
        assert response.status_code in [400, 409, 200]


@pytest.mark.integration
class TestContactsIntegration:
    """Integration tests involving multiple services"""

    def test_contact_creation_triggers_notification(
        self, client, auth_headers, mock_whatsapp_api
    ):
        """Test that creating a contact triggers welcome message"""
        contact_data = {
            "name": "New Contact",
            "phone": "+19876543210",
            "send_welcome": True
        }

        response = client.post(
            "/contacts",
            json=contact_data,
            headers=auth_headers
        )

        assert response.status_code == 200

        # Verify WhatsApp API was called
        mock_whatsapp_api.assert_called_once()


# Run tests with:
# pytest tests/test_contacts_api.py -v
# pytest tests/test_contacts_api.py -v --cov=contacts
# pytest tests/test_contacts_api.py -m "not slow"  # Skip slow tests
