from datetime import datetime, timedelta, timezone
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import get_db, get_read_db
from app.models.url import URL
from app.schemas.url import ShortenRequest, ShortenResponse, URLDetailResponse
from app.services.cache import cache_manager
from app.services.click_processor import click_processor
from app.services.encoding import encode
from app.services.id_generator import id_generator
from app.middleware.metrics import cache_operations_total

logger = logging.getLogger("shortener_router")
router = APIRouter(tags=["Shortener"])


@router.post(
    "/shorten",
    response_model=ShortenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a shortened URL",
)
async def shorten_url(
    payload: ShortenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Generates a shortened URL using Twitter Snowflake distributed ID generation and Base62 encoding.
    
    Guarantees collision-free, distributed short codes across multi-node deployments
    without database auto-increment locks.
    """
    now = datetime.now(timezone.utc)
    expires_at = None
    if payload.expires_in_seconds:
        expires_at = now + timedelta(seconds=payload.expires_in_seconds)

    if payload.custom_code:
        # Check custom code availability
        stmt = select(URL).where(URL.short_code == payload.custom_code)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Custom alias '{payload.custom_code}' is already taken.",
            )
        short_code = payload.custom_code
        url_id = id_generator.generate_id()
    else:
        # Generate 64-bit Snowflake ID and encode to Base62
        url_id = id_generator.generate_id()
        short_code = encode(url_id)

    # Persist to Primary Database
    new_url = URL(
        id=url_id,
        short_code=short_code,
        long_url=payload.long_url,
        created_at=now,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(new_url)
    await db.commit()
    await db.refresh(new_url)

    # Populate cache-aside immediately
    url_dict = new_url.to_dict()
    ttl = payload.expires_in_seconds if payload.expires_in_seconds else settings.CACHE_TTL_SECONDS
    await cache_manager.set_url(short_code, url_dict, ttl=ttl)

    base_url = str(request.base_url).rstrip("/")
    short_url = f"{base_url}/{short_code}"

    return ShortenResponse(
        short_code=short_code,
        short_url=short_url,
        long_url=new_url.long_url,
        created_at=new_url.created_at,
        expires_at=new_url.expires_at,
    )


@router.get(
    "/{code}",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    summary="Redirect to original URL",
)
async def redirect_url(
    code: str,
    request: Request,
    read_db: AsyncSession = Depends(get_read_db),
):
    """Redirects client to target URL with cache-aside lookup and async click logging."""
    client_ip = request.client.host if request.client else None
    referrer = request.headers.get("referer")
    user_agent = request.headers.get("user-agent")

    # 1. Check Redis Cache
    cached_data = await cache_manager.get_url(code)
    if cached_data:
        cache_operations_total.labels(operation="get", status="hit").inc()

        # Check expiration in cache
        expires_at_str = cached_data.get("expires_at")
        if expires_at_str:
            exp_dt = datetime.fromisoformat(expires_at_str)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt < datetime.now(timezone.utc):
                await cache_manager.delete_url(code)
                raise HTTPException(status_code=status.HTTP_410_GONE, detail="Short URL has expired.")

        # Non-blocking click enqueue (off hot path)
        await click_processor.enqueue_click(
            short_code=code,
            referrer=referrer,
            user_agent=user_agent,
            client_ip=client_ip,
        )

        response = RedirectResponse(url=cached_data["long_url"], status_code=status.HTTP_302_FOUND)
        response.headers["X-Cache"] = "HIT"
        return response

    cache_operations_total.labels(operation="get", status="miss").inc()

    # 2. Cache Miss: Query Database (Read Replica)
    stmt = select(URL).where(URL.short_code == code, URL.is_active.is_(True))
    result = await read_db.execute(stmt)
    url_record = result.scalar_one_or_none()

    if not url_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short URL not found.")

    if url_record.is_expired():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Short URL has expired.")

    # 3. Populate Redis Cache
    await cache_manager.set_url(code, url_record.to_dict())

    # 4. Enqueue click analytics
    await click_processor.enqueue_click(
        short_code=code,
        referrer=referrer,
        user_agent=user_agent,
        client_ip=client_ip,
    )

    response = RedirectResponse(url=url_record.long_url, status_code=status.HTTP_302_FOUND)
    response.headers["X-Cache"] = "MISS"
    return response


@router.delete(
    "/{code}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate and delete a shortened URL",
)
async def delete_url(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """Deletes/deactivates URL and purges Redis cache key."""
    stmt = select(URL).where(URL.short_code == code, URL.is_active.is_(True))
    result = await db.execute(stmt)
    url_record = result.scalar_one_or_none()

    if not url_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Short URL not found.")

    # Soft delete in DB
    url_record.is_active = False
    await db.commit()

    # Invalidate Redis cache
    await cache_manager.delete_url(code)
    cache_operations_total.labels(operation="delete", status="success").inc()

    return {"message": "URL deleted successfully", "short_code": code}
