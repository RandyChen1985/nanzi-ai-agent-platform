from pathlib import Path

import pytest

from app.core.context import AgentContext, get_current_agent_context, set_agent_context


pytestmark = pytest.mark.no_infrastructure


class _FakeArtifactSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None


@pytest.fixture
def publish_env(tmp_path, monkeypatch):
    from app.services.ai.runtime.agentscope import workspace
    from app.services.ai.tools import generated_file_service

    root = tmp_path / "agent_workspaces"
    user_root = root / "7"
    docs_dir = user_root / "docs"
    docs_dir.mkdir(parents=True)

    async def workspace_root():
        return str(root)

    monkeypatch.setattr(workspace, "resolve_workspace_root", workspace_root)
    monkeypatch.setattr(generated_file_service, "_workspace_root", workspace_root)
    monkeypatch.setattr(
        generated_file_service,
        "AsyncSessionLocal",
        lambda: _FakeArtifactSession(),
    )
    set_agent_context(
        AgentContext(
            agent_id="agent",
            agent_name="Agent",
            user_id=7,
            conversation_id="conv-1",
            trace_id="trace-1",
        )
    )
    try:
        yield root, docs_dir
    finally:
        # 请求上下文是跨用例共享的 ContextVar，必须清理。否则后续用例会带着
        # user_id=7、is_admin=False 进入权限分支：例如 search_knowledge_base
        # 会把显式传入的 dataset_id 当作「无权限的受限数据集」移除，导致
        # test_knowledge_tool.py 全量运行时失败、单跑却通过。
        set_agent_context(None)


@pytest.mark.asyncio
async def test_publish_generated_file_returns_download_url_for_docker_path(publish_env):
    from app.services.ai.tools.generated_file_tool import publish_generated_file

    root, docs_dir = publish_env
    source = docs_dir / "report.md"
    source.write_text("# report", encoding="utf-8")

    result = await publish_generated_file.ainvoke({
        "path": "/workspace/docs/report.md",
    })

    assert result["status"] == "ok"
    assert result["filename"] == "report.md"
    assert result["artifact_type"] == "markdown"
    # download_url 可能是相对路径，也可能被拼成绝对地址（取决于进程内 base URL
    # 配置是否已被其它测试/初始化装载），断言只关心路径本身，不绑定这个差异。
    assert "/api/v1/chat/generated-files/" in result["download_url"]
    assert "token=" in result["download_url"]
    assert result["expires_at"]
    assert result["download_url"] in get_current_agent_context().published_download_urls
    assert Path(root / "7" / "docs" / "report.md").read_text(encoding="utf-8") == "# report"


@pytest.mark.asyncio
async def test_publish_generated_file_rejects_another_users_workspace(publish_env):
    from app.services.ai.tools.generated_file_tool import publish_generated_file

    root, _ = publish_env
    secret = root / "8" / "docs" / "secret.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="当前用户工作区"):
        await publish_generated_file.ainvoke({"path": str(secret)})


@pytest.mark.asyncio
async def test_publish_generated_file_failure_does_not_register_download_url(
    publish_env,
    monkeypatch,
):
    from app.services.ai.tools import generated_file_tool as generated_file_tool_module
    from app.services.ai.tools.generated_file_tool import publish_generated_file

    _, docs_dir = publish_env
    source = docs_dir / "failed.md"
    source.write_text("# failed", encoding="utf-8")

    async def fail_register(**kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(generated_file_tool_module, "register_artifact", fail_register)

    with pytest.raises(ValueError, match="下载地址登记失败"):
        await publish_generated_file.ainvoke({"path": "/workspace/docs/failed.md"})

    assert get_current_agent_context().published_download_urls == []


def test_publish_generated_file_is_system_implicit():
    from app.services.ai.tools.registry import ToolRegistry

    names = {
        getattr(tool, "name", "")
        for tool in ToolRegistry.get_system_implicit_tools()
    }

    assert "publish_generated_file" in names
    assert ToolRegistry._registry["publish_generated_file"].name == "publish_generated_file"
