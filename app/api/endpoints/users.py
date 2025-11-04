from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.db.database import get_db
from app.schemas.user import UserUpdate, UserSelfUpdate, UserProfileResponse
from app.crud.user import (
    get_users,
    get_user,
    get_user_by_phone_number,
    delete_user,
    update_user,
    update_user_password,
)
from sqlalchemy.exc import IntegrityError
from app.auth.dependencies import get_current_admin, get_current_user
from app.core.cache import cached
import logging

# Set up logging
logger = logging.getLogger(__name__)


router = APIRouter()

@router.get("/")
@cached(ttl=180, key_prefix="users")
async def list_users(
    skip: int = Query(0, ge=0, description="Skip items for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Limit items per page"),
    is_admin: Optional[bool] = Query(None, description="Filter by admin status"),
    search: Optional[str] = Query(None, description="Search in name, surname, phone"),
    db: AsyncSession = Depends(get_db), 
    admin=Depends(get_current_admin)
):
    """
    List all users with pagination and filtering. Admin-only endpoint.
    Optimized with concurrent queries and minimal data transfer.
    """
    try:
        logger.info(f"Admin {admin.name} listing users (skip={skip}, limit={limit})")
        users, total = await get_users(
            db, skip=skip, limit=limit, is_admin=is_admin, search=search
        )
        
        # Optimized response structure - minimal data for list view
        items = []
        for user in users:
            items.append({
                "id": user.id,
                "name": user.name,
                "surname": user.surname,
                "phone_number": user.phone_number,
                "is_admin": user.is_admin,
                "created_at": user.created_at.isoformat()
            })
        
        return {
            "items": items,
            "total": total,
            "page": (skip // limit) + 1,
            "pages": (total + limit - 1) // limit,
            "has_next": skip + limit < total,
            "has_prev": skip > 0
        }
        
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        raise HTTPException(status_code=500, detail="Error fetching users")
    
@router.get("/me", response_model=UserProfileResponse)
async def get_own_profile(
    current_user=Depends(get_current_user)
):
    """
    Get the profile of the currently authenticated user.
    Returns sanitized data without internal fields.
    """
    return {
        "name": current_user.name,
        "surname": current_user.surname,
        "phone_number": current_user.phone_number,
        "is_admin": current_user.is_admin,
    }

 

@router.get("/{user_id:int}")
@cached(ttl=300, key_prefix="user")
async def get_user_detail(user_id: int, db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    """
    Get user details by ID. Admin-only endpoint.
    """
    user = await get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    logger.info(f"Admin {admin.name} viewing user details: {user.name} ({user.phone_number})")
    u = user.__dict__.copy()
    u.pop("id", None)
    u.pop("created_at", None)
    u.pop("updated_at", None)
    return u

@router.put("/{user_id:int}")
async def update_user_endpoint(
    user_id: int, 
    user_update: UserUpdate, 
    db: AsyncSession = Depends(get_db), 
    admin=Depends(get_current_admin)
):
    """
    Update a user by ID. Admin-only endpoint.
    """
    user = await get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Prevent admin from updating themselves through this endpoint
    if user.id == admin.id:
        logger.warning(f"Admin {admin.name} attempted to update themselves through admin endpoint")
        raise HTTPException(
            status_code=400, 
            detail="You cannot update your own account through this endpoint"
        )
    logger.info(f"Admin {admin.name} updating user: {user.name} ({user.phone_number})")

    # Prevent duplicate phone numbers (only when phone provided and changed)
    if 'phone_number' in user_update.model_dump(exclude_unset=True):
        new_phone = (user_update.phone_number or '').strip()
        if not new_phone:
            # Treat empty string as not provided; don't overwrite existing phone
            pass
        elif new_phone != (user.phone_number or ''):
            existing = await get_user_by_phone_number(db, new_phone)
            if existing and existing.id != user.id:
                raise HTTPException(status_code=400, detail="Phone number already in use")

    try:
        # When phone_number was sent as empty, avoid overwriting by removing it
        payload_admin = user_update.model_dump(exclude_unset=True)
        if 'phone_number' in payload_admin and not (payload_admin.get('phone_number') or '').strip():
            payload_admin.pop('phone_number', None)
        updated_user = await update_user(db, user, UserUpdate(**payload_admin))
    except IntegrityError as ie:
        await db.rollback()
        msg = str(getattr(ie, 'orig', ie))
        if 'phone_number' in msg or 'UNIQUE' in msg.upper():
            raise HTTPException(status_code=400, detail="Phone number already in use")
        raise HTTPException(status_code=400, detail="Update failed")
    
    # Clear cache after updating user
    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("users:")
    await invalidate_cache_pattern(f"user:{user_id}:")
    
    u = updated_user.__dict__.copy()
    u.pop("id", None)
    u.pop("created_at", None)
    u.pop("updated_at", None)
    return u

@router.delete("/{user_id:int}")
async def delete_user_endpoint(user_id: int, db: AsyncSession = Depends(get_db), admin=Depends(get_current_admin)):
    """
    Delete a user by ID. Admin-only endpoint.
    """
    user = await get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent admin from deleting themselves
    if user.id == admin.id:
        logger.warning(f"Admin {admin.name} attempted to delete themselves")
        raise HTTPException(
            status_code=400, 
            detail="You cannot delete your own account"
        )
    
    logger.info(f"Admin {admin.name} deleting user: {user.name} ({user.phone_number})")
    await delete_user(db, user)
    
    # Clear cache after deleting user
    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("users:")
    await invalidate_cache_pattern(f"user:{user_id}:")
    
    return {"msg": "User deleted"} 

@router.put("/me", response_model=UserProfileResponse)
async def update_own_profile(
    user_update: UserSelfUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user)
):
    """
    Update the profile of the currently authenticated user.
    Users cannot change their own admin status (is_admin).
    """
    # Disallow changing own admin status
    if user_update.is_admin is not None and user_update.is_admin != current_user.is_admin:
        raise HTTPException(status_code=403, detail="You cannot change your admin status")

    # Handle optional password change
    if user_update.new_password is not None:
        # Verify current password before changing
        from app.auth.password import verify_password
        if not verify_password(user_update.current_password or "", current_user.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        # Update password (stored as-is per current policy)
        await update_user_password(db, current_user.name, user_update.new_password)

    # Sanitize payload to exclude is_admin and password fields
    payload = user_update.model_dump(exclude_unset=True)
    payload.pop("is_admin", None)
    payload.pop("current_password", None)
    payload.pop("new_password", None)
    payload.pop("confirm_new_password", None)
    sanitized_update = UserUpdate(**payload)

    # Prevent duplicate phone numbers (only when phone provided and changed)
    if 'phone_number' in payload:
        new_phone = (payload.get('phone_number') or '').strip()
        if not new_phone:
            # Don't overwrite existing phone with empty string
            sanitized_payload = sanitized_update.model_dump(exclude_unset=True)
            sanitized_payload.pop('phone_number', None)
            sanitized_update = UserUpdate(**sanitized_payload)
        elif new_phone != (current_user.phone_number or ''):
            existing = await get_user_by_phone_number(db, new_phone)
            if existing and existing.id != current_user.id:
                raise HTTPException(status_code=400, detail="Phone number already in use")

    try:
        updated_user = await update_user(db, current_user, sanitized_update)
    except IntegrityError as ie:
        await db.rollback()
        msg = str(getattr(ie, 'orig', ie))
        if 'phone_number' in msg or 'UNIQUE' in msg.upper():
            raise HTTPException(status_code=400, detail="Phone number already in use")
        raise HTTPException(status_code=400, detail="Update failed")

    # Invalidate related caches
    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("users:")
    await invalidate_cache_pattern(f"user:{current_user.id}:")

    # Return sanitized profile
    return {
        "name": updated_user.name,
        "surname": updated_user.surname,
        "phone_number": updated_user.phone_number,
        "is_admin": updated_user.is_admin,
    }