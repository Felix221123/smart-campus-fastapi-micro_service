# src/users/views.py
# User search and profile endpoints

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from src.database import get_db
from src.auth import get_current_user
from src.module.models import User
from pydantic import BaseModel
from uuid import UUID

router = APIRouter(prefix="/users", tags=["Users"])



# Pydantic Schemas

class UserSearchResponse(BaseModel):
    id: UUID
    full_name: str
    email: str
    role: str
    university_id: str
    bio: str | None = None

    class Config:
        from_attributes = True
        json_encoders = {
            UUID: lambda v: str(v)
        }



# Search Users Endpoint
@router.get("/search", response_model=List[UserSearchResponse])
def search_users(
    query: str = Query(..., min_length=2, description="Search query (name, email, or university ID)"),
    limit: int = Query(20, ge=1, le=100, description="Max number of results"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Search for users by name, email, or university ID.
    Returns users matching the search query (excludes current user).
    """

    # Search by full_name, email, or university_id
    search_filter = or_(
        User.full_name.ilike(f"%{query}%"),
        User.email.ilike(f"%{query}%"),
        User.university_id.ilike(f"%{query}%")
    )

    # Exclude current user from results
    users = (
        db.query(User)
        .filter(and_(search_filter, User.id != current_user.id))
        .limit(limit)
        .all()
    )

    return users



# Get User Profile by ID
@router.get("/{user_id}", response_model=UserSearchResponse)
def get_user_profile(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a user's profile by their ID"""
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    return user
