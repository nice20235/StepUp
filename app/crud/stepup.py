from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy import func, and_, or_
from app.models.stepup import StepUp, Category
from app.schemas.stepup import StepUpCreate, StepUpUpdate
from app.schemas.category import CategoryCreate, CategoryUpdate
from typing import Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)


# Category CRUD operations
async def get_category(db: AsyncSession, category_id: int):
    result = await db.execute(select(Category).where(Category.id == category_id))
    return result.scalar_one_or_none()


async def get_categories(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Category).offset(skip).limit(limit))
    return result.scalars().all()


async def create_category(db: AsyncSession, category: CategoryCreate):
    db_category = Category(**category.model_dump())
    db.add(db_category)
    await db.commit()
    await db.refresh(db_category)
    return db_category


async def update_category(db: AsyncSession, db_category: Category, category_update: CategoryUpdate):
    for field, value in category_update.model_dump(exclude_unset=True).items():
        setattr(db_category, field, value)
    db.add(db_category)
    await db.commit()
    await db.refresh(db_category)
    return db_category


async def delete_category(db: AsyncSession, db_category: Category):
    await db.delete(db_category)
    await db.commit()


# StepUp CRUD operations (keeps function names for compatibility)
async def get_slipper(db: AsyncSession, slipper_id: int, load_images: bool = False):
    """Get stepup by ID with optional image loading"""
    query = select(StepUp).options(joinedload(StepUp.category))
    
    if load_images:
        query = query.options(selectinload(StepUp.images))
    
    result = await db.execute(query.where(StepUp.id == slipper_id))
    return result.scalar_one_or_none()


async def get_slippers(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    category_id: Optional[int] = None,
    search: Optional[str] = None,
    sort: str = "name_asc"
) -> Tuple[List[StepUp], int]:
    """Get stepups with pagination and filters - optimized"""
    query = select(StepUp).options(joinedload(StepUp.category))
    conditions = []
    
    if category_id:
        conditions.append(StepUp.category_id == category_id)
    
    if search:
        conditions.append(
            or_(
                StepUp.name.ilike(f"%{search}%"),
                StepUp.size.ilike(f"%{search}%")
            )
        )
    
    if conditions:
        query = query.where(and_(*conditions))
    
    sort_map = {
        "id_asc": StepUp.id.asc(),
        "id_desc": StepUp.id.desc(),
        "name_asc": StepUp.name.asc(),
        "name_desc": StepUp.name.desc(),
        "price_asc": StepUp.price.asc(),
        "price_desc": StepUp.price.desc(),
        "created_asc": StepUp.created_at.asc(),
        "created_desc": StepUp.created_at.desc(),
    }
    order_clause = sort_map.get(sort, StepUp.name.asc())
    query = query.order_by(order_clause)
    
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    data_result = await db.execute(query.offset(skip).limit(limit))
    items = data_result.scalars().all()
    
    return items, total


async def create_slipper(db: AsyncSession, slipper_data: dict):
    """Create stepup - optimized"""
    db_slipper = StepUp(**slipper_data)
    db.add(db_slipper)
    await db.commit()
    await db.refresh(db_slipper)
    
    logger.info(f"Created stepup with ID: {db_slipper.id}")
    return db_slipper


async def update_slipper(db: AsyncSession, db_slipper: StepUp, slipper_update: StepUpUpdate):
    """Update stepup - optimized"""
    for field, value in slipper_update.model_dump(exclude_unset=True).items():
        setattr(db_slipper, field, value)
    
    db.add(db_slipper)
    await db.commit()
    await db.refresh(db_slipper)
    
    logger.info(f"Updated stepup with ID: {db_slipper.id}")
    return db_slipper


async def delete_slipper(db: AsyncSession, db_slipper: StepUp):
    await db.delete(db_slipper)
    await db.commit()
