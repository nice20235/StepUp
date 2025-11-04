from fastapi import Depends, HTTPException, status, Request
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.auth.jwt import decode_access_token
from app.models.user import User
from app.crud.user import get_user
import logging

# Set up logging
logger = logging.getLogger(__name__)

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Get current authenticated user from JWT token"""
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
        user = await get_user(db, user_id)
        
        if user is None:
            logger.warning(f"User not found: {user_id}")
            raise credentials_exception
        
        logger.info(f"User authenticated successfully: {user.name} (ID: {user.id})")
        return user
        
    except (JWTError, ValueError) as e:
        logger.warning(f"JWT validation error: {e}")
        raise credentials_exception

async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current user (kept for compatibility, but no longer checks is_active)"""
    return current_user

async def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """Get current admin user"""
    if not current_user.is_admin:
        logger.warning(f"Non-admin user attempted admin access: {current_user.name} (Admin: {current_user.is_admin})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Admin access required. You don't have permission to access this resource."
        )
    logger.info(f"Admin access granted: {current_user.name} (ID: {current_user.id})")
    return current_user

async def get_current_user_or_admin(current_user: User = Depends(get_current_user)):
    return current_user 