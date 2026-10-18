"""沙箱 gateway 请求超时应跟随 ``agent_max_toolcall_timeout`` 配置。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


pytestmark = pytest.mark.no_infrastructure


def _fake_workspace(timeout: float = 30.0) -> SimpleNamespace:
    return SimpleNamespace(_gateway=SimpleNamespace(timeout=timeout))


async def test_apply_sandbox_gateway_timeout_follows_configured_tool_timeout():
    from app.services.ai.runtime.agentscope.tool_timeout import (
        apply_sandbox_gateway_timeout,
    )

    workspace = _fake_workspace()
    with patch(
        "app.services.config_service.ConfigService.get",
        new_callable=AsyncMock,
    ) as get:
        get.return_value = "180"
        applied = await apply_sandbox_gateway_timeout(workspace)

    assert applied == 180.0
    assert workspace._gateway.timeout == 180.0


async def test_apply_sandbox_gateway_timeout_keeps_library_floor():
    from app.services.ai.runtime.agentscope.tool_timeout import (
        MIN_SANDBOX_GATEWAY_TIMEOUT,
        apply_sandbox_gateway_timeout,
    )

    workspace = _fake_workspace()
    with patch(
        "app.services.config_service.ConfigService.get",
        new_callable=AsyncMock,
    ) as get:
        get.return_value = "5"
        applied = await apply_sandbox_gateway_timeout(workspace)

    assert applied == MIN_SANDBOX_GATEWAY_TIMEOUT == 30.0
    assert workspace._gateway.timeout == 30.0


async def test_apply_sandbox_gateway_timeout_falls_back_on_invalid_config():
    from app.services.ai.runtime.agentscope.tool_timeout import (
        DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT,
        apply_sandbox_gateway_timeout,
    )

    workspace = _fake_workspace()
    with patch(
        "app.services.config_service.ConfigService.get",
        new_callable=AsyncMock,
    ) as get:
        get.side_effect = RuntimeError("config store unavailable")
        applied = await apply_sandbox_gateway_timeout(workspace)

    assert applied == DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT
    assert workspace._gateway.timeout == DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT


async def test_apply_sandbox_gateway_timeout_ignores_workspace_without_gateway():
    from app.services.ai.runtime.agentscope.tool_timeout import (
        apply_sandbox_gateway_timeout,
    )

    assert await apply_sandbox_gateway_timeout(SimpleNamespace()) is None


async def test_lazy_sandbox_proxy_applies_gateway_timeout_on_ensure_ready():
    from app.services.ai.runtime.agentscope import workspace as workspace_module

    fake = _fake_workspace()
    proxy = workspace_module.LazySandboxWorkspaceProxy(
        policy=workspace_module.SANDBOX_POLICY_DOCKER,
        root="/tmp",
        user_key="tester",
        skill_paths=None,
    )
    with patch.object(
        workspace_module,
        "_acquire_docker_workspace",
        new=AsyncMock(return_value=(fake, "cache-key")),
    ), patch(
        "app.services.config_service.ConfigService.get",
        new_callable=AsyncMock,
    ) as get:
        get.return_value = "600"
        result = await proxy.ensure_ready()

    assert result is fake
    assert fake._gateway.timeout == 600.0
