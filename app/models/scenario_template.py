from datetime import datetime

from sqlalchemy import Column, DateTime, JSON, String, Text, UniqueConstraint

from app.core.orm import Base


class ScenarioTemplateInstance(Base):
    """A concrete business delivery created from a scenario template."""

    __tablename__ = "ai_agent_scenario_instances"
    __table_args__ = (
        UniqueConstraint("template_id", "agent_id", name="uix_scenario_template_agent"),
    )

    id = Column(String(36), primary_key=True)
    template_id = Column(String(100), nullable=False, index=True)
    template_name = Column(String(100), nullable=False)
    agent_id = Column(String(36), nullable=False, index=True)
    agent_name = Column(String(100), nullable=False)
    display_name = Column(String(100), nullable=False)
    owner = Column(String(100), nullable=True)
    status = Column(String(32), nullable=False, default="installed")
    resource_bindings = Column(JSON, nullable=True)
    missing_resources = Column(JSON, nullable=True)
    deliverables = Column(JSON, nullable=True)
    acceptance_criteria = Column(JSON, nullable=True)
    next_steps = Column(JSON, nullable=True)
    created_by = Column(String(64), nullable=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ScenarioTemplateInstallRun(Base):
    """Audit trail for every prechecked install attempt."""

    __tablename__ = "ai_agent_scenario_install_runs"

    id = Column(String(36), primary_key=True)
    instance_id = Column(String(36), nullable=True, index=True)
    template_id = Column(String(100), nullable=False, index=True)
    agent_id = Column(String(36), nullable=True, index=True)
    status = Column(String(32), nullable=False)
    precheck_result = Column(JSON, nullable=True)
    install_result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
