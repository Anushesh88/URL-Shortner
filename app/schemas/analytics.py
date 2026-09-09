from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class ClickEventSchema(BaseModel):
    short_code: str
    clicked_at: datetime
    referrer: Optional[str] = None
    user_agent: Optional[str] = None
    client_ip: Optional[str] = None


class URLAnalyticsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    short_code: str
    long_url: str
    created_at: datetime
    total_clicks: int
    clicks_last_24h: int
    top_referrers: Dict[str, int]
    recent_clicks: List[ClickEventSchema]
