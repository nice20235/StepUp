from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
import uvicorn
import os
import logging
import time
from datetime import datetime
from dotenv import load_dotenv

from app.core.config import settings
from app.core.middleware import PerformanceMiddleware, CompressionHeaderMiddleware, SecurityHeadersMiddleware
from app.core.cache import cache
from app.db.database import init_db, close_db
from app.api.endpoints import users, stepups, orders, categories
from app.api.endpoints import cart as cart_router
from app.api.endpoints import octo as octo_payments
from app.auth.routes import auth_router
from app.schemas.responses import HealthCheckResponse, ErrorResponse

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

START_TIME = time.time()

# Application lifespan manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown with optimizations"""
    # Startup
    logger.info("🚀 Starting StepUp Order System...")
    
    try:
        # Initialize database
        await init_db()
        logger.info("✅ Database initialized")
        
        # Initialize cache
        await cache.clear()  # Start with clean cache
        logger.info("✅ Cache initialized")
        
        # Warm up critical cache entries if needed
        # await warm_up_cache()
        
        logger.info("✅ Application started successfully!")
        
    except Exception as e:
        logger.error(f"❌ Failed to start application: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down...")
    
    try:
        # Clean up cache
        await cache.clear()
        logger.info("✅ Cache cleared")
        
        # Close database connections
        await close_db()
        logger.info("✅ Database connections closed")
        
        logger.info("✅ Application shutdown complete!")
        
    except Exception as e:
        logger.error(f"❌ Error during shutdown: {e}")

# Create FastAPI application with optimizations
app = FastAPI(
    title="StepUp Order System API",
    description="""
    A high-performance StepUp ordering system with comprehensive features.
    
    ## 🔐 Authentication
    
    1. **Register**: `POST /auth/register` - Create new user account
    2. **Login**: `POST /auth/login` - Get JWT tokens in secure cookies
    3. **Refresh**: `POST /auth/refresh` - Refresh access token
    4. **Logout**: `POST /auth/logout` - Clear authentication cookies
    5. **Forgot Password**: `POST /auth/forgot-password` - Reset password
    
    ## 🏪 Features
    
    * **JWT-based authentication** with HttpOnly cookies
    * **Role-based access control** (User/Admin)
    * **Rate limiting** and security headers
    * **Comprehensive StepUp management** with multiple images
    * **Order tracking system** with status updates
    * **Pagination and filtering** on all list endpoints
    * **Caching system** for improved performance
    * **File upload support** for slipper images
    * **Analytics endpoints** for admin dashboard
    
    ## 🚀 Performance
    
    * **Async database operations** with connection pooling
    * **Redis-like caching** with TTL support
    * **Optimized queries** with pagination and filtering
    * **Response compression** for large payloads
    * **Performance monitoring** headers
    
    ## 📊 API Structure
    
    * `/auth/*` - Authentication endpoints
    * `/users/*` - User management (admin)
    * `/stepups/*` - StepUp catalog and images
    * `/orders/*` - Order management
    * `/categories/*` - Product categories
    """,
    version="2.0.0",
    lifespan=lifespan,
    # Optimize OpenAPI generation
    openapi_tags=[
        {"name": "Authentication", "description": "User authentication and authorization"},
        {"name": "Users", "description": "User management (admin only)"},
    {"name": "StepUps", "description": "StepUp catalog and image management"},
        {"name": "Orders", "description": "Order processing and tracking"},
        {"name": "Categories", "description": "Product category management"},
    # System diagnostics removed for simplification; keep /health only
    ],
    # Enable docs for development
    docs_url="/docs",
    redoc_url="/redoc", 
    openapi_url="/openapi.json")
"""CORS middleware configuration
We support both a concrete allow_origins list and a regex (allow_origin_regex) to
cover www/non-www and subdomain variants. Trailing slashes are stripped since
the browser's Origin header never contains them. If ALLOWED_ORIGIN_REGEX is set,
it takes precedence over the explicit list.
"""
# Normalize origins
allowed: list[str] = []
for o in settings.ALLOWED_ORIGINS.split(','):
    o = (o or "").strip()
    if not o:
        continue
    if o.endswith('/'):
        o = o[:-1]
    allowed.append(o)

cors_kwargs = dict(
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # Allow any request headers to avoid preflight rejections (covers X-Requested-With, X-Idempotency-Key, etc.)
    allow_headers=["*"],
    expose_headers=["Authorization", "Refresh-Token", "Token-Type", "X-Expires-In"],
)

