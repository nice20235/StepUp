from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

# Category schemas
class CategoryBase(BaseModel):
    name: str = Field(
        ..., 
        description="Category name", 
        min_length=1, 
        max_length=100,
        example="Men"
    )
    description: Optional[str] = Field(
        None, 
        description="Category description", 
        max_length=255,
        example="Men's slippers"
    )
    is_active: bool = Field(
        default=True, 
        description="Whether category is active",
        example=True
    )

class CategoryCreate(CategoryBase):
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Men",
                "description": "Men's slippers",
                "is_active": True
            }
        }

class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(
        None, 
        description="Category name", 
        min_length=1, 
        max_length=100,
        example="Men"
    )
    description: Optional[str] = Field(
        None, 
        description="Category description", 
        max_length=255,
        example="Men's slippers"
    )
    is_active: Optional[bool] = Field(
        None, 
        description="Whether category is active",
        example=True
    )

class CategoryInDB(CategoryBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
