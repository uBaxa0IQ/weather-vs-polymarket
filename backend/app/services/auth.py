from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

_ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=True)


def create_token() -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    return jwt.encode({"exp": expire, "sub": "admin"}, settings.jwt_secret, algorithm=_ALGORITHM)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_auth(credentials: HTTPAuthorizationCredentials = Security(_bearer)) -> str:
    _decode_token(credentials.credentials)
    return credentials.credentials
