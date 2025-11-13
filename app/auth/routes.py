from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.auth.jwt import create_access_token, create_refresh_token, decode_refresh_token, _calc_session_exp
from app.crud.user import create_user, authenticate_user, get_user_by_name, get_user_by_phone_number, get_user, update_user_password
from app.schemas.user import UserCreate, UserLogin, RefreshTokenRequest, UserResponse, ForgotPasswordRequest
from typing import Optional

import logging
from datetime import datetime
import time
from collections import defaultdict, deque
from app.core.config import settings

logger = logging.getLogger(__name__)

auth_router = APIRouter()

# In-memory rate limit storage: key -> deque[timestamps]
_login_attempts = defaultdict(deque)

def _rate_limit_key(name: str, client_ip: str) -> str:
    return f"{client_ip}:{name}" if name else client_ip

def check_login_rate_limit(name: str, client_ip: str):
    now = time.time()
    window = settings.LOGIN_RATE_WINDOW_SEC
    limit = settings.LOGIN_RATE_LIMIT
    key = _rate_limit_key(name, client_ip)
    dq = _login_attempts[key]
    # drop old
    while dq and now - dq[0] > window:
        dq.popleft()
    if len(dq) >= limit:
        raise HTTPException(status_code=429, detail="Too many login attempts. Please try again later.")
    # record attempt
    dq.append(now)


@auth_router.post("/register")
async def register_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    response: Response = None
):
    # Check if user with same name already exists
    existing_user_by_name = await get_user_by_name(db, user_data.name)
    if existing_user_by_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this name already exists"
        )
    # Check if user with same phone number already exists
    existing_user_by_phone = await get_user_by_phone_number(db, user_data.phone_number)
    if existing_user_by_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this phone number already exists"
        )
    # Create new user
    user = await create_user(db, user_data)
    logger.info(f"Created new user: {user.name} ({user.phone_number})")
    # Calculate absolute session expiration
    now_session_exp = _calc_session_exp(datetime.utcnow())
    # Create tokens
    access_token = create_access_token(data={"sub": str(user.id)}, session_exp=now_session_exp)
    refresh_token = create_refresh_token(data={"sub": str(user.id)}, session_exp=now_session_exp)
    # Return tokens via response headers
    if response is not None:
        response.headers["Authorization"] = f"Bearer {access_token}"
        response.headers["Refresh-Token"] = refresh_token
        response.headers["Token-Type"] = "bearer"
        response.headers["X-Expires-In"] = str(settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    user_payload = UserResponse.from_orm(user).dict()
    user_payload.pop("id", None)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "user": user_payload,
    }


@auth_router.post("/login")
async def login_user(
    user_credentials: UserLogin,
    db: AsyncSession = Depends(get_db),
    response: Response = None,
    request: Request = None
):
    """
    Login user with name and password
    """
    # Rate-limit by user name + client IP
    client_ip = request.client.host if request and request.client else "unknown"
    check_login_rate_limit(user_credentials.name, client_ip)
    # Authenticate user
    user = await authenticate_user(db, user_credentials.name, user_credentials.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect name or password"
        )
    # Absolute session expiration on first login
    now_session_exp = _calc_session_exp(datetime.utcnow())
    access_token = create_access_token(data={"sub": str(user.id)}, session_exp=now_session_exp)
    refresh_token = create_refresh_token(data={"sub": str(user.id)}, session_exp=now_session_exp)
    # Return tokens via response headers
    if response is not None:
        response.headers["Authorization"] = f"Bearer {access_token}"
        response.headers["Refresh-Token"] = refresh_token
        response.headers["Token-Type"] = "bearer"
        response.headers["X-Expires-In"] = str(settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    logger.info(f"User logged in successfully: {user.name} (ID: {user.id})")
    user_payload = UserResponse.from_orm(user).dict()
    user_payload.pop("id", None)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "user": user_payload,
    }


@auth_router.post("/refresh")
async def refresh_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    response: Response = None
):
    """
    Refresh access token using refresh token from body or headers
    """
    # Try to get refresh token from multiple sources
    refresh_token_value = None
    
    # 1. Try to get from request body
    try:
        body = await request.json()
        refresh_token_value = body.get("refresh_token") if body else None
    except Exception:
        # Body parsing failed or empty - that's fine, try headers
        pass
    
    # 2. From headers (fallback)
    if not refresh_token_value:
        refresh_token_value = (
            request.headers.get("Refresh-Token") or 
            request.headers.get("refresh-token") or
            request.headers.get("X-Refresh-Token")
        )
    
    if not refresh_token_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refresh token required in request body or headers"
        )
    
    # Decode refresh token
    payload = decode_refresh_token(refresh_token_value)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    # Get user
    user_id = int(payload["sub"])
    user = await get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    # Preserve original absolute session expiration if present
    sess_exp_ts = payload.get("sess_exp")
    if sess_exp_ts:
        sess_exp_dt = datetime.utcfromtimestamp(int(sess_exp_ts))
        if datetime.utcnow() >= sess_exp_dt:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please log in again.")
    else:
        sess_exp_dt = _calc_session_exp(datetime.utcnow())

    # Create new tokens but do NOT extend sess_exp
    access_token = create_access_token(data={"sub": str(user.id)}, session_exp=sess_exp_dt)
    refresh_token = create_refresh_token(data={"sub": str(user.id)}, session_exp=sess_exp_dt)
    # Return tokens via response headers
    if response is not None:
        response.headers["Authorization"] = f"Bearer {access_token}"
        response.headers["Refresh-Token"] = refresh_token
        response.headers["Token-Type"] = "bearer"
        response.headers["X-Expires-In"] = str(settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    logger.info(f"Token refreshed for user: {user.name} (ID: {user.id})")
    user_payload = UserResponse.from_orm(user).dict()
    user_payload.pop("id", None)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "user": user_payload,
    }

@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    """
    Logout endpoint - invalidate session and clear any cached user data
    """
    try:
        # Try to get current user to invalidate their cache
        header_auth = request.headers.get("Authorization") or request.headers.get("authorization")
        if header_auth:
            parts = header_auth.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1].strip()
                from app.auth.jwt import decode_access_token
                payload = decode_access_token(token)
                if payload and "sub" in payload:
                    user_id = payload["sub"]
                    # Clear any cached user data
                    from app.core.cache import cache
                    await cache.clear_pattern(f"user:{user_id}")
                    await cache.clear_pattern(f"orders:{user_id}")
                    logger.info(f"Logged out user {user_id} and cleared cache")
    except Exception as e:
        logger.warning(f"Error during logout cache cleanup: {e}")
    
    # Set response headers to help client clear tokens
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
    response.headers["X-Logout"] = "true"
    
    return {"message": "Logged out successfully", "timestamp": datetime.utcnow().isoformat()}


@auth_router.post("/forgot-password")
async def forgot_password(
    forgot_data: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Reset user password by username
    """
    # Check if user exists
    user = await get_user_by_name(db, forgot_data.name)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User with this name not found"
        )
    # Disallow admin password resets via this public endpoint
    if getattr(user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins cannot reset password via this endpoint"
        )
    
    # Update password
    updated_user = await update_user_password(db, forgot_data.name, forgot_data.new_password)
    if not updated_user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update password"
        )
    
    logger.info(f"Password reset for user: {updated_user.name}")
    return {"message": "Password updated successfully"}

