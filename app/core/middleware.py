"""Core middleware: performance, compression, security, and Basic Auth for /rpc."""
import base64
import logging
import time
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

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


class BasicAuthRPCMiddleware(BaseHTTPMiddleware):
    """Basic Auth middleware for the JSON-RPC /rpc endpoint.

    The acquirer authenticates using HTTP Basic Auth. Credentials are configured
    via environment variables and exposed through `settings`:

    - ACQUIRING_RPC_BASIC_USERNAME
    - ACQUIRING_RPC_BASIC_PASSWORD
    """

    def __init__(self, app):
        super().__init__(app)
        self._username = settings.ACQUIRING_RPC_BASIC_USERNAME
        self._password = settings.ACQUIRING_RPC_BASIC_PASSWORD

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Apply Basic Auth protection only for the /rpc endpoint
        if request.url.path != "/rpc":
            return await call_next(request)

        auth_header = request.headers.get("authorization") or request.headers.get(
            "Authorization"
        )
        if not auth_header or not auth_header.lower().startswith("basic "):
            return self._unauthorized_response()

        encoded_credentials = auth_header.split(" ", 1)[1].strip()
        try:
            decoded = base64.b64decode(encoded_credentials).decode("utf-8")
        except Exception:
            return self._unauthorized_response()

        if ":" not in decoded:
            return self._unauthorized_response()

        username, password = decoded.split(":", 1)
        if username != self._username or password != self._password:
            logger.warning("Invalid Basic Auth credentials for /rpc")
            return self._unauthorized_response()

        return await call_next(request)

    @staticmethod
    def _unauthorized_response() -> Response:
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
            headers={"WWW-Authenticate": "Basic realm=\"rpc\""},
        )
