from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.core.orm import Base


class PortalSavedReport(Base):
    __tablename__ = "portal_saved_reports"

    id = Column(String(64), primary_key=True)
    title = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    sql_content = Column(Text, nullable=False)
    dataset_id = Column(BigInteger, nullable=True)
    data_source = Column(String(100), nullable=False)
    original_query = Column(Text, nullable=True)
    mode = Column(String(32), nullable=False, default="static_sql")
    sql_template = Column(Text, nullable=True)
    params_schema = Column(JSON, nullable=True)
    default_params = Column(JSON, nullable=True)
    analysis_mode = Column(String(32), nullable=False, default="manual")
    tags = Column(JSON, nullable=True)
    owner_user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    owner_name = Column(String(100), nullable=True)
    visibility = Column(String(32), nullable=False, default="private")
    status = Column(String(32), nullable=False, default="active")
    last_run_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    shares = relationship(
        "PortalSavedReportShare",
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_portal_saved_reports_owner_updated", "owner_user_id", "updated_at"),
        Index("idx_portal_saved_reports_visibility", "visibility"),
    )


class PortalSavedReportShare(Base):
    __tablename__ = "portal_saved_report_shares"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(String(64), ForeignKey("portal_saved_reports.id"), nullable=False, index=True)
    target_type = Column(String(16), nullable=False)
    target_id = Column(BigInteger, nullable=False, index=True)
    permission = Column(String(16), nullable=False, default="run")
    created_by = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    report = relationship("PortalSavedReport", back_populates="shares")

    __table_args__ = (
        Index("idx_portal_saved_report_shares_target", "target_type", "target_id"),
        Index("ux_portal_saved_report_share_target", "report_id", "target_type", "target_id", unique=True),
    )


class PortalSavedReportUserPref(Base):
    __tablename__ = "portal_saved_report_user_prefs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(String(64), ForeignKey("portal_saved_reports.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    is_favorite = Column(Boolean, nullable=False, default=False)
    pinned_at = Column(DateTime, nullable=True)
    last_viewed_at = Column(DateTime, nullable=True)
    run_count = Column(Integer, nullable=False, default=0)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        Index("ux_portal_saved_report_user_pref", "report_id", "user_id", unique=True),
        Index("idx_portal_saved_report_user_pref_user", "user_id", "pinned_at", "last_run_at"),
    )


class PortalSavedReportRun(Base):
    __tablename__ = "portal_saved_report_runs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(String(64), ForeignKey("portal_saved_reports.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    trigger_type = Column(String(32), nullable=False, default="manual")
    task_id = Column(BigInteger, nullable=True, index=True)
    status = Column(String(16), nullable=False, default="running")
    resolved_params = Column(JSON, nullable=True)
    executed_sql = Column(Text, nullable=True)
    data_source = Column(String(100), nullable=True)
    dataset_name = Column(String(255), nullable=True)
    row_count = Column(Integer, nullable=True)
    snapshot_row_count = Column(Integer, nullable=False, default=0)
    result_snapshot = Column(JSON, nullable=True)
    permission_notice = Column(JSON, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.now, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_portal_saved_report_runs_report_started", "report_id", "started_at"),
        Index("idx_portal_saved_report_runs_user_started", "user_id", "started_at"),
        Index("idx_portal_saved_report_runs_status_started", "status", "started_at"),
    )


class PortalSavedReportSubscription(Base):
    __tablename__ = "portal_saved_report_subscriptions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(String(64), ForeignKey("portal_saved_reports.id"), nullable=False, unique=True, index=True)
    user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    schedule_type = Column(String(16), nullable=False)
    cron_expr = Column(String(64), nullable=False)
    timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    params = Column(JSON, nullable=True)
    ai_analysis_enabled = Column(Boolean, nullable=False, default=True)
    analysis_instruction = Column(Text, nullable=True)
    notify_on_success = Column(Boolean, nullable=False, default=False)
    notify_on_failure = Column(Boolean, nullable=False, default=True)
    external_channels = Column(JSON, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    consecutive_failures = Column(Integer, nullable=False, default=0)
    last_run_id = Column(BigInteger, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class PortalSavedReportDigestDelivery(Base):
    __tablename__ = "portal_saved_report_digest_deliveries"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(BigInteger, ForeignKey("portal_saved_report_runs.id"), nullable=False, index=True)
    subscription_id = Column(BigInteger, ForeignKey("portal_saved_report_subscriptions.id"), nullable=False, index=True)
    channel = Column(String(32), nullable=False)
    digest_payload = Column(JSON, nullable=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    ai_status = Column(String(16), nullable=False, default="disabled")
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index("idx_saved_report_digest_subscription_created", "subscription_id", "created_at"),
    )
