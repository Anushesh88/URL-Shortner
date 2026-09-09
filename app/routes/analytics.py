from collections import Counter
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_read_db
from app.models.click import ClickAnalytics
from app.models.url import URL
from app.schemas.analytics import ClickEventSchema, URLAnalyticsResponse

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get(
    "/{code}",
    response_model=URLAnalyticsResponse,
    summary="Get analytics for a shortened URL",
)
async def get_url_analytics(
    code: str,
    read_db: AsyncSession = Depends(get_read_db),
):
    """Retrieves click performance, referral sources, and timeline data."""
    # 1. Fetch URL metadata
    stmt = select(URL).where(URL.short_code == code)
    result = await read_db.execute(stmt)
    url_record = result.scalar_one_or_none()

    if not url_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="URL not found.",
        )

    # 2. Query recent click events
    clicks_stmt = (
        select(ClickAnalytics)
        .where(ClickAnalytics.short_code == code)
        .order_by(desc(ClickAnalytics.clicked_at))
        .limit(100)
    )
    clicks_result = await read_db.execute(clicks_stmt)
    clicks = clicks_result.scalars().all()

    # 3. Aggregate metrics
    now = datetime.now(timezone.utc)
    one_day_ago = now - timedelta(hours=24)

    clicks_24h = 0
    referrer_counts = Counter()
    recent_events = []

    for c in clicks:
        click_dt = c.clicked_at
        if click_dt.tzinfo is None:
            click_dt = click_dt.replace(tzinfo=timezone.utc)

        if click_dt >= one_day_ago:
            clicks_24h += 1

        ref = c.referrer or "Direct / None"
        referrer_counts[ref] += 1

        if len(recent_events) < 20:
            recent_events.append(
                ClickEventSchema(
                    short_code=c.short_code,
                    clicked_at=c.clicked_at,
                    referrer=c.referrer,
                    user_agent=c.user_agent,
                    client_ip=c.client_ip,
                )
            )

    return URLAnalyticsResponse(
        short_code=url_record.short_code,
        long_url=url_record.long_url,
        created_at=url_record.created_at,
        total_clicks=url_record.click_count,
        clicks_last_24h=clicks_24h,
        top_referrers=dict(referrer_counts.most_common(5)),
        recent_clicks=recent_events,
    )
