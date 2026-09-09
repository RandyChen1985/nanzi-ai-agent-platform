from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.error_response_service import build_error_presentation


pytestmark = pytest.mark.no_infrastructure


def _client(response):
    client = MagicMock()
    client.generate_text = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_current_model_generates_friendly_error_and_keeps_sanitized_detail():
    primary = _client("读取配置没有完成，请检查文件是否存在或当前账号是否有访问权限。")
    raw = (
        "GET https://api.example.test/v1 failed: Authorization: Bearer sk-live-secret "
        "password=super-secret; Cookie: session-id=session-secret; "
        "refresh=refresh-secret; mysql://db-user:db-secret@db.internal/app; "
        "see /Users/chenxiaolong/workspace/app/.env"
    )

    with patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_configured_llm",
        new=AsyncMock(return_value="primary-llm"),
    ) as get_current, patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_fallback_llm",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.ai.error_response_service.chat_client_from_handle",
        return_value=primary,
    ):
        result = await build_error_presentation(RuntimeError(raw), model_name="deepseek-v4")

    assert result.content == "读取配置没有完成，请检查文件是否存在或当前账号是否有访问权限。"
    assert result.ai_status == "success"
    assert "sk-live-secret" not in result.raw_error
    assert "super-secret" not in result.raw_error
    assert "session-secret" not in result.raw_error
    assert "refresh-secret" not in result.raw_error
    assert "db-secret" not in result.raw_error
    assert "/Users/chenxiaolong" not in result.raw_error
    assert "api.example.test" in result.raw_error
    prompt = primary.generate_text.await_args.args[0][0].content[0].text
    assert "sk-live-secret" not in prompt
    get_current.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_current_model_uses_fallback_model():
    primary = _client(None)
    primary.generate_text.side_effect = TimeoutError("primary timed out")
    fallback = _client("这次处理没有完成，请稍后重试；如果持续发生，请检查模型服务状态。")

    with patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_configured_llm",
        new=AsyncMock(return_value="primary-llm"),
    ), patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_fallback_llm",
        new=AsyncMock(return_value="fallback-llm"),
    ) as get_fallback, patch(
        "app.services.ai.error_response_service.chat_client_from_handle",
        side_effect=lambda handle: primary if handle == "primary-llm" else fallback,
    ):
        result = await build_error_presentation(
            RuntimeError("upstream returned 503"),
            model_name="primary-model",
        )

    assert result.ai_status == "fallback"
    assert result.content.startswith("这次处理没有完成")
    get_fallback.assert_awaited_once_with(
        streaming=False,
        config=None,
        exclude_model="primary-model",
    )


@pytest.mark.asyncio
async def test_all_explanation_attempts_fall_back_to_existing_static_message():
    primary = _client("")
    fallback = _client("[系统错误] 这是不应该直接展示的内部格式")

    with patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_configured_llm",
        new=AsyncMock(return_value="primary-llm"),
    ), patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_fallback_llm",
        new=AsyncMock(return_value="fallback-llm"),
    ), patch(
        "app.services.ai.error_response_service.chat_client_from_handle",
        side_effect=lambda handle: primary if handle == "primary-llm" else fallback,
    ):
        result = await build_error_presentation(RuntimeError("connection reset by peer"))

    assert result.ai_status == "disabled"
    assert result.content.startswith("\n\n[系统错误] 执行过程中发生异常:")


@pytest.mark.asyncio
async def test_known_context_window_error_skips_ai_explanation():
    with patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_configured_llm",
        new=AsyncMock(),
    ) as get_current:
        result = await build_error_presentation(
            RuntimeError(
                "maximum context length exceeded; requested token count 10000"
            ),
            model_name="deepseek-v4",
        )

    assert result.ai_status == "disabled"
    assert "上下文" in result.content
    get_current.assert_not_awaited()


@pytest.mark.asyncio
async def test_docker_sandbox_error_uses_specialized_message_without_ai():
    from app.services.ai.runtime.agentscope.workspace import DockerSandboxUnavailableError

    with patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_configured_llm",
        new=AsyncMock(),
    ) as get_current:
        result = await build_error_presentation(
            DockerSandboxUnavailableError(
                "permission denied: /var/run/docker.sock",
                user_message="Docker 沙箱不可用，Bash 未执行。",
            )
        )

    assert result.ai_status == "disabled"
    assert result.content == "Docker 沙箱不可用，Bash 未执行。"
    assert "/var/run/docker.sock" not in result.raw_error
    get_current.assert_not_awaited()


@pytest.mark.asyncio
async def test_k8s_sandbox_error_uses_specialized_message_without_ai():
    from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

    with patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_configured_llm",
        new=AsyncMock(),
    ) as get_current:
        result = await build_error_presentation(
            K8sSandboxUnavailableError(
                "gateway failed to add MCP 'sandbox'",
                user_message="Kubernetes 沙箱中的 Bash 工具不可用，Bash 未执行。",
            )
        )

    assert result.ai_status == "disabled"
    assert result.content == "Kubernetes 沙箱中的 Bash 工具不可用，Bash 未执行。"
    get_current.assert_not_awaited()


@pytest.mark.asyncio
async def test_explanation_prompt_receives_structured_safe_diagnostic_context():
    primary = _client("读取文件没有完成，请检查路径和权限后重试。")

    with patch(
        "app.services.ai.error_response_service.AgentConfigProvider.get_configured_llm",
        new=AsyncMock(return_value="primary-llm"),
    ), patch(
        "app.services.ai.error_response_service.chat_client_from_handle",
        return_value=primary,
    ):
        await build_error_presentation(
            RuntimeError("permission denied password=top-secret"),
            tool_name="Glob",
            stage="工具执行",
            operation="读取工作空间文件",
        )

    prompt = primary.generate_text.await_args.args[0][1].content[0].text
    assert "工具：Glob" in prompt
    assert "阶段：工具执行" in prompt
    assert "操作：读取工作空间文件" in prompt
    assert "top-secret" not in prompt
    assert primary.generate_text.await_args.kwargs["temperature"] == 0.1
