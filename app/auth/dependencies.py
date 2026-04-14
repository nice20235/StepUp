from fastapi import Depends, HTTPException, status, Request
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.auth.jwt import decode_access_token
from app.models.user import User
from app.crud.user import get_user
from app.core.cache import cache
import logging

# Set up logging
logger = logging.getLogger(__name__)


class CurrentUser(BaseModel):
    """Lightweight authenticated user context, decoupled from SQLAlchemy sessions.

    We intentionally avoid returning ORM User instances from this dependency,
    because caching ORM objects across requests can lead to DetachedInstanceError
    when their original DB session is gone. This model carries only scalar
    fields used by the API and is safe to cache in-memory.
    """

    id: int
    name: str
    surname: str | None = None
    phone_number: str | None = None
    is_admin: bool
    password_hash: str


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> CurrentUser:
    """Get current authenticated user from JWT token.

    Returns a `CurrentUser` value object rather than a SQLAlchemy `User` instance
    to avoid session-bound state leaking across requests.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # Prefer Authorization header (Bearer ...) over legacy cookies
        header_auth = request.headers.get("Authorization") or request.headers.get("authorization")
        header_token = None
        if header_auth:
            parts = header_auth.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                header_token = parts[1].strip()
            elif len(parts) == 1:
                # Non-standard: token provided without scheme
                header_token = parts[0].strip()

        # Only Authorization header is supported now
        token_to_use = header_token
        if not token_to_use:
            raise credentials_exception

        payload = decode_access_token(token_to_use)
        if payload is None or "sub" not in payload:
            logger.warning("Invalid JWT token or missing subject")
            raise credentials_exception
        
        # Check session expiration if present
        sess_exp_ts = payload.get("sess_exp")
        if sess_exp_ts:
            from datetime import datetime
            sess_exp_dt = datetime.utcfromtimestamp(int(sess_exp_ts))
            if datetime.utcnow() >= sess_exp_dt:
                logger.warning("Session expired, forcing re-login")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session expired. Please log in again.",
                    headers={"WWW-Authenticate": "Bearer"}
                )
        
        user_id: int = int(payload["sub"])

        # Try to read user context from in-memory cache first to avoid a DB query
        cache_key = f"user:{user_id}"
        cached_user = await cache.get(cache_key)
        if cached_user is not None:
            # Cached value is a plain Pydantic model (or dict), never a SQLAlchemy instance
            if isinstance(cached_user, CurrentUser):
                logger.info(f"User authenticated from cache: id={user_id}")
                return cached_user
            try:
                user_ctx = CurrentUser.model_validate(cached_user)
                logger.info(f"User authenticated from cache: id={user_id}")
                return user_ctx
            except Exception:
                # Fallback to DB if cache data is in an unexpected format
                logger.warning("Cached user data had unexpected format; falling back to DB lookup")

        # Fallback to database lookup and populate cache with a detached value object
        db_user = await get_user(db, user_id)

        if db_user is None:
            logger.warning(f"User not found: {user_id}")
            raise credentials_exception

        user_ctx = CurrentUser(
            id=db_user.id,
            name=db_user.name,
            surname=db_user.surname,
            phone_number=db_user.phone_number,
            is_admin=db_user.is_admin,
            password_hash=db_user.password_hash,
        )

        # Cache the user context object for subsequent requests within a short TTL
        await cache.set(cache_key, user_ctx)

        logger.info(f"User authenticated successfully: {user_ctx.name} (ID: {user_ctx.id})")
        return user_ctx
        
    except (JWTError, ValueError) as e:
        logger.warning(f"JWT validation error: {e}")
        raise credentials_exception

async def get_current_active_user(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Get current user (kept for compatibility, but no longer checks is_active)."""
    return current_user

async def get_current_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Get current admin user"""
    if not current_user.is_admin:
        logger.warning(f"Non-admin user attempted admin access: {current_user.name} (Admin: {current_user.is_admin})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Admin access required. You don't have permission to access this resource."
        )
    logger.info(f"Admin access granted: {current_user.name} (ID: {current_user.id})")
    return current_user


async def get_current_user_or_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return current_user 