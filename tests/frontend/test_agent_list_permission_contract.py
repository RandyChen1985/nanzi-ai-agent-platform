"""非管理页面「智能体下拉」的接口契约。

`GET /api/portal/agents/` 要求 ``menu:agent_management``，且对管理员返回全部智能体；
而任务调度台（``menu:task_center``）、聊天日志（``menu:chat_logs``）都是可以单独授权
的页面。只授予页面权限时该接口会 403，前端 catch 又是静默的，于是下拉恒定为空。

因此这两处必须使用仅要求登录态的 ``GET /api/portal/agents/allowed``：
- 任务调度台只列「能跑的」智能体（默认不含停用）；
- 聊天日志审计需要筛选历史记录，额外带 ``include_disabled=true``。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
TASK_CENTER = ROOT / "frontend/src/views/TaskCenter.vue"
CHAT_LOGS = ROOT / "frontend/src/views/ChatLogs.vue"
AGENT_API = ROOT / "frontend/src/api/agent.ts"


def test_task_center_uses_allowed_agents_endpoint():
    source = TASK_CENTER.read_text(encoding="utf-8")
    assert "agentApi.listAllowedAgents()" in source
    assert "agentApi.listAgents()" not in source


def test_chat_logs_uses_allowed_agents_including_disabled():
    source = CHAT_LOGS.read_text(encoding="utf-8")
    assert "agentApi.listAllowedAgents({ includeDisabled: true })" in source
    assert "agentApi.listAgents()" not in source


def test_agent_api_exposes_allowed_agents():
    source = AGENT_API.read_text(encoding="utf-8")
    assert "listAllowedAgents:" in source
    assert "'/api/portal/agents/allowed'" in source
