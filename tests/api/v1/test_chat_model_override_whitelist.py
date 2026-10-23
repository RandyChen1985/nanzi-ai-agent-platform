"""输入端模型覆盖的白名单拦截：未配置放行 / 白名单内放行 / 默认模型豁免 / 越权 403。"""

import pytest
from fastapi import HTTPException


pytestmark = pytest.mark.no_infrastructure


class _Session:
    """不查库：白名单与默认模型都由 monkeypatch 提供。"""

    async def execute(self, _stmt):  # pragma: no cover - 正常路径不会走到
        raise AssertionError("unexpected query")


async def _call(
    monkeypatch,
    *,
    role="user",
    allowed={"gpt-4o"},
    default="deepseek-chat",
    model="gpt-4o",
    agent_id="agent-1",
):
    from app.api.v1.endpoints import chat as chat_module
    from app.services.ai.agent_manager import AgentManagerService
    from app.services.permission_service import PermissionService

    async def _fake_allowed(self, user_id):
        return allowed

    async def _fake_default(session, agent_id, version_id=None):
        return default

    monkeypatch.setattr(PermissionService, "get_chat_allowed_model_ids", _fake_allowed)
    monkeypatch.setattr(
        AgentManagerService, "published_agent_model_name", staticmethod(_fake_default)
    )
    await chat_module._enforce_chat_model_whitelist(
        db=_Session(),
        user_info={"id": 7, "role": role},
        chat_user_id=7,
        effective_execution_options={"model": model},
        agent_id=agent_id,
        version_id=None,
    )


async def test_allows_model_in_whitelist(monkeypatch):
    await _call(monkeypatch, model="gpt-4o")


async def test_allows_agent_default_model_outside_whitelist(monkeypatch):
    await _call(monkeypatch, allowed={"gpt-4o"}, default="deepseek-chat", model="deepseek-chat")


async def test_rejects_model_outside_whitelist(monkeypatch):
    with pytest.raises(HTTPException) as excinfo:
        await _call(
            monkeypatch, allowed={"gpt-4o"}, default="deepseek-chat", model="gpt-4-turbo"
        )

    assert excinfo.value.status_code == 403
    assert "可用范围" in excinfo.value.detail


async def test_unconfigured_whitelist_allows_everything(monkeypatch):
    await _call(monkeypatch, allowed=None, model="whatever")


async def test_empty_override_is_ignored(monkeypatch):
    await _call(monkeypatch, allowed=set(), model="")


async def test_admin_bypasses_whitelist(monkeypatch):
    await _call(monkeypatch, role="admin", allowed={"gpt-4o"}, model="gpt-4-turbo")


def test_chat_endpoint_calls_the_guard_in_execution_options_cleanup():
    source = open("app/api/v1/endpoints/chat.py", encoding="utf-8").read()

    assert "_enforce_chat_model_whitelist(" in source
    # 必须紧邻既有的 execution_options 清洗，避免被绕过
    assert source.index("effective_execution_options.pop(\"resource_scope\", None)") < source.index(
        "await _enforce_chat_model_whitelist("
    )
