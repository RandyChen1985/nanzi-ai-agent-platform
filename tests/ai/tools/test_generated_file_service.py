import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


# --------------------------------------------------------------------------- #
# 确定性签名 token：修「列表接口轮换 token 吊销已分发链接」
#
# 根因：ai_artifacts 只存 token 哈希、无法还原明文，列表接口为了给出可用的
# download_url 只能重新签发并覆盖哈希 —— 而登记时写进消息正文的链接用的是
# 当时那个 token，于是「一打开我的产出抽屉，正文里的链接就全失效」。
# 改成由 (artifact_id, expires_at) 确定性派生后，列表接口直接重算同一个 token，
# 无需写库，链接不再互相吊销。
# --------------------------------------------------------------------------- #
def test_build_artifact_token_is_deterministic_and_binds_id_and_expiry():
    from app.services.ai.tools import generated_file_service

    expires = datetime(2026, 11, 10, 5, 32, 43, tzinfo=timezone.utc)
    first = generated_file_service.build_artifact_token("a" * 32, expires)
    second = generated_file_service.build_artifact_token("a" * 32, expires)
    assert first == second, "同一产物同一过期时间必须派生同一 token（列表接口靠它免写库）"
    assert first != generated_file_service.build_artifact_token("b" * 32, expires)
    assert first != generated_file_service.build_artifact_token("a" * 32, expires.replace(hour=6))
    # 必须能安全放进 ?token=...（现有 URL 正则只允许 [A-Za-z0-9_-]）
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
    # 秒级以内的微秒差异不得改变 token（DB 列不保存微秒）
    assert first == generated_file_service.build_artifact_token(
        "a" * 32, expires.replace(microsecond=999999)
    )


def test_build_artifact_token_requires_expiry():
    from app.services.ai.tools import generated_file_service

    with pytest.raises(ValueError):
        generated_file_service.build_artifact_token("a" * 32, None)


def test_verify_artifact_token_prefers_signature_and_falls_back_to_legacy_hash():
    from app.services.ai.tools import generated_file_service

    expires = datetime(2026, 11, 10, 5, 32, 43, tzinfo=timezone.utc)
    artifact_id = "a" * 32
    signed = generated_file_service.build_artifact_token(artifact_id, expires)

    # 新机制：验签通过（即便库里没有历史哈希）
    assert generated_file_service.verify_artifact_token(artifact_id, expires, signed, None) is True

    # 历史随机 token：签名必然不匹配，但哈希匹配时仍放行（兼容升级前已发出的链接）
    legacy = "legacy-random-token"
    assert generated_file_service.verify_artifact_token(
        artifact_id, expires, legacy, generated_file_service._token_hash(legacy)
    ) is True

    # 两种都不匹配 → 拒绝
    assert generated_file_service.verify_artifact_token(
        artifact_id, expires, "wrong-token", generated_file_service._token_hash(legacy)
    ) is False
    # 空 token 直接拒绝
    assert generated_file_service.verify_artifact_token(artifact_id, expires, "", None) is False
    # 拿别的产物的签名 token 来访问本产物 → 拒绝（artifact_id 参与派生）
    other = generated_file_service.build_artifact_token("b" * 32, expires)
    assert generated_file_service.verify_artifact_token(artifact_id, expires, other, None) is False


@pytest.mark.asyncio
async def test_register_artifact_issues_deterministic_signed_token(unified_publish_env):
    from app.services.ai.tools import generated_file_service

    workspace_root, session = unified_publish_env
    source = workspace_root / "报告.docx"
    source.write_bytes(b"docx-bytes")

    published = await generated_file_service.register_artifact(
        source_path=source,
        filename="报告.docx",
        owner_user_id=7,
        artifact_type="word",
    )

    assert published.token == generated_file_service.build_artifact_token(
        published.artifact_id, published.expires_at
    ), "登记返回的 token 必须能由 (id, expires_at) 重算出来"
    # 哈希列仍要写（模型非空约束），但只是历史回退用的备份
    assert session.added[0].token_hash == generated_file_service._token_hash(published.token)
    # DB 的 DateTime 列不保存微秒（MySQL DATETIME(0) 还会四舍五入）：登记时必须已规整到
    # 整秒，否则列表接口从库里读回来的值会与登记值差 1 秒 → 重算出的 token 与登记时不一致
    assert published.expires_at.microsecond == 0


