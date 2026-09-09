from datetime import datetime, timezone
from sqlalchemy import BigInteger, Column, DateTime, Integer, String, func
from app.models.database import Base


class ClickAnalytics(Base):
    __tablename__ = "click_analytics"

    id = Column(Integer().with_variant(BigInteger, "postgresql"), primary_key=True, autoincrement=True)
    short_code = Column(String(10), index=True, nullable=False)
    clicked_at = Column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)
    referrer = Column(String(255), nullable=True)
    user_agent = Column(String(255), nullable=True)
    client_ip = Column(String(64), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "short_code": self.short_code,
            "clicked_at": self.clicked_at.isoformat() if self.clicked_at else None,
            "referrer": self.referrer,
            "user_agent": self.user_agent,
            "client_ip": self.client_ip,
        }
