import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool, NullPool

# Configure logging
logger = logging.getLogger(__name__)

# Set USE_PGBOUNCER=true in environment if PgBouncer is enabled on Azure
USE_PGBOUNCER = os.environ.get('USE_PGBOUNCER', 'false').lower() == 'true'
DB_PORT = '6432' if USE_PGBOUNCER else '5432'

DATABASE_URL = f"postgresql://nurenai:Biz1nurenWar*@nurenaistore.postgres.database.azure.com:{DB_PORT}/nurenpostgres_Whatsapp"

# Set up SQLAlchemy engine with connection pool configuration
# When using PgBouncer, use NullPool (PgBouncer handles pooling)
# Without PgBouncer, use QueuePool with conservative settings

if USE_PGBOUNCER:
    # PgBouncer handles connection pooling - use NullPool
    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,  # No application-level pooling (PgBouncer handles it)
        connect_args={
            "sslmode": "require",
            "connect_timeout": 30,
            "application_name": "FastAPI_PgBouncer",
        },
        echo=False,
        isolation_level="READ_COMMITTED",
        future=True
    )
    logger.info("Using PgBouncer mode (NullPool)")
else:
    # General Purpose D2s_v3 tier - can use larger pool
    # Allocate ~30 connections for FastAPI (pool_size=5 * workers + overflow)
    engine = create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_size=5,                 # 5 connections per worker (increased from 2)
        max_overflow=5,              # 5 overflow (max 10 per worker)
        pool_timeout=30,             # Fail faster if no connections
        pool_recycle=1800,           # Recycle after 30 minutes
        pool_pre_ping=True,          # Validate connections before use
        connect_args={
            "sslmode": "require",
            "connect_timeout": 30,
            "application_name": "FastAPI_Scheduler",
            "keepalives_idle": 300,
            "keepalives_interval": 30,
            "keepalives_count": 3
        },
        echo=False,
        pool_reset_on_return='rollback',
        isolation_level="READ_COMMITTED",
        future=True
    )
    logger.info("Using QueuePool mode - General Purpose tier (pool_size=5, max_overflow=5)")

# Create a session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Define a base class for the models
Base = declarative_base()

# IMPROVED: Dependency function with better error handling
def get_db():
    """
    Enhanced database dependency with proper error handling and connection cleanup
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        logger.error(f"Database session error: {e}")
        # Rollback any pending transaction
        try:
            db.rollback()
        except Exception as rollback_error:
            logger.error(f"Error during rollback: {rollback_error}")
        raise
    finally:
        # CRITICAL: Always close the session
        try:
            db.close()
        except Exception as close_error:
            logger.error(f"Error closing database session: {close_error}")

# IMPROVED: Health check function with better error handling
def test_db_connection():
    """Test database connection - useful for health checks"""
    db = None
    try:
        db = SessionLocal()
        result = db.execute("SELECT 1").fetchone()
        logger.info("Database connection test successful")
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return False
    finally:
        if db:
            try:
                db.close()
            except Exception as close_error:
                logger.error(f"Error closing test database connection: {close_error}")

# IMPROVED: Get current pool status for monitoring with error handling
def get_pool_status():
    """Return current connection pool status for monitoring"""
    try:
        pool = engine.pool
        status = {
            "pool_size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "checked_in": pool.checkedin(),
            "total_capacity": pool.size() + engine.pool.max_overflow,
            "connection_utilization": f"{pool.checkedout()}/{pool.size() + engine.pool.max_overflow}",
            "pool_status": "healthy" if pool.checkedout() < (pool.size() + engine.pool.max_overflow) * 0.8 else "warning"
        }
        return status
    except Exception as e:
        logger.error(f"Error getting pool status: {e}")
        return {"error": "Could not retrieve pool status"}

# NEW: Connection pool monitoring
def log_pool_status():
    """Log current pool status for monitoring"""
    status = get_pool_status()
    if "error" not in status:
        logger.info(f"DB Pool Status - Checked out: {status['checked_out']}, "
                   f"Available: {status['checked_in']}, "
                   f"Total capacity: {status['total_capacity']}")
    
        # Warning if pool utilization is high
        utilization_ratio = status['checked_out'] / status['total_capacity']
        if utilization_ratio > 0.8:
            logger.warning(f"High database connection pool utilization: {utilization_ratio:.2%}")

# NEW: Force close all connections (emergency use)
def force_close_all_connections():
    """Emergency function to close all database connections"""
    try:
        engine.dispose()
        logger.warning("All database connections forcefully closed")
        return True
    except Exception as e:
        logger.error(f"Error force closing connections: {e}")
        return False

# NEW: Create a new engine (for recovery)
def recreate_engine():
    """Recreate the database engine (for recovery from connection issues)"""
    global engine, SessionLocal
    try:
        old_engine = engine
        old_engine.dispose()
        
        # Create new engine with same settings
        engine = create_engine(
            DATABASE_URL,
            poolclass=QueuePool,
            pool_size=3,
            max_overflow=2,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
            connect_args={
                "sslmode": "require",
                "connect_timeout": 60,
                "application_name": "FastAPI_Scheduler",
                "keepalives_idle": 600,
                "keepalives_interval": 30,
                "keepalives_count": 3
            },
            echo=False,
            pool_reset_on_return='rollback',
            isolation_level="READ_COMMITTED",
            future=True
        )
        
        # Update SessionLocal with new engine
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        logger.info("Database engine recreated successfully")
        return True
    except Exception as e:
        logger.error(f"Error recreating database engine: {e}")
        return False