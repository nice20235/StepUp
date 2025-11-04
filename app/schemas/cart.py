from pydantic import BaseModel, Field
from typing import List, Optional

class CartItemCreate(BaseModel):
    slipper_id: int = Field(..., ge=1)
    quantity: int = Field(1, ge=1, le=999)

class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=0, le=999)  # quantity 0 => remove

class CartItemOut(BaseModel):
    id: int
    slipper_id: int
    quantity: int
    name: Optional[str] = None
    price: Optional[float] = None
    total_price: Optional[float] = None

    class Config:
        from_attributes = True

class CartOut(BaseModel):
    id: int
    items: List[CartItemOut]
    total_items: int
    total_quantity: int
    total_amount: float

    class Config:
        from_attributes = True


class CartTotalOut(BaseModel):
    total_items: int = Field(..., description="Number of distinct items in cart")
    total_quantity: int = Field(..., description="Sum of quantities across items")
    total_amount: float = Field(..., description="Sum of price*quantity for all items")


