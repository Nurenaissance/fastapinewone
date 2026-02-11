import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool

# Configure logging
logger = logging.getLogger(__name__)

# Database configuration from environment variables (with defaults)
DB_HOST = os.environ.get('DB_HOST', 'nurenaistore.postgres.database.azure.com')
DB_USER = os.environ.get('DB_USER', 'nurenai')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'Biz1nurenWar*')
DB_NAME = os.environ.get('DB_NAME', 'nurenpostgres_Whatsapp')
DB_PORT = os.environ.get('DB_PORT', '5432')  # Always use direct port 5432

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

logger.info(f"Database: {DB_HOST}:{DB_PORT}/{DB_NAME}")

# Production-ready engine with optimized pooling for Azure
# Pool size should be: workers * 2-3, max_overflow allows burst capacity
POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '5'))
MAX_OVERFLOW = int(os.environ.get('DB_MAX_OVERFLOW', '10'))

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=30,
    pool_recycle=280,  # Slightly less than Azure's 300s idle timeout
    pool_pre_ping=True,
    connect_args={
        "sslmode": "require",
        "connect_timeout": 30,
        "application_name": "FastAPI_Production",
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    },
    echo=False,
    pool_reset_on_return='rollback',
    isolation_level="READ_COMMITTED",
    future=True
)

logger.info(f"Database pool configured: pool_size={POOL_SIZE}, max_overflow={MAX_OVERFLOW}")

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


def get_db():
    """Database dependency with proper cleanup"""
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        logger.error(f"Database session error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def get_pool_status():
    """Return connection pool status for monitoring"""
    try:
        pool = engine.pool
        return {
            "pool_size": pool.size(),
            "checked_out": pool.checkedout(),
            "checked_in": pool.checkedin(),
            "overflow": pool.overflow(),
        }
    except Exception as e:
        logger.error(f"Error getting pool status: {e}")
        return {"error": str(e)}


def force_close_all_connections():
    """Emergency: close all database connections"""
    try:
        engine.dispose()
        logger.warning("All database connections closed")
        return True
    except Exception as e:
        logger.error(f"Error closing connections: {e}")
        return False


# For backwards compatibility
USE_PGBOUNCER = False
