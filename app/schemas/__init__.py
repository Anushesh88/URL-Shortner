"""Pydantic schemas package."""

from app.schemas.url import ShortenRequest, ShortenResponse, URLDetailResponse
from app.schemas.analytics import URLAnalyticsResponse, ClickEventSchema

__all__ = [
    "ShortenRequest",
    "ShortenResponse",
    "URLDetailResponse",
    "URLAnalyticsResponse",
    "ClickEventSchema",
]
