from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Dict
import os
from uuid import uuid4
import logging
from app.db.database import get_db
from app.auth.dependencies import get_current_admin
from app.core.cache import cached
from app.crud.stepup import (
    get_slipper,
    get_slippers,
    update_slipper,
    delete_slipper,
    get_category,
)


# Set up logging
logger = logging.getLogger(__name__)


router = APIRouter()


# ---------------------------
# Helpers (local to endpoint)
# ---------------------------
async def _fetch_images_by_stepup(
    db: AsyncSession, stepup_ids: List[int]
) -> Dict[int, List[dict]]:
    """Batch load images for provided stepup IDs and group them by stepup_id.

    Returns dict: { stepup_id: [ {id, image_path, is_primary, alt_text, order_index}, ... ] }
    """
    if not stepup_ids:
        return {}
    from sqlalchemy import select, asc
    from app.models.stepup import StepUpImage

    rows = await db.execute(
        select(StepUpImage)
        .where(StepUpImage.slipper_id.in_(stepup_ids))
        .order_by(asc(StepUpImage.slipper_id), asc(StepUpImage.order_index), asc(StepUpImage.id))
    )
    images_by_stepup: Dict[int, List[dict]] = {}
    for img in rows.scalars().all():
        images_by_stepup.setdefault(int(img.slipper_id), []).append(
            {
                "id": img.id,
                "image_path": img.image_path,
                "is_primary": img.is_primary,
                "alt_text": img.alt_text,
                "order_index": img.order_index,
            }
        )
    return images_by_stepup


def _serialize_stepup(stepup, *, images: Optional[List[dict]] = None) -> dict:
    """Convert StepUp ORM object + optional images to API dict."""
    return {
        "id": stepup.id,
        "name": stepup.name,
        "size": stepup.size,
        "price": stepup.price,
        "quantity": stepup.quantity,
        "category_id": stepup.category_id,
        "category_name": stepup.category.name if stepup.category else None,
        "image": stepup.image,
        **({"images": images} if images is not None else {}),
        "is_available": stepup.quantity > 0,
    }


