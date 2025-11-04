from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func, and_, or_
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from app.auth.password import verify_password
from typing import Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)

async def get_user(db: AsyncSession, user_id: int, load_orders: bool = False) -> Optional[User]:
    """Get user by ID with optional order loading"""
    query = select(User).where(User.id == user_id)
    if load_orders:
        query = query.options(selectinload(User.orders))
    result = await db.execute(query)
    return result.scalar_one_or_none()

async def get_user_by_name(db: AsyncSession, name: str, load_orders: bool = False) -> Optional[User]:
    """Get user by name with optional order loading"""
    query = select(User).where(User.name == name)
    if load_orders:
        query = query.options(selectinload(User.orders))
    result = await db.execute(query)
    return result.scalar_one_or_none()

async def get_user_by_phone_number(db: AsyncSession, phone_number: str) -> Optional[User]:
    """Get user by phone number - optimized for authentication"""
    result = await db.execute(
        select(User).where(User.phone_number == phone_number)
    )
    return result.scalar_one_or_none()

async def authenticate_user(db: AsyncSession, name: str, password: str) -> Optional[User]:
    """Authenticate user by name and password - optimized query"""
    result = await db.execute(
        select(User).where(User.name == name)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user

async def get_users(
    db: AsyncSession, 
    skip: int = 0, 
    limit: int = 100,
    is_admin: Optional[bool] = None,
    search: Optional[str] = None
) -> Tuple[List[User], int]:
    """Get users with pagination and filters - optimized"""
    # Build base query without loading orders for list view
    query = select(User)
    conditions = []
    
    # Apply filters
    if is_admin is not None:
        conditions.append(User.is_admin == is_admin)
    
    if search:
        conditions.append(
            or_(
                User.name.ilike(f"%{search}%"),
                User.surname.ilike(f"%{search}%"),
                User.phone_number.ilike(f"%{search}%")
            )
        )
    
    if conditions:
        query = query.where(and_(*conditions))
    
    # Order by created_at for consistent results
    query = query.order_by(User.created_at.desc())
    
    # Sequential execution to avoid SQLite concurrent operations
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    data_result = await db.execute(query.offset(skip).limit(limit))
    users = data_result.scalars().all()
    
    return users, total

async def create_user(db: AsyncSession, user: UserCreate) -> User:
    """Create new user - optimized"""
    user_data = user.model_dump()
    password = user_data.pop('password')
    user_data.pop('confirm_password', None)
    
    # Store password directly (no hashing)
    user_data['password_hash'] = password
    
    db_user = User(**user_data)
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    
    logger.info(f"Created user with ID: {db_user.id}")
    return db_user

async def update_user(db: AsyncSession, db_user: User, user_update: UserUpdate, load_orders: bool = False) -> User:
    """Update user with optional order loading (avoids extra SELECT when not needed)."""
    update_data = user_update.model_dump(exclude_unset=True)
    dirty = False
    for field, value in update_data.items():
        if getattr(db_user, field) != value:
            setattr(db_user, field, value)
            dirty = True
    if dirty:
        db.add(db_user)
        await db.commit()
        # refresh only fields changed; full refresh not needed unless relationships requested
        await db.refresh(db_user)
    if load_orders:
        result = await db.execute(
            select(User)
            .options(selectinload(User.orders))
            .where(User.id == db_user.id)
        )
        return result.scalar_one()
    return db_user

async def delete_user(db: AsyncSession, db_user: User) -> bool:
    """Delete user"""
    await db.delete(db_user)
    await db.commit()
    return True

async def promote_to_admin(db: AsyncSession, name: str) -> Optional[User]:
    """Promote user to admin by name"""
    user = await get_user_by_name(db, name)
    if not user:
        return None
    
    user.is_admin = True
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    # Load relationships
    result = await db.execute(
        select(User)
        .options(selectinload(User.orders))
        .where(User.id == user.id)
    )
    return result.scalar_one()

async def update_user_password(db: AsyncSession, name: str, new_password: str, load_orders: bool = False) -> Optional[User]:
    """Update user password by name"""
    user = await get_user_by_name(db, name)
    if not user:
        return None
    
    # Store new password directly (no hashing as per your request)
    user.password_hash = new_password
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if load_orders:
        result = await db.execute(
            select(User)
            .options(selectinload(User.orders))
            .where(User.id == user.id)
        )
        user = result.scalar_one()
    logger.info(f"Password updated for user: {user.name}")
    return user 