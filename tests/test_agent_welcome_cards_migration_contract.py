from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[1]


def test_welcome_cards_are_version_scoped_and_migrated():
    migration = (ROOT / "db-prod/V104-add-agent-welcome-cards.sql").read_text(encoding="utf-8")
    model = (ROOT / "app/models/agent.py").read_text(encoding="utf-8")
    schema = (ROOT / "app/schemas/agent.py").read_text(encoding="utf-8")

    assert "ALTER TABLE `ai_agent_versions`" in migration
    assert "`welcome_config` JSON NULL" in migration
    assert "welcome_config = Column(JSON, nullable=True)" in model
    assert "welcome_config: Optional[Dict[str, Any]] = None" in schema