@pytest.mark.asyncio
async def test_signed_token_survives_artifact_listing(unified_publish_env):
    """回归锁：登记时签发的 token 在列表接口跑过之后必须仍然可用。

    这正是用户踩的坑（正文链接 404「文件不存在或已过期」而抽屉正常）。
    """
    from app.services.ai.tools import generated_file_service

    workspace_root, _session = unified_publish_env
    source = workspace_root / "清单.docx"
    source.write_bytes(b"docx-bytes")

    published = await generated_file_service.register_artifact(
        source_path=source,
        filename="清单.docx",
        owner_user_id=7,
        artifact_type="word",
    )
    token_before = published.token

    # 模拟「我的产出」列表接口：重算并返回地址（旧实现会在这里覆盖 token_hash）
    recomputed = generated_file_service.build_artifact_token(
        published.artifact_id, published.expires_at
    )
    assert recomputed == token_before, "列表接口必须重算出同一个 token，而不是另发一个"

    resolved = await generated_file_service.resolve_workspace_artifact(
        published.artifact_id, token_before
    )
    assert resolved is not None, "登记时签发的 token 在列表之后必须仍可下载"
    assert resolved.path == source.resolve()


@pytest.mark.asyncio
async def test_resolve_accepts_signed_token_when_stored_hash_is_legacy(unified_publish_env):
    """历史记录场景：库里哈希仍是当年那个随机 token 的，而列表接口给出的是签名 token。

    升级前登记的记录就长这样（哈希指向一个明文已丢失的随机 token）。若解析只看哈希，
    这些记录在列表接口跑过之后同样会 404 —— 所以验签必须优先于哈希回退。
    """
    from app.services.ai.tools import generated_file_service

    workspace_root, session = unified_publish_env
    source = workspace_root / "旧清单.docx"
    source.write_bytes(b"docx-bytes")

    published = await generated_file_service.register_artifact(
        source_path=source,
        filename="旧清单.docx",
        owner_user_id=7,
        artifact_type="word",
    )
    # 模拟升级前的历史记录：哈希属于一个明文已无法还原的随机 token
    session.record.token_hash = generated_file_service._token_hash("lost-legacy-token")

    resolved = await generated_file_service.resolve_workspace_artifact(
        published.artifact_id, published.token
    )
    assert resolved is not None, "签名 token 必须在哈希为历史值的情况下也能解析"
    assert resolved.path == source.resolve()


def test_artifact_token_is_second_precision_so_db_roundtrip_is_stable():
    """token 只能依赖整秒 —— 这是「登记值 vs 库里读回值」必须一致的硬约束。

    MySQL 的 DATETIME 不保存微秒，且 .5 秒以上会四舍五入进位。若登记时用带微秒的
    expires_at 派生（int() 截断到 43 秒），而列表接口从库里读回 44 秒再重算，
    同一个产物就会算出两个 token —— 正文里的链接即使哈希未被覆盖也会失效。
    """
    from app.services.ai.tools import generated_file_service

    def _db_roundtrip(value):
        """模拟 MySQL DATETIME(0)：秒以下四舍五入后落库、读回时就是那个整秒值。"""
        return (value + timedelta(microseconds=500000)).replace(microsecond=0)

    artifact_id = "a" * 32
    with_micro = datetime(2026, 11, 10, 5, 32, 43, 700000, tzinfo=timezone.utc)

    # 不规整 → 漂移真实存在：登记时算的是 43 秒，库里读回的是 44 秒
    assert generated_file_service.build_artifact_token(
        artifact_id, with_micro
    ) != generated_file_service.build_artifact_token(
        artifact_id, _db_roundtrip(with_micro)
    ), "带微秒的 expires_at 会产生 1 秒漂移，必须靠登记时规整来消除"

    # 规整到整秒后，DB 往返不再改变该值 —— 登记值与列表重算值必然一致
    normalized = with_micro.replace(microsecond=0)
    assert generated_file_service.build_artifact_token(
        artifact_id, normalized
    ) == generated_file_service.build_artifact_token(artifact_id, _db_roundtrip(normalized))
