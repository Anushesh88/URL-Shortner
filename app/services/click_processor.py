from collections import Counter
from datetime import datetime, timezone
import json
import logging
from typing import List, Optional
from sqlalchemy import select, update

from app.config import settings
from app.models.database import AsyncSessionLocal
from app.models.click import ClickAnalytics
from app.models.url import URL
from app.services.cache import cache_manager

logger = logging.getLogger("click_processor")


class ClickProcessor:
    """Decouples click analytics from the redirect hot path.
    
    Instead of performing a blocking database UPDATE on every single redirect,
    redirect handlers push an event to a Redis queue. A worker drains this queue
    in batches to eliminate write contention and row locking.
    """

    def __init__(self, queue_key: Optional[str] = None):
        self.queue_key = queue_key or settings.CLICK_QUEUE_KEY

    async def enqueue_click(
        self,
        short_code: str,
        referrer: Optional[str] = None,
        user_agent: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> bool:
        """Pushes a click event to the Redis queue non-blockingly."""
        event = {
            "short_code": short_code,
            "clicked_at": datetime.now(timezone.utc).isoformat(),
            "referrer": referrer,
            "user_agent": user_agent,
            "client_ip": client_ip,
        }
        try:
            client = await cache_manager.get_client()
            await client.lpush(self.queue_key, json.dumps(event))
            return True
        except Exception as e:
            logger.error(f"Failed to enqueue click event for {short_code}: {e}")
            return False

    async def process_batch(self, batch_size: Optional[int] = None) -> int:
        """Drains a batch of click events from Redis and commits aggregated updates to DB."""
        size = batch_size or settings.CLICK_BATCH_SIZE
        client = await cache_manager.get_client()

        # Pop up to batch_size items atomically
        raw_events: List[str] = []
        for _ in range(size):
            item = await client.rpop(self.queue_key)
            if not item:
                break
            raw_events.append(item)

        if not raw_events:
            return 0

        # Parse events
        events = []
        code_counts = Counter()
        for raw in raw_events:
            try:
                data = json.loads(raw)
                events.append(data)
                code_counts[data["short_code"]] += 1
            except Exception as e:
                logger.warning(f"Skipping malformed click event: {e}")

        # Batch write to database
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # 1. Insert granular click records
                for ev in events:
                    dt = datetime.fromisoformat(ev["clicked_at"])
                    click_obj = ClickAnalytics(
                        short_code=ev["short_code"],
                        clicked_at=dt,
                        referrer=ev.get("referrer"),
                        user_agent=ev.get("user_agent"),
                        client_ip=ev.get("client_ip"),
                    )
                    session.add(click_obj)

                # 2. Increment aggregated click counts on URLs
                for code, increment in code_counts.items():
                    stmt = (
                        update(URL)
                        .where(URL.short_code == code)
                        .values(click_count=URL.click_count + increment)
                    )
                    await session.execute(stmt)

        logger.info(
            f"Successfully processed {len(events)} click events across {len(code_counts)} short codes."
        )
        return len(events)


click_processor = ClickProcessor()
