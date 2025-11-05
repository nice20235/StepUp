from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from .category import CategoryBase, CategoryCreate, CategoryUpdate, CategoryInDB
from .stepup_image import StepUpImageResponse


# StepUp schemas
class StepUpBase(BaseModel):
    name: str = Field(
        ..., 
        description="StepUp name", 
        min_length=1, 
        max_length=100,
        example="Cozy Home StepUp"
    )
    size: str = Field(
        ..., 
        description="StepUp size (e.g., 38, 42, M, L)", 
        min_length=1,
        max_length=20,
        example="42"
    )
    price: float = Field(
        ..., 
        description="StepUp price", 
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


class StepUpCreate(StepUpBase):
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Cozy Home StepUp",
                "size": "42",
                "price": 25.99,
                "quantity": 50,
                "category_id": 1
            }
        }


class StepUpUpdate(BaseModel):
    image: Optional[str] = Field(None, description="Image URL or path", min_length=1, max_length=255)
    name: Optional[str] = Field(None, description="StepUp name", min_length=1, max_length=100)
    size: Optional[str] = Field(None, description="StepUp size (e.g., 38, 42, M, L)", min_length=1, max_length=20)
    price: Optional[float] = Field(None, description="StepUp price", gt=0)
    quantity: Optional[int] = Field(None, description="Available quantity in stock", ge=0)
    category_id: Optional[int] = Field(None, description="Category ID")


class StepUpInDB(StepUpBase):
    id: int = Field(..., description="StepUp ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    category: Optional[CategoryInDB] = Field(None, description="Associated category")
    images: List[StepUpImageResponse] = Field(default=[], description="StepUp images")

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class StepUpResponse(StepUpInDB):
    pass


class StepUpList(BaseModel):
    stepups: List[StepUpResponse] = Field(..., description="List of stepups")
    total: int = Field(..., description="Total number of stepups")
    skip: int = Field(..., description="Number of stepups skipped")
    limit: int = Field(..., description="Maximum number of stepups returned")
