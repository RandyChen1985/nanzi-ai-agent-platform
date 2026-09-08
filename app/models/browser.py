from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, String, Text

from app.core.orm import Base


class BrowserProfile(Base):
    __tablename__ = "browser_profiles"

    id = Column(String(36), primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    display_name = Column(String(120), nullable=False)
    encrypted_storage_ref = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="active", index=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    id = Column(String(36), primary_key=True)
    profile_id = Column(String(36), nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    attached_conversation_id = Column(String(64), nullable=True, index=True)
    current_url = Column(Text, nullable=True)
    page_title = Column(String(500), nullable=True)
    approval_mode = Column(String(20), nullable=False, default="autopilot")
    status = Column(String(20), nullable=False, default="active", index=True)
    viewer_token_hash = Column(String(128), nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
