from pydantic import BaseModel, Field, validator
from app.core.config import settings
from typing import Optional, List
from datetime import datetime

class UserBase(BaseModel):
    name: str = Field(
        ..., 
        description="User's first name", 
        min_length=2, 
        max_length=100,
        example="John"
    )
    surname: str = Field(
        ..., 
        description="User's last name", 
        min_length=2, 
        max_length=100,
        example="Doe"
    )
    phone_number: str = Field(
        ..., 
        description="User's phone number", 
        # + plus at least 6 digits => total length >=7
        min_length=7, 
        max_length=20,
        example="+79991234567"
    )
    is_admin: bool = Field(
        default=False, 
        description="Whether user is an admin",
        example=False
    )

class UserCreate(UserBase):
    password: str = Field(
        ..., 
        description="User's password", 
        min_length=8,
        example="securepassword123"
    )
    confirm_password: str = Field(
        ..., 
        description="Confirm password", 
        example="securepassword123"
    )
    
    @validator('confirm_password')
    def passwords_match(cls, v, values):
        if 'password' in values and v != values['password']:
            raise ValueError('Passwords do not match')
        return v
    
    @validator('phone_number')
    def validate_phone_number(cls, v):
        if not v:
            raise ValueError('Phone number required')
        v = v.strip()
        if not v.startswith('+'):
            raise ValueError('Phone number must start with +')
        body = v[1:]
        if not body.isdigit():
            raise ValueError('Phone number must contain only digits after +')
        if len(body) < 6 or len(body) > 15:
            raise ValueError('Phone number length invalid (6-15 digits after +)')
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "John",
                "surname": "Doe",
                "phone_number": "+79991234567",
                "password": "securepassword123",
                "confirm_password": "securepassword123",
                "is_admin": False
            }
        }

class UserUpdate(BaseModel):
    name: Optional[str] = Field(
        None, 
        description="User's first name", 
        min_length=2, 
        max_length=100,
        example="John"
    )
    surname: Optional[str] = Field(
        None, 
        description="User's last name", 
        min_length=2, 
        max_length=100,
        example="Doe"
    )
    phone_number: Optional[str] = Field(
        None, 
        description="User's phone number", 
        min_length=7, 
        max_length=20,
        example="+79991234567"
    )
    is_admin: Optional[bool] = Field(
        None, 
        description="Whether user is an admin",
        example=False
    )
    
    @validator('phone_number')
    def validate_phone_number(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v.startswith('+'):
            raise ValueError('Phone number must start with +')
        body = v[1:]
        if not body.isdigit():
            raise ValueError('Phone number must contain only digits after +')
        if len(body) < 6 or len(body) > 15:
            raise ValueError('Phone number length invalid (6-15 digits after +)')
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "John",
                "surname": "Doe",
                "phone_number": "+79991234567",
                "is_admin": False
            }
        }

class UserInDB(UserBase):
    id: int = Field(..., description="User ID", example=1)
    created_at: datetime = Field(..., description="Account creation timestamp", example="2024-01-15T10:30:00Z")
    updated_at: datetime = Field(..., description="Last update timestamp", example="2024-01-15T10:30:00Z")
    
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class UserResponse(UserInDB):
    """User response schema for API endpoints"""
    pass

class UserList(BaseModel):
    """Schema for list of users"""
    users: List[UserResponse] = Field(..., description="List of users")
    total: int = Field(..., description="Total number of users")
    skip: int = Field(..., description="Number of users skipped")
    limit: int = Field(..., description="Maximum number of users returned")

class UserLogin(BaseModel):
    phone_number: str = Field(
        ..., 
        description="User's phone number", 
        example="+998901234567"
    )
    password: str = Field(
        ..., 
        description="User's password", 
        example="securepassword123"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "phone_number": "+998901234567",
                "password": "securepassword123"
            }
        }

class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiration time in minutes")
    user: UserResponse = Field(..., description="User information")

class RefreshTokenRequest(BaseModel):
    refresh_token: Optional[str] = Field(None, description="JWT refresh token (can also be provided in headers)")
    
    class Config:
        # Allow request with empty body when token is in headers
        extra = "ignore"

class ForgotPasswordRequest(BaseModel):
    name: str = Field(
        ..., 
        description="User's login name", 
        min_length=2, 
        max_length=100,
        example="John"
    )
    new_password: str = Field(
        ..., 
        description="New password", 
        min_length=8,
        example="newsecurepassword123"
    )
    confirm_new_password: str = Field(
        ..., 
        description="Confirm new password", 
        example="newsecurepassword123"
    )
    
    @validator('confirm_new_password')
    def passwords_match(cls, v, values):
        if 'new_password' in values and v != values['new_password']:
            raise ValueError('New passwords do not match')
        return v 


class UserSelfUpdate(UserUpdate):
    """Schema for self-profile update including optional password change"""
    current_password: Optional[str] = Field(
        None,
        description="Current password (required when changing password)",
        example="oldpassword123"
    )
    new_password: Optional[str] = Field(
        None,
        description="New password (min 8 chars)",
        min_length=8,
        example="newsecurepassword123"
    )
    confirm_new_password: Optional[str] = Field(
        None,
        description="Confirm new password",
        example="newsecurepassword123"
    )

    @validator('confirm_new_password')
    def self_passwords_match(cls, v, values):
        # Only validate when changing password
        if values.get('new_password') is not None:
            if v != values.get('new_password'):
                raise ValueError('New passwords do not match')
        return v

    @validator('current_password')
    def require_current_when_changing(cls, v, values):
        # If changing password, current_password must be provided
        if values.get('new_password') is not None and not v:
            raise ValueError('Current password is required to change password')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "name": "John",
                "surname": "Doe",
                "phone_number": "+79991234567",
                "current_password": "oldpassword123",
                "new_password": "newsecurepassword123",
                "confirm_new_password": "newsecurepassword123"
            }
        }


class UserProfileResponse(BaseModel):
    """Public user profile returned by /users/me endpoints"""
    name: str
    surname: str
    phone_number: str
    is_admin: bool = False

    class Config:
        json_schema_extra = {
            "example": {
                "name": "John",
                "surname": "Doe",
                "phone_number": "+79991234567",
                "is_admin": False
            }
        }