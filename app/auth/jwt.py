from datetime import datetime, timedelta
from jose import JWTError, jwt
from app.core.config import settings
from typing import Optional, Dict, Any
import os

ALGORITHM = os.getenv("ALGORITHM", "HS256")
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "1"))  # Reduced from 7 to 1 day
SESSION_MAX_DAYS = int(os.getenv("SESSION_MAX_DAYS", "1"))  # Default 1 day instead of 0 (disabled)
SESSION_MAX_HOURS = int(os.getenv("SESSION_MAX_HOURS", "8"))  # Read from env, default 8 hours

def _calc_session_exp(now: datetime, existing_session_exp: datetime | None = None) -> datetime | None:
    """Return absolute session expiration or None if disabled.

    If SESSION_MAX_DAYS == 0 => disabled.
    existing_session_exp lets us keep the original absolute expiry when rotating tokens.
    """
    if SESSION_MAX_HOURS > 0:
        if existing_session_exp:
            return existing_session_exp
        return now + timedelta(hours=SESSION_MAX_HOURS)
    if SESSION_MAX_DAYS <= 0:
        return None
    if existing_session_exp:
        return existing_session_exp
    return now + timedelta(days=SESSION_MAX_DAYS)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None, session_exp: datetime | None = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    if session_exp is not None:
        to_encode["sess_exp"] = int(session_exp.timestamp())
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None, session_exp: datetime | None = None) -> str:
    """Create JWT refresh token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "type": "refresh"})
    if session_exp is not None:
        to_encode["sess_exp"] = int(session_exp.timestamp())
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate JWT access token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None

def decode_refresh_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate JWT refresh token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None

def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify any JWT token and return payload"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None