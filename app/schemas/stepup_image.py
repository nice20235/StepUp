from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class StepUpImageBase(BaseModel):
    image_path: str = Field(..., description="Path to the image file")
    is_primary: bool = Field(default=False, description="Whether this is the primary image")
    alt_text: Optional[str] = Field(None, description="Alt text for the image")
    order_index: int = Field(default=0, description="Display order of the image")

class StepUpImageCreate(StepUpImageBase):
    pass

class StepUpImageUpdate(BaseModel):
    image_path: Optional[str] = None
    is_primary: Optional[bool] = None
    alt_text: Optional[str] = None
    order_index: Optional[int] = None

class StepUpImageInDB(StepUpImageBase):
    id: int
    slipper_id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class StepUpImageResponse(StepUpImageInDB):
    pass
