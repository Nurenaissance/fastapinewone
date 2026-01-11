"""
PyTest Configuration for FastAPI Backend
Provides fixtures for testing with database rollback and mock services
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from config.database import Base, get_db
from main import app
import os

# Test database URL (use separate test DB)
TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL', 'postgresql://user:pass@localhost/test_db')

# Create test engine
test_engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function")
def db_session():
    """
    Create a fresh database session for each test with automatic rollback

    Usage in tests:
        def test_create_contact(db_session):
            contact = Contact(name="Test")
            db_session.add(contact)
            db_session.commit()
    """
    # Create tables
    Base.metadata.create_all(bind=test_engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

        # Drop all tables after test
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session):
    """
    FastAPI TestClient with overridden database dependency

    Usage:
        def test_api_endpoint(client):
            response = client.get("/contacts")
            assert response.status_code == 200
    """
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    """
    Mock authentication headers for testing protected endpoints

    Usage:
        def test_protected_endpoint(client, auth_headers):
            response = client.get("/protected", headers=auth_headers)
    """
    import jwt
    from main import JWT_SECRET, JWT_ALGORITHM

    token = jwt.encode(
        {
            "sub": "test_user_id",
            "tenant_id": "test_tenant_id",
            "tier": "premium"
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM
    )

    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": "test_tenant_id"
    }


@pytest.fixture
def mock_whatsapp_api(mocker):
    """
    Mock external WhatsApp API calls

    Usage:
        def test_send_message(mock_whatsapp_api):
            # WhatsApp API calls will return mock data
            response = send_whatsapp_message(...)
            assert response['success'] == True
    """
    mock = mocker.patch('whatsapp_tenant.router.send_to_whatsapp')
    mock.return_value = {
        'success': True,
        'message_id': 'wamid.test123',
        'status': 'sent'
    }
    return mock


@pytest.fixture
def sample_contact(db_session):
    """
    Create a sample contact for testing

    Usage:
        def test_contact_update(sample_contact, db_session):
            sample_contact.name = "Updated Name"
            db_session.commit()
    """
    from contacts.models import Contact

    contact = Contact(
        id="test_contact_id",
        name="Test Contact",
        phone="+1234567890",
        tenant_id="test_tenant_id",
        status="active"
    )
    db_session.add(contact)
    db_session.commit()
    db_session.refresh(contact)

    return contact


@pytest.fixture
def sample_broadcast_group(db_session):
    """
    Create a sample broadcast group for testing
    """
    from whatsapp_tenant.models import BroadcastGroups

    group = BroadcastGroups(
        id="test_group_id",
        name="Test Group",
        tenant_id="test_tenant_id",
        members=[],
        auto_rules={
            "enabled": True,
            "logic": "AND",
            "conditions": [
                {
                    "type": "date",
                    "field": "createdOn",
                    "operator": "within_days",
                    "value": 30
                }
            ]
        }
    )
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    return group


# Pytest configuration
def pytest_configure(config):
    """
    Configure pytest markers
    """
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "unit: mark test as unit test"
    )
