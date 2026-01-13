import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool, NullPool

# Configure logging
logger = logging.getLogger(__name__)

# =============================================================================
# PERMANENT FIX: PgBouncer Connection Pooling (RECOMMENDED)
# =============================================================================
# Azure PostgreSQL Flexible Server has built-in PgBouncer on port 6432
# This MUST be enabled in Azure Portal:
#   Azure Portal → PostgreSQL → Server Parameters → pgbouncer.enabled = true
#
# Benefits:
# - Eliminates "remaining connection slots" errors permanently
# - Supports 1000s of app connections with only ~100 DB connections
# - Better performance under high load
# =============================================================================

# DEFAULT TO TRUE - PgBouncer enabled by default for production stability
USE_PGBOUNCER = os.environ.get('USE_PGBOUNCER', 'true').lower() == 'true'
DB_PORT = '6432' if USE_PGBOUNCER else '5432'

DATABASE_URL = f"postgresql://nurenai:Biz1nurenWar*@nurenaistore.postgres.database.azure.com:{DB_PORT}/nurenpostgres_Whatsapp"

logger.info(f"Database mode: {'PgBouncer (port 6432)' if USE_PGBOUNCER else 'Direct (port 5432)'}")

if USE_PGBOUNCER:
    # ==========================================================================
    # PgBouncer Mode (RECOMMENDED for production)
    # ==========================================================================
    # NullPool: No application-level pooling - PgBouncer handles everything
    # This is the correct configuration for PgBouncer transaction pooling
    engine = create_engine(
        DATABASE_URL,
        poolclass=NullPool,
        connect_args={
            "sslmode": "require",
            "connect_timeout": 10,
            "application_name": "FastAPI_PgBouncer",
            # Statement timeout to prevent long-running queries
            "options": "-c statement_timeout=30000",
        },
        echo=False,
        isolation_level="READ_COMMITTED",
        future=True
    )
    logger.info("✅ Using PgBouncer mode (NullPool) - Connection pooling handled by Azure")
else:
    # ==========================================================================
    # Direct Connection Mode (Fallback - NOT recommended for production)
    # ==========================================================================
    # Conservative pool settings to prevent connection exhaustion
    engine = create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_size=2,
        max_overflow=3,
        pool_timeout=10,
        pool_recycle=300,
        pool_pre_ping=True,
        connect_args={
            "sslmode": "require",
            "connect_timeout": 10,
            "application_name": "FastAPI_Direct",
            "keepalives_idle": 300,
            "keepalives_interval": 30,
            "keepalives_count": 3
        },
        echo=False,
        pool_reset_on_return='rollback',
        isolation_level="READ_COMMITTED",
        future=True
    )
    logger.warning("⚠️ Using Direct mode (QueuePool) - Enable PgBouncer for production!")

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