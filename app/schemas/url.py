from datetime import datetime
import re
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ShortenRequest(BaseModel):
    long_url: str = Field(..., description="The original target URL to shorten")
    custom_code: Optional[str] = Field(None, min_length=3, max_length=20, description="Optional custom alphanumeric alias")
    expires_in_seconds: Optional[int] = Field(None, gt=0, description="Optional expiration TTL in seconds")

    @field_validator("long_url")
    @classmethod
    def validate_long_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL must begin with http:// or https://")
        if len(v) > 2048:
            raise ValueError("URL exceeds maximum length of 2048 characters.")
        return v

    @field_validator("custom_code")
    @classmethod
    def validate_custom_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not re.match(r"^[0-9a-zA-Z_-]+$", v):
            raise ValueError("Custom code may only contain alphanumeric characters, underscores, and dashes.")
        return v


class ShortenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    short_code: str
    short_url: str
    long_url: str
    created_at: datetime
    expires_at: Optional[datetime] = None


class URLDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    short_code: str
    short_url: str
    long_url: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    click_count: int
    is_active: bool
