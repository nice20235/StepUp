from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class CartItemCreate(BaseModel):
    """Internal schema used by cart CRUD layer.

    Field name `slipper_id` matches DB model and existing logic.
    """

    slipper_id: int = Field(..., ge=1)
    quantity: int = Field(1, ge=1, le=999)


class CartAddItemRequest(BaseModel):
    """Public request schema for POST /cart/items.

    Uses `product_id` as in the external API contract.
    """

    product_id: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1, le=999)

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


class CartItemPublic(BaseModel):
    """Public cart item representation for API responses.

    All monetary values are in UZS.
    """

    product_id: int
    name: str
    price: int  # price per unit in UZS
    quantity: int
    subtotal: int  # price * quantity in UZS


class CartPublicData(BaseModel):
    id: str
    items: List[CartItemPublic]
    total_amount: int  # total in UZS
    currency: str = "UZS"
    items_count: int


class CartPublicResponse(BaseModel):
    status: Literal["success"]
    data: CartPublicData


