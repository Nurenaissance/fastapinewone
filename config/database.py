import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool

# Configure logging
logger = logging.getLogger(__name__)

DATABASE_URL="postgresql://nurenai:Biz1nurenWar*@nurenaistore.postgres.database.azure.com/nurenpostgres_Whatsapp"

# Set up SQLAlchemy engine with improved connection pool configuration
engine = create_engine(
    DATABASE_URL,
    # Connection pool settings - AGGRESSIVE REDUCTION for Azure connection exhaustion
    poolclass=QueuePool,         # Explicit pool class
    pool_size=2,                 # REDUCED: 2 connections per worker
    max_overflow=1,              # REDUCED: Only 1 overflow (max 3 per worker)
    pool_timeout=30,             # Reduced from 120 - fail faster if no connections available
    pool_recycle=1800,           # Recycle connections after 30 minutes (Azure timeout is usually 1 hour)
    pool_pre_ping=True,          # Validate connections before use (handles disconnects)
    
    # Additional settings for Azure PostgreSQL
    connect_args={
        "sslmode": "require",            # Azure PostgreSQL requires SSL
        "connect_timeout": 60,           # Increased connection timeout
        "application_name": "FastAPI_Scheduler",  # Updated name
        "keepalives_idle": 600,          # Keep connection alive (10 minutes)
        "keepalives_interval": 30,       # Keepalive interval
        "keepalives_count": 3            # Keepalive retries
    },
    
    # Logging (set to False in production)
    echo=False,                          # Set to True for SQL debugging
    
    # Engine settings - FIXED: Removed duplicate pool_reset_on_return
    pool_reset_on_return='rollback',     # Handle failed transactions (ONLY ONE INSTANCE)
    isolation_level="READ_COMMITTED",    # Default isolation level
    future=True                          # Use SQLAlchemy 2.0 style
)

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