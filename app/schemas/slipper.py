from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from .category import CategoryBase, CategoryCreate, CategoryUpdate, CategoryInDB
from .slipper_image import SlipperImageResponse


# Slipper schemas
class SlipperBase(BaseModel):
    name: str = Field(
        ..., 
        description="Slipper name", 
        min_length=1, 
        max_length=100,
        example="Cozy Home Slipper"
    )
    size: str = Field(
        ..., 
        description="Slipper size (e.g., 38, 42, M, L)", 
        min_length=1,
        max_length=20,
        example="42"
    )
    price: float = Field(
        ..., 
        description="Slipper price", 
        gt=0,
        example=25.99
    )
    quantity: int = Field(
        ...,
        description="Available quantity in stock",
        ge=0,
        example=50
    )
    category_id: Optional[int] = Field(
        None, 
        description="Category ID",
        example=1
    )


class SlipperCreate(SlipperBase):
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Cozy Home Slipper",
                "size": "42",
                "price": 25.99,
                "quantity": 50,
                "category_id": 1
            }
        }


class SlipperUpdate(BaseModel):
    image: Optional[str] = Field(None, description="Image URL or path", min_length=1, max_length=255)
    name: Optional[str] = Field(None, description="Slipper name", min_length=1, max_length=100)
    size: Optional[str] = Field(None, description="Slipper size (e.g., 38, 42, M, L)", min_length=1, max_length=20)
    price: Optional[float] = Field(None, description="Slipper price", gt=0)
    quantity: Optional[int] = Field(None, description="Available quantity in stock", ge=0)
    category_id: Optional[int] = Field(None, description="Category ID")


class SlipperInDB(SlipperBase):
    id: int = Field(..., description="Slipper ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    category: Optional[CategoryInDB] = Field(None, description="Associated category")
    images: List[SlipperImageResponse] = Field(default=[], description="Slipper images")

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class SlipperResponse(SlipperInDB):
    pass


class SlipperList(BaseModel):
    slippers: List[SlipperResponse] = Field(..., description="List of slippers")
    total: int = Field(..., description="Total number of slippers")
    skip: int = Field(..., description="Number of slippers skipped")
    limit: int = Field(..., description="Maximum number of slippers returned")


