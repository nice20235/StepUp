from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import settings


security = HTTPBasic()


def verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    """Verify Basic Auth credentials for JSON-RPC endpoint.

    Username and password are taken from settings:
    - RPC_USERNAME
    - RPC_PASSWORD
    """

    expected_username = settings.RPC_USERNAME
    expected_password = settings.RPC_PASSWORD

    if credentials.username != expected_username or credentials.password != expected_password:
        # RFC-compliant 401 with WWW-Authenticate header
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
