from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, Column, DateTime, String, Text, func
from app.models.database import Base


class URL(Base):
    __tablename__ = "urls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    short_code = Column(String(10), unique=True, index=True, nullable=False)
    long_url = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    click_count = Column(BigInteger, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        now = datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None:
            return self.expires_at.replace(tzinfo=timezone.utc) < now
        return self.expires_at < now

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "short_code": self.short_code,
            "long_url": self.long_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "click_count": self.click_count,
            "is_active": self.is_active,
        }
