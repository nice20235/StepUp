"""
Performance monitoring middleware
"""
import time
import logging
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

class PerformanceMiddleware(BaseHTTPMiddleware):
    """Middleware to monitor API performance"""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Start timer
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Calculate processing time
        process_time = time.time() - start_time
        
        # Add performance headers
        response.headers["X-Process-Time"] = str(process_time)
        
        # Log slow requests (over 1 second)
        if process_time > 1.0:
            logger.warning(
                f"Slow request: {request.method} {request.url.path} "
                f"took {process_time:.2f}s"
            )
        
        # Log all requests in debug mode
        logger.debug(
            f"{request.method} {request.url.path} - "
            f"{response.status_code} - {process_time:.3f}s"
        )
        
        return response

class CompressionHeaderMiddleware(BaseHTTPMiddleware):
    """Middleware to add compression hints"""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Add compression hints for JSON responses
        if (
            response.headers.get("content-type", "").startswith("application/json") and
            int(response.headers.get("content-length", "0")) > 1024  # Only for larger responses
        ):
            response.headers["X-Compress-Hint"] = "true"
        
        return response

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers"""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Add security headers (safe defaults)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Do NOT set CSP on interactive docs/static assets to avoid blocking Swagger UI
        path = request.url.path
        _csp_excluded_prefixes = (
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico",
            "/static",
        )

        content_type = response.headers.get("content-type", "")
        if (
            "text/html" in content_type
            and not any(path.startswith(p) for p in _csp_excluded_prefixes)
        ):
            # Reasonable CSP for app HTML; allow inline styles for simplicity
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "connect-src 'self';"
            )
        
        return response