@router.get("/")
@cached(ttl=300, key_prefix="stepups")
async def read_slippers(
    skip: int = Query(0, ge=0, description="Skip items for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Limit items per page"),
    category_id: Optional[int] = Query(None, description="Filter by category ID"),
    search: Optional[str] = Query(None, description="Search in name and size"),
    sort: str = Query(
        "id_desc",
        description="Sort order: id_asc,id_desc,name_asc,name_desc,price_asc,price_desc,created_asc,created_desc",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Get all stepups with filtering, pagination, search and sorting."""
    try:
        slippers, total = await get_slippers(
            db,
            skip=skip,
            limit=limit,
            category_id=category_id,
            search=search,
            sort=sort,
        )
        # Batch-load images and serialize
        images_by_stepup = await _fetch_images_by_stepup(db, [s.id for s in slippers])
        items = [_serialize_stepup(s, images=images_by_stepup.get(int(s.id), [])) for s in slippers]

        return {
            "items": items,
            "total": total,
            "page": (skip // limit) + 1,
            "pages": (total + limit - 1) // limit,
            "has_next": skip + limit < total,
            "has_prev": skip > 0,
            "sort": sort,
        }
    except Exception as e:
        logger.error(f"Error fetching stepups: {e}")
        raise HTTPException(status_code=500, detail="Error fetching stepups")


@router.get("/{slipper_id}")
@cached(ttl=600, key_prefix="stepup")
async def read_slipper(
    slipper_id: int,
    include_images: bool = Query(False, description="Include stepup images"),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific stepup by ID with optional image loading."""
    try:
        stepup = await get_slipper(db, slipper_id=slipper_id, load_images=include_images)
        if stepup is None:
            raise HTTPException(status_code=404, detail="StepUp not found")
        # Serialize, include images if requested
        images = None
        if include_images and hasattr(stepup, "images"):
            images = [
                {
                    "id": img.id,
                    "image_path": img.image_path,
                    "is_primary": img.is_primary,
                    "alt_text": img.alt_text,
                    "order_index": img.order_index,
                }
                for img in stepup.images
            ]
        base = _serialize_stepup(stepup, images=images)
        base.update(
            {
                "created_at": stepup.created_at.isoformat(),
                "updated_at": stepup.updated_at.isoformat() if stepup.updated_at else None,
            }
        )
        return base
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching stepup {slipper_id}: {e}")
        raise HTTPException(status_code=500, detail="Error fetching stepup")




from app.schemas.stepup import StepUpCreate, StepUpUpdate

@router.post("/", summary="Создать stepup (без картинки)")
async def create_new_slipper(
    slipper: StepUpCreate,
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    """
    Создать новый stepup (admin only) через JSON. Картинку загружать отдельным запросом.
    """
    # Проверяем категорию
    if slipper.category_id:
        category = await get_category(db, category_id=slipper.category_id)
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")

    from app.models.stepup import StepUp
    db_slipper = StepUp(
        name=slipper.name,
        size=slipper.size,
        price=slipper.price,
        quantity=slipper.quantity,
        category_id=slipper.category_id,
        image="",
    )
    db.add(db_slipper)
    await db.commit()
    await db.refresh(db_slipper)
    
    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("stepups:")
    
    return {
        "id": db_slipper.id,
        "name": db_slipper.name,
        "size": db_slipper.size,
        "price": db_slipper.price,
        "quantity": db_slipper.quantity,
        "category_id": db_slipper.category_id,
        "image": db_slipper.image,
    }


@router.put("/{slipper_id}")
async def update_existing_slipper(
    slipper_id: int,
    slipper: StepUpUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    """
    Update a stepup item (Admin only).
    """
    # Load existing stepup
    existing = await get_slipper(db, slipper_id=slipper_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="StepUp not found")
    # Build update model from provided fields
    # Update with provided partial fields
    db_slipper = await update_slipper(db, existing, slipper)
    
    # Clear cache after updating
    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("stepups:")
    await invalidate_cache_pattern(f"stepup:{slipper_id}:")
    
    return {
        "id": db_slipper.id,
        "name": db_slipper.name,
        "size": db_slipper.size,
        "price": db_slipper.price,
        "quantity": db_slipper.quantity,
        "category_id": db_slipper.category_id,
        "image": db_slipper.image,
    }


@router.delete("/{slipper_id}")
async def delete_existing_slipper(
    slipper_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    """
    Delete a stepup item (Admin only).
    """
    try:
        db_slipper = await get_slipper(db, slipper_id=slipper_id)
        if db_slipper is None:
            raise HTTPException(status_code=404, detail="StepUp not found")

        await delete_slipper(db, db_slipper=db_slipper)

        from app.core.cache import invalidate_cache_pattern

        await invalidate_cache_pattern("stepups:")
        await invalidate_cache_pattern(f"stepup:{slipper_id}:")

        return {"message": "StepUp deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting stepup {slipper_id}: {e}")
        raise HTTPException(status_code=500, detail="Error deleting stepup")


@router.post("/{slipper_id}/upload-images", summary="Загрузить несколько изображений для stepup")
async def upload_slipper_images(
    slipper_id: int,
    images: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    """Upload one or many images for a stepup. First image becomes main image if not set."""
    from app.models.stepup import StepUpImage
    slipper = await get_slipper(db, slipper_id=slipper_id)
    if not slipper:
        raise HTTPException(status_code=404, detail="StepUp not found")

    if len(images) > 10:
        raise HTTPException(status_code=400, detail="Too many images. Maximum 10 images allowed.")

    uploaded_images: List[dict] = []
    first_image_path: Optional[str] = None
    upload_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../static/images"))
    os.makedirs(upload_dir, exist_ok=True)

    for i, image in enumerate(images):
        ext = os.path.splitext(image.filename)[1]
        if ext.lower() not in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
            raise HTTPException(status_code=400, detail=f"Invalid image format for file {image.filename}")

        filename = f"{uuid4().hex}{ext}"
        file_path = os.path.join(upload_dir, filename)
        with open(file_path, "wb") as f:
            f.write(await image.read())
        relative_path = f"/static/images/{filename}"

        slipper_image = StepUpImage(
            slipper_id=slipper_id,
            image_path=relative_path,
            order_index=i,
        )
        db.add(slipper_image)

        if first_image_path is None:
            first_image_path = relative_path

        uploaded_images.append(
            {
                "image_path": relative_path,
                "order_index": i,
            }
        )

    if (not slipper.image) and first_image_path:
        slipper.image = first_image_path
        db.add(slipper)

    await db.commit()

    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("stepups:")
    await invalidate_cache_pattern(f"stepup:{slipper_id}:")

    return {
        "slipper_id": slipper_id,
        "uploaded_images": uploaded_images,
        "total_uploaded": len(uploaded_images),
    }


@router.get("/{slipper_id}/images", summary="Получить все изображения stepup")
async def get_slipper_images(
    slipper_id: int, db: AsyncSession = Depends(get_db)
):
    """Получить все изображения для конкретного stepup."""
    from app.models.stepup import StepUpImage
    from sqlalchemy import select, asc

    slipper = await get_slipper(db, slipper_id=slipper_id)
    if not slipper:
        raise HTTPException(status_code=404, detail="StepUp not found")

    result = await db.execute(
        select(StepUpImage)
        .where(StepUpImage.slipper_id == slipper_id)
        .order_by(asc(StepUpImage.order_index))
    )
    images = result.scalars().all()

    return {
        "slipper_id": slipper_id,
        "images": [
            {
                "id": img.id,
                "image_path": img.image_path,
                "is_primary": img.is_primary,
                "alt_text": img.alt_text,
                "order_index": img.order_index,
                "created_at": img.created_at,
            }
            for img in images
        ],
        "total_images": len(images),
    }


@router.delete("/{slipper_id}/images/{image_id}", summary="Удалить изображение stepup")
async def delete_slipper_image(
    slipper_id: int,
    image_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    """Удалить конкретное изображение stepup."""
    from app.models.stepup import StepUpImage
    from sqlalchemy import select

    result = await db.execute(
        select(StepUpImage)
        .where(StepUpImage.id == image_id)
        .where(StepUpImage.slipper_id == slipper_id)
    )
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        file_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../", image.image_path.lstrip("/"))
        )
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.warning(f"Failed to delete physical file {image.image_path}: {e}")

    await db.delete(image)
    await db.commit()

    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("stepups:")
    await invalidate_cache_pattern(f"stepup:{slipper_id}:")

    return {"message": "Image deleted successfully", "deleted_image_id": image_id}
