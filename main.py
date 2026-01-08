from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager
from config.database import engine, Base
from config.middleware import add_cors_middleware
import contacts.router, node_templates.router, scheduled_events.router, whatsapp_tenant.router
import product.router, dynamic_models.router
import conversations.router, emails, notifications.router
import broadcast_analytics.router
import catalog.router
import flowsAPI.router
import logging
import jwt
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ------------- Logging -------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

newEvent = False

# ------------- JWT config (must match Django) -------------
# Load JWT secret from environment variable
JWT_SECRET = os.getenv('JWT_SECRET_KEY', 'CHANGE_THIS_TO_A_LONG_RANDOM_STRING')
JWT_ALGORITHM = "HS256"

# Warn if using default JWT secret
if JWT_SECRET == 'CHANGE_THIS_TO_A_LONG_RANDOM_STRING':
    logger.warning('⚠️ WARNING: Using default JWT_SECRET. Set JWT_SECRET_KEY in .env for production!')

# ------------- Lifespan -------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - startup and shutdown"""
    logger.info("FastAPI application starting up...")
    # Startup code (if needed)
    yield
    # Shutdown code
    logger.info("FastAPI application shutting down...")
    try:
        from conversations.router import cleanup_resources
        cleanup_resources()
        logger.info("Resources cleaned up successfully")
    except ImportError:
        logger.warning("Could not import cleanup_resources - ensure it's available in conversations.router")
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")

# ------------- Create app -------------
app = FastAPI(lifespan=lifespan)
from fastapi.responses import JSONResponse
# ------------- Service Authentication Keys -------------
# Load service keys from environment
SERVICE_KEYS = {
    'django': os.getenv('DJANGO_SERVICE_KEY'),
    'fastapi': os.getenv('FASTAPI_SERVICE_KEY'),
    'nodejs': os.getenv('NODEJS_SERVICE_KEY'),
}

def is_valid_service_key(api_key: str) -> tuple[bool, str]:
    """
    Check if API key is a valid service key
    Returns: (is_valid, service_name)
    """
    for service_name, key in SERVICE_KEYS.items():
        if key and api_key == key:
            return True, service_name
    return False, None

# ------------- Dual Authentication Middleware (JWT + Service Keys) -------------
async def jwt_middleware(request: Request, call_next):
    """
    Enhanced authentication middleware supporting:
    1. Public routes (no auth)
    2. Service-to-service authentication (X-Service-Key header)
    3. User JWT authentication (Authorization: Bearer token)
    """
    # PUBLIC ROUTES — exact match only
    PUBLIC_PATHS = {
        "/health",
        "/",
        "/admin/cleanup",
        "/admin/resources",
    }

    # 1. Allow public routes
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/docs"):
        return await call_next(request)

    # 2. Check for Service API Key (X-Service-Key header)
    service_key = request.headers.get("X-Service-Key")

    if service_key:
        is_valid, service_name = is_valid_service_key(service_key)

        if is_valid:
            # Valid service key - allow request
            request.state.is_service_request = True
            request.state.service_name = service_name

            # Still get X-Tenant-Id for tenant-specific operations
            tenant_id = request.headers.get("X-Tenant-Id")
            if tenant_id:
                request.state.tenant_id = tenant_id

            logger.info(f"✅ Service request from: {service_name} (tenant: {tenant_id or 'none'})")
            return await call_next(request)
        else:
            logger.warning(f"❌ Invalid service key attempted")
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "message": "Invalid service key"}
            )

    # 3. Check for User JWT Token (Authorization header)
    auth = request.headers.get("Authorization", "")

    if not auth.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "Authorization token missing"}
        )

    token = auth.replace("Bearer ", "")

    try:
        # Decode and verify JWT
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_aud": False})
        request.state.user_id = payload.get("sub")
        request.state.tenant_id = payload.get("tenant_id")
        request.state.tier = payload.get("tier", "free")
        request.state.is_service_request = False

        return await call_next(request)

    except jwt.ExpiredSignatureError:
        return JSONResponse(
            status_code=401,
            content={"error": "token_expired", "message": "Access token has expired"}
        )
    except jwt.InvalidTokenError:
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_token", "message": "Invalid token"}
        )

# SECURITY FIX: JWT Authentication Middleware ENABLED
# This protects all FastAPI endpoints with dual authentication:
# - Service-to-service: X-Service-Key header
# - User requests: Authorization: Bearer token
# To rollback if issues occur, simply comment out the line below
app.middleware("http")(jwt_middleware)

# ------------- CORS + DB -------------
add_cors_middleware(app)
Base.metadata.create_all(bind=engine)

# ------------- Routers -------------
app.include_router(contacts.router.router)
app.include_router(node_templates.router.router)
app.include_router(whatsapp_tenant.router.router)
app.include_router(scheduled_events.router.router)
app.include_router(product.router.router)
app.include_router(dynamic_models.router.router)
app.include_router(conversations.router.router)
app.include_router(emails.router)
app.include_router(notifications.router.router)
app.include_router(flowsAPI.router.router)
app.include_router(catalog.router.router)
app.include_router(broadcast_analytics.router.router)

# ------------- Health + debug endpoints -------------
@app.get("/health")
def health_check():
    try:
        from conversations.router import thread_pool_manager, conversation_cache
        pool_status = "healthy" if not thread_pool_manager._shutdown else "shutdown"
        cache_entries = len(conversation_cache._cache)
    except Exception as e:
        pool_status = "error"
        cache_entries = -1
        logger.error(f"Health check error: {e}")
    
    return {
        "status": "FastApi Code is healthy",
        "thread_pool_status": pool_status,
        "cache_entries": cache_entries,
        "timestamp": logging.Formatter().formatTime(logging.LogRecord(
            name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None
        ))
    }

@app.get("/")
def read_root():
    return {"message": "FastAPI server is running"}

@app.post("/admin/cleanup")
async def manual_cleanup():
    try:
        from conversations.router import cleanup_resources
        cleanup_resources()
        return {"message": "Resources cleaned up manually"}
    except Exception as e:
        logger.error(f"Manual cleanup error: {str(e)}")
        return {"error": str(e)}

@app.get("/admin/resources")
async def check_resources():
    try:
        from conversations.router import thread_pool_manager, conversation_cache
        pool = thread_pool_manager._pool
        return {
            "thread_pool_shutdown": thread_pool_manager._shutdown,
            "cache_entries": len(conversation_cache._cache),
            "pool_exists": pool is not None,
            "pool_shutdown": pool._shutdown if pool else None,
            "active_threads": pool._threads if pool and hasattr(pool, '_threads') else 0
        }
    except Exception as e:
        return {"error": str(e)}