# Allow both explicit origins and regex simultaneously.
# This ensures localhost works in development even when a production regex is configured.
cors_kwargs["allow_origins"] = allowed
origin_regex = getattr(settings, "ALLOWED_ORIGIN_REGEX", None)
# In DEBUG, be permissive to avoid accidental CORS blocks during development or staging
if settings.DEBUG and not origin_regex:
    origin_regex = r".*"
if origin_regex:
    cors_kwargs["allow_origin_regex"] = origin_regex

# Startup log for visibility
print(f"[CORS] allowed_origins={allowed} regex={origin_regex}")

# Performance middleware
app.add_middleware(PerformanceMiddleware)
app.add_middleware(CompressionHeaderMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# GZip compression for responses > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Simple global rate limiting middleware (IP-based) - keeping existing implementation
from collections import defaultdict, deque
import time
_req_log = defaultdict(deque)
_exclude = {p.strip() for p in settings.RATE_LIMIT_EXCLUDE_PATHS.split(',') if p.strip()}

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Always let CORS preflight pass through quickly
    if request.method == "OPTIONS":
        # Return minimal OK for preflight if another route/middleware doesn't handle it
        response = JSONResponse(status_code=200, content={})
        # When allow_credentials is true, Access-Control-Allow-Origin cannot be '*', so FastAPI's CORS
        # will set the echo origin. Here we just return early to avoid other middlewares blocking it.
        return response
    path = request.url.path
    if path in _exclude:
        return await call_next(request)

    # Identify client IP
    if settings.TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        client_ip = fwd.split(',')[0].strip() if fwd else request.client.host
    else:
        client_ip = request.client.host

    now = time.time()
    window = settings.RATE_LIMIT_WINDOW_SEC
    limit = settings.RATE_LIMIT_REQUESTS
    dq = _req_log[client_ip]
    while dq and now - dq[0] > window:
        dq.popleft()
    if len(dq) >= limit:
        reset_in = int(max(0, window - (now - dq[0]))) if dq else window
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"},
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_in)
            }
        )
    dq.append(now)
    remaining = max(0, limit - len(dq))
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    reset_in = int(max(0, window - (now - dq[0]))) if dq else window
    response.headers["X-RateLimit-Reset"] = str(reset_in)
    return response

# Register CORS middleware LAST so it becomes the outermost middleware and reliably
# handles preflight OPTIONS before other middlewares can interfere.
app.add_middleware(CORSMiddleware, **cors_kwargs)

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for better error responses"""
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "message": str(exc) if os.getenv("DEBUG", "False").lower() == "true" else "Something went wrong"
        }
    )

# Include routers

app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(categories.router, prefix="/categories", tags=["Categories"])
app.include_router(stepups.router, prefix="/stepups", tags=["StepUps"])
app.include_router(orders.router, prefix="/orders", tags=["Orders"])
app.include_router(octo_payments.router, prefix="/payments/octo", tags=["Payments (OCTO)"])
# Cart router already defines its tag; avoid re-specifying to prevent duplicates
app.include_router(cart_router.router)
# System diagnostics router removed

# Serve static files (images, etc.)
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with welcome message and basic API info"""
    return {
    "message": "🥿 StepUp Order System API",
        "version": "2.0.0",
        "docs": "/docs",
        "status": "operational"
    }

# Slow request logging middleware (diagnostics)
SLOW_REQUEST_THRESHOLD_SEC = 1.0  # adjust as needed

@app.middleware("http")
async def slow_request_logger(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    if duration > SLOW_REQUEST_THRESHOLD_SEC:
        logger.warning("Slow request path=%s duration=%.3fs", request.url.path, duration)
    response.headers["X-Process-Time"] = f"{duration:.3f}s"
    response.headers["X-Uptime"] = f"{int(time.time() - START_TIME)}"
    return response

# Health check endpoint  
@app.get("/health", tags=["System"], response_model=HealthCheckResponse)
async def health_check():
    """Health check endpoint for monitoring"""
    return HealthCheckResponse(
        status="healthy",
        timestamp=datetime.utcnow(),
        version="2.0.0",
        database=True,
        cache=True
    )

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
    host=settings.APP_HOST,
    port=settings.APP_PORT,
        reload=True,
        log_level="info"
    ) 