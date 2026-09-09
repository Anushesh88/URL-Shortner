"""API routes package."""

from app.routes.shortener import router as shortener_router
from app.routes.analytics import router as analytics_router
from app.routes.health import router as health_router

__all__ = ["shortener_router", "analytics_router", "health_router"]
