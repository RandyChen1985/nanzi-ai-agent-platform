"""智能体基础字段的口径：名称必须与 ai_agents.name 的 100 字符列宽一致。"""
import pytest
from pydantic import ValidationError

from app.schemas.agent import AIAgentBase

pytestmark = pytest.mark.no_infrastructure


def test_agent_name_rejects_more_than_column_limit():
    with pytest.raises(ValidationError):
        AIAgentBase(name="a" * 101, display_name="超长名称")

    with pytest.raises(ValidationError):
        AIAgentBase(name="ok", display_name="b" * 101)
