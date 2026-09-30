from pathlib import Path
from datetime import timedelta

import pytest

from app.core.context import AgentContext, get_current_agent_context, set_agent_context


pytestmark = pytest.mark.no_infrastructure


class _FakeArtifactSession:
    def __init__(self):
        self.added = []
        self.record = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, value):
        self.added.append(value)
        self.record = value

    async def commit(self):
        return None

    async def get(self, model, artifact_id):
        return self.record if self.record and self.record.id == artifact_id else None


@pytest.fixture
def unified_publish_env(tmp_path, monkeypatch):
    from app.services.ai.tools import generated_file_service

    workspace_root = tmp_path / "agent_workspaces"
    workspace_root.mkdir()
    session = _FakeArtifactSession()

    async def fake_workspace_root():
        return workspace_root

    async def fake_config_get(key, default=None):
        return None

    monkeypatch.setattr(generated_file_service, "_workspace_root", fake_workspace_root)
    monkeypatch.setattr(generated_file_service, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(generated_file_service.ConfigService, "get", fake_config_get)
    set_agent_context(AgentContext(agent_id="agent", agent_name="Agent", user_id=7))
    try:
        yield workspace_root, session
    finally:
        # 请求上下文是跨用例共享的 ContextVar，fixture 里设置就必须在退出时清理，
        # 否则后续用例会带着 user_id=7、is_admin=False 进入权限分支。
        set_agent_context(None)


def test_generated_file_default_ttl_is_thirty_days():
    from app.services.ai.tools.generated_file_service import DEFAULT_TTL

    assert DEFAULT_TTL == timedelta(days=30)


@pytest.mark.asyncio
async def test_publish_generates_private_artifact_and_resolves_matching_token(
    tmp_path,
    unified_publish_env,
):
    from app.services.ai.tools import generated_file_service

    source = tmp_path / "source.xlsx"
    source.write_bytes(b"workbook")

    artifact = await generated_file_service.publish(
        source,
        "report.xlsx",
        owner_user_id=7,
        artifact_type="excel",
    )

    assert artifact.download_url.startswith("/api/v1/chat/generated-files/")
    assert "static/uploads" not in artifact.download_url
    resolved = await generated_file_service.resolve_workspace_artifact(
        artifact.artifact_id,
        artifact.token,
    )
    assert resolved is not None
    assert resolved.path.read_bytes() == b"workbook"


@pytest.mark.asyncio
async def test_publish_uses_configured_public_url_for_download_link(
    tmp_path,
    monkeypatch,
    unified_publish_env,
):
    from app.services.ai.tools import generated_file_service

    monkeypatch.setattr(
        generated_file_service.settings,
        "APP_PUBLIC_URL",
        "https://files.example.com/",
    )
    source = tmp_path / "report.md"
    source.write_text("# report", encoding="utf-8")

    artifact = await generated_file_service.publish(
        source,
        "report.md",
        owner_user_id=7,
        artifact_type="markdown",
    )

    assert artifact.download_url.startswith(
        "https://files.example.com/api/v1/chat/generated-files/"
    )
    assert "//api/" not in artifact.download_url


@pytest.mark.asyncio
async def test_system_download_url_prefix_takes_precedence_over_env(monkeypatch):
    from app.services.ai.tools import generated_file_service

    async def fake_get(key, default=None):
        assert key == "download_url_prefix"
        return "https://configured.example.com/"

    monkeypatch.setattr(generated_file_service.ConfigService, "get", fake_get)
    monkeypatch.setattr(
        generated_file_service.settings,
        "APP_PUBLIC_URL",
        "https://environment.example.com",
    )

    assert await generated_file_service.get_download_url_prefix() == "https://configured.example.com"


@pytest.mark.asyncio
async def test_resolve_workspace_artifact_rejects_wrong_token(
    tmp_path,
    unified_publish_env,
):
    from app.services.ai.tools import generated_file_service

    source = tmp_path / "source.docx"
    source.write_bytes(b"document")
    artifact = await generated_file_service.publish(
        source,
        "letter.docx",
        owner_user_id=7,
        artifact_type="word",
    )

    assert await generated_file_service.resolve_workspace_artifact(
        artifact.artifact_id,
        "wrong",
    ) is None


@pytest.mark.asyncio
async def test_resolve_workspace_artifact_rejects_expired_token(
    tmp_path,
    unified_publish_env,
):
    from app.services.ai.tools import generated_file_service

    source = tmp_path / "expired.docx"
    source.write_bytes(b"document")
    artifact = await generated_file_service.publish(
        source,
        "expired.docx",
        owner_user_id=7,
        artifact_type="word",
        ttl=timedelta(seconds=-1),
    )

    assert await generated_file_service.resolve_workspace_artifact(
        artifact.artifact_id,
        artifact.token,
    ) is None


@pytest.mark.asyncio
async def test_resolve_workspace_artifact_rejects_deleted_source_file(
    tmp_path,
    unified_publish_env,
):
    from app.services.ai.tools import generated_file_service

    source = tmp_path / "deleted.docx"
    source.write_bytes(b"document")
    artifact = await generated_file_service.publish(
        source,
        "deleted.docx",
        owner_user_id=7,
        artifact_type="word",
    )
    record = unified_publish_env[1].record
    Path(record.storage_path).unlink()

    assert await generated_file_service.resolve_workspace_artifact(
        artifact.artifact_id,
        artifact.token,
    ) is None


@pytest.mark.asyncio
async def test_publish_unifies_external_file_into_workspace_artifact(tmp_path, monkeypatch):
    from app.core.context import AgentContext, set_agent_context
    from app.services.ai.tools import generated_file_service

    class FakeSession:
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

    source = tmp_path / "browser-download" / "report.pdf"
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    workspace_root = tmp_path / "agent_workspaces"
    workspace_root.mkdir()
    session = FakeSession()

    async def fake_workspace_root():
        return workspace_root

    async def fake_config_get(key, default=None):
        return None

    monkeypatch.setattr(generated_file_service, "_workspace_root", fake_workspace_root)
    monkeypatch.setattr(generated_file_service, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(generated_file_service.ConfigService, "get", fake_config_get)
    set_agent_context(
        AgentContext(
            agent_id="agent",
            agent_name="Agent",
            user_id=7,
            conversation_id="conv-1",
            trace_id="trace-1",
        )
    )

    artifact = await generated_file_service.publish(
        source,
        "report.pdf",
        owner_user_id=7,
        conversation_id="conv-1",
        trace_id="trace-1",
        artifact_type="pdf",
    )

    assert len(session.added) == 1
    assert session.added[0].artifact_type == "pdf"
    assert Path(session.added[0].storage_path).is_relative_to(workspace_root)
    assert Path(session.added[0].storage_path).read_bytes() == b"pdf"
    assert not list(workspace_root.rglob("manifest.json"))
    assert artifact.download_url in get_current_agent_context().published_download_urls
    # 本用例自行设置过请求上下文，收尾时清理，避免泄漏给后续用例
    set_agent_context(None)


@pytest.mark.asyncio
async def test_publish_requires_owner_for_unified_artifact(tmp_path):
    from app.services.ai.tools import generated_file_service

    source = tmp_path / "report.pdf"
    source.write_bytes(b"pdf")

    with pytest.raises(ValueError, match="owner_user_id"):
        await generated_file_service.publish(source, "report.pdf")


def test_record_published_download_url_deduplicates_current_context():
    from app.core.context import AgentContext, set_agent_context
    from app.services.ai.tools import generated_file_service

    context = AgentContext(agent_id="agent", agent_name="agent")
    set_agent_context(context)

    generated_file_service.record_published_download_url(
        "/api/v1/chat/generated-files/0123456789abcdef0123456789abcdef?token=token_1"
    )
    generated_file_service.record_published_download_url(
        "/api/v1/chat/generated-files/0123456789abcdef0123456789abcdef?token=token_1"
    )
    generated_file_service.record_published_download_url("")

    assert context.published_download_urls == [
        "/api/v1/chat/generated-files/0123456789abcdef0123456789abcdef?token=token_1"
    ]
    # 同上：显式清理，避免污染后续用例
    set_agent_context(None)


def test_filter_untrusted_generated_download_urls_preserves_only_allowlisted_links():
    from app.services.ai.tools import generated_file_service

    trusted = "https://files.example.com/api/v1/chat/generated-files/0123456789abcdef0123456789abcdef?token=trusted"
    fake = "https://files.example.com/api/v1/chat/generated-files/abcdef0123456789abcdef0123456789?token=fake"
    text = f"真实地址：{trusted}\n伪造地址：{fake}\n普通链接：https://example.com/report.docx"

    filtered = generated_file_service.filter_untrusted_download_urls(
        text,
        allowed_urls={trusted},
    )

    assert trusted in filtered
    assert fake not in filtered
    assert "下载地址未通过文件工具确认" in filtered
    assert "https://example.com/report.docx" in filtered
