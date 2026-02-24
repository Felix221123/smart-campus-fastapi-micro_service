# src/auth.py
# JWT Authentication for FastAPI (matches your Express authenticate middleware)

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from src.database import get_db
from src.module.models import User
from src import constants

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Authenticate user via JWT token (Bearer token in Authorization header).
    Matches your Express authenticate middleware logic:
    1. Extract token from Authorization header
    2. Verify JWT signature
    3. Check if user exists in database
    4. Validate sessionToken matches
    """
    token = credentials.credentials

    try:
        # Decode JWT token
        payload = jwt.decode(
            token,
            constants.config["JWT_SECRET"],
            algorithms=[constants.config["JWT_ALGORITHM"]]
        )

        user_id: str = payload.get("id")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload"
            )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )

    # Fetch user from database
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not found"
        )

    # Validate sessionToken matches (like your Express middleware)
    if user.sessionToken != token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid session or token expired"
        )

    return user


# Optional: WebSocket authentication helper
async def get_current_user_ws(token: str, db: Session) -> User:
    """
    Authenticate WebSocket connections.
    Call this manually in WebSocket endpoints with token from query params.
    """
    try:
        payload = jwt.decode(
            token,
            constants.config["JWT_SECRET"],
            algorithms=[constants.config["JWT_ALGORITHM"]]
        )

        user_id: str = payload.get("id")
        if user_id is None:
            raise ValueError("Invalid token payload")

    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
        raise ValueError(f"Token validation failed: {str(e)}")

    user = db.query(User).filter(User.id == user_id).first()

    if not user or user.sessionToken != token:
        raise ValueError("Invalid session or user not found")

    return user
