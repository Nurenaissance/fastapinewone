"""
PyTest Configuration for FastAPI Backend
Provides fixtures for testing with database rollback and mock services.

FIXED: Uses SQLite for tests instead of PostgreSQL — runs locally without external DB.
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from config.database import Base, get_db
from main import app
import os

# ── SQLite for tests (no external DB required) ──
TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL', 'sqlite:///./test.db')

# SQLite needs check_same_thread=False for FastAPI's threaded test client
connect_args = {"check_same_thread": False} if "sqlite" in TEST_DATABASE_URL else {}
test_engine = create_engine(TEST_DATABASE_URL, connect_args=connect_args)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


# Enable WAL mode and foreign keys for SQLite
@event.listens_for(test_engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    if "sqlite" in TEST_DATABASE_URL:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture(scope="function")
def db_session():
    """
    Create a fresh database session for each test with automatic rollback.
    """
    Base.metadata.create_all(bind=test_engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session):
    """
    FastAPI TestClient with overridden database dependency.
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
    Mock authentication headers for testing protected endpoints.
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
def sample_tenant(db_session):
    """Create a sample tenant for testing."""
    from models import Tenant

    tenant = Tenant(
        id="test_tenant_id",
        organization="Test Org",
        db_user="test_db_user",
        db_user_password="test_db_pass",
        tier="premium",
    )
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture
def sample_contact(db_session, sample_tenant):
    """Create a sample contact for testing."""
    from contacts.models import Contact

    contact = Contact(
        name="Test Contact",
        phone="+1234567890",
        tenant_id=sample_tenant.id,
    )
    db_session.add(contact)
    db_session.commit()
    db_session.refresh(contact)
    return contact


@pytest.fixture
def sample_broadcast_group(db_session, sample_tenant):
    """Create a sample broadcast group for testing."""
    from whatsapp_tenant.models import BroadcastGroups

    group = BroadcastGroups(
        id="test_group_id",
        name="Test Group",
        tenant_id=sample_tenant.id,
        members=[],
        auto_rules={},
    )
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


@pytest.fixture
def mock_whatsapp_api(mocker):
    """Mock external WhatsApp API calls."""
    mock = mocker.patch('whatsapp_tenant.router.send_to_whatsapp')
    mock.return_value = {
        'success': True,
        'message_id': 'wamid.test123',
        'status': 'sent'
    }
    return mock


# Pytest configuration
def pytest_configure(config):
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "unit: mark test as unit test")
