from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy import func, and_, or_
from app.models.slipper import Slipper, Category
from app.schemas.slipper import SlipperCreate, SlipperUpdate
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


# Slipper CRUD operations
async def get_slipper(db: AsyncSession, slipper_id: int, load_images: bool = False):
	"""Get slipper by ID with optional image loading"""
	query = select(Slipper).options(joinedload(Slipper.category))
	
	if load_images:
		query = query.options(selectinload(Slipper.images))
	
	result = await db.execute(query.where(Slipper.id == slipper_id))
	return result.scalar_one_or_none()


async def get_slippers(
	db: AsyncSession,
	skip: int = 0,
	limit: int = 100,
	category_id: Optional[int] = None,
	search: Optional[str] = None,
	sort: str = "name_asc"
) -> Tuple[List[Slipper], int]:
	"""Get slippers with pagination and filters - optimized"""
	# Build base query with efficient loading
	query = select(Slipper).options(joinedload(Slipper.category))
	conditions = []
	
	# Apply filters
	if category_id:
		conditions.append(Slipper.category_id == category_id)
	
	if search:
		conditions.append(
			or_(
				Slipper.name.ilike(f"%{search}%"),
				Slipper.size.ilike(f"%{search}%")
			)
		)
	
	if conditions:
		query = query.where(and_(*conditions))
	
	# Sorting options
	sort_map = {
		"id_asc": Slipper.id.asc(),
		"id_desc": Slipper.id.desc(),
		"name_asc": Slipper.name.asc(),
		"name_desc": Slipper.name.desc(),
		"price_asc": Slipper.price.asc(),
		"price_desc": Slipper.price.desc(),
		"created_asc": Slipper.created_at.asc(),
		"created_desc": Slipper.created_at.desc(),
	}
	order_clause = sort_map.get(sort, Slipper.name.asc())
	query = query.order_by(order_clause)
	
	# Sequential execution to avoid SQLite concurrent operations
	count_query = select(func.count()).select_from(query.subquery())
	count_result = await db.execute(count_query)
	total = count_result.scalar() or 0

	data_result = await db.execute(query.offset(skip).limit(limit))
	slippers = data_result.scalars().all()
	
	return slippers, total


async def create_slipper(db: AsyncSession, slipper_data: dict):
	"""Create slipper - optimized"""
	db_slipper = Slipper(**slipper_data)
	db.add(db_slipper)
	await db.commit()
	await db.refresh(db_slipper)
	
	logger.info(f"Created slipper with ID: {db_slipper.id}")
	return db_slipper


async def update_slipper(db: AsyncSession, db_slipper: Slipper, slipper_update: SlipperUpdate):
	"""Update slipper - optimized"""
	for field, value in slipper_update.model_dump(exclude_unset=True).items():
		setattr(db_slipper, field, value)
	
	db.add(db_slipper)
	await db.commit()
	await db.refresh(db_slipper)
	
	logger.info(f"Updated slipper with ID: {db_slipper.id}")
	return db_slipper


async def delete_slipper(db: AsyncSession, db_slipper: Slipper):
	await db.delete(db_slipper)
	await db.commit()


