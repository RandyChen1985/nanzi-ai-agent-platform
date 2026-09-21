"""智能体专属头像上传端点：权限、校验，以及最关键的「只清自己的旧文件」。

历史坑：全局 AI 头像上传会用 `agent_avatar*` 通配清空 `data/branding/avatars/`。
如果智能体头像落在同一目录或共用同前缀，上传任意一个智能体头像都会连带删除
全局头像和其它智能体头像——本文件的隔离用例就是钉死这条边界的。
"""

from io import BytesIO

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.portal.endpoints import agents as agents_endpoint
from app.core import avatar_assets
from app.models.agent import AIAgent
from app.services.ai.agent_manager import AgentManagerService, build_agent_identity_map


pytestmark = pytest.mark.no_infrastructure


class FakeSession:
    """只实现端点用到的 `session.get`。"""

    def __init__(self, agent=None):
        self.agent = agent

    async def get(self, _model, _agent_id):
        return self.agent


def _agent(agent_id="agent-1", created_by="alice", is_system=False, avatar_url=None):
    return AIAgent(
        id=agent_id,
        name=f"name-{agent_id}",
        display_name=f"显示名-{agent_id}",
        created_by=created_by,
        is_system=is_system,
        avatar_url=avatar_url,
    )


def _upload(filename="robot.png", content=b"\x89PNG\r\n\x1a\nfake-image-content", content_type="image/png"):
    return StarletteUploadFile(
        filename=filename,
        file=BytesIO(content),
        headers={"content-type": content_type},
    )


@pytest.fixture(autouse=True)
def _isolated_branding_dirs(monkeypatch, tmp_path):
    """把智能体头像目录指向临时目录，绝不写真实 data/branding。"""
    agent_dir = tmp_path / "branding_agent_avatars"
    monkeypatch.setattr(agents_endpoint, "AGENT_AVATAR_DIR", str(agent_dir))
    return agent_dir


@pytest.mark.asyncio
async def test_owner_uploads_avatar_and_gets_short_public_path(monkeypatch, tmp_path):
    agent_dir = tmp_path / "branding_agent_avatars"
    result = await agents_endpoint.upload_agent_avatar(
        agent_id="agent-1",
        file=_upload(),
        session=FakeSession(_agent()),
        user={"user_name": "alice", "role": "user"},
    )

    assert result["code"] == 0
    avatar_url = result["data"]["avatar_url"]
    assert avatar_url.startswith("/branding/agent-avatars/agent-1_")
    assert avatar_url.endswith(".png")
    # 短路径：远低于 ai_agents.avatar_url 的 String(255) 上限
    assert len(avatar_url) < 255
    saved = list(agent_dir.glob("agent-1_*.png"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"\x89PNG\r\n\x1a\nfake-image-content"


@pytest.mark.asyncio
async def test_non_owner_cannot_upload(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        await agents_endpoint.upload_agent_avatar(
            agent_id="agent-1",
            file=_upload(),
            session=FakeSession(_agent(created_by="alice")),
            user={"user_name": "bob", "role": "user"},
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_system_agent_upload_requires_admin():
    with pytest.raises(HTTPException) as exc_info:
        await agents_endpoint.upload_agent_avatar(
            agent_id="agent-1",
            file=_upload(),
            session=FakeSession(_agent(created_by="alice", is_system=True)),
            user={"user_name": "alice", "role": "user"},
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_upload_for_others_agent(monkeypatch, tmp_path):
    result = await agents_endpoint.upload_agent_avatar(
        agent_id="agent-1",
        file=_upload(),
        session=FakeSession(_agent(created_by="alice", is_system=True)),
        user={"user_name": "root", "role": "admin"},
    )
    assert result["data"]["avatar_url"].startswith("/branding/agent-avatars/agent-1_")


@pytest.mark.asyncio
async def test_missing_agent_is_forbidden():
    with pytest.raises(HTTPException) as exc_info:
        await agents_endpoint.upload_agent_avatar(
            agent_id="gone",
            file=_upload(),
            session=FakeSession(None),
            user={"user_name": "alice", "role": "user"},
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_unsupported_type_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        await agents_endpoint.upload_agent_avatar(
            agent_id="agent-1",
            file=_upload(filename="evil.exe", content_type="application/octet-stream"),
            session=FakeSession(_agent()),
            user={"user_name": "alice", "role": "user"},
        )
    assert exc_info.value.status_code == 400
    assert "仅支持" in exc_info.value.detail


@pytest.mark.asyncio
async def test_oversized_upload_is_rejected(monkeypatch):
    monkeypatch.setattr(agents_endpoint, "MAX_AVATAR_BYTES", 8)
    with pytest.raises(HTTPException) as exc_info:
        await agents_endpoint.upload_agent_avatar(
            agent_id="agent-1",
            file=_upload(content=b"x" * 64),
            session=FakeSession(_agent()),
            user={"user_name": "alice", "role": "user"},
        )
    assert exc_info.value.status_code == 400
    assert "2MB" in exc_info.value.detail


@pytest.mark.asyncio
async def test_upload_only_purges_own_previous_files(tmp_path):
    """核心回归：上传一个智能体头像，绝不能动其它智能体的文件。"""
    agent_dir = tmp_path / "branding_agent_avatars"
    agent_dir.mkdir(parents=True)
    other_agent_file = agent_dir / "agent-2_1700000000.png"
    other_agent_file.write_bytes(b"other-agent-avatar")
    stale_own_file = agent_dir / "agent-1_1600000000.png"
    stale_own_file.write_bytes(b"stale-own-avatar")

    await agents_endpoint.upload_agent_avatar(
        agent_id="agent-1",
        file=_upload(),
        session=FakeSession(_agent()),
        user={"user_name": "alice", "role": "user"},
    )

    assert not stale_own_file.exists(), "同一智能体的历史头像应被清理"
    assert other_agent_file.exists(), "其它智能体的头像被误删了（清理范围必须限定在本智能体）"
    assert other_agent_file.read_bytes() == b"other-agent-avatar"
    assert len(list(agent_dir.glob("agent-1_*.png"))) == 1


def test_global_and_agent_avatar_dirs_are_isolated():
    """目录必须彻底隔离：全局上传的 `agent_avatar*` 清理不能波及智能体头像。"""
    assert avatar_assets.AGENT_AVATAR_DIR != avatar_assets.BRANDING_AVATARS_DIR
    assert not avatar_assets.AGENT_AVATAR_DIR.startswith(avatar_assets.BRANDING_AVATARS_DIR + "/")
    assert not avatar_assets.BRANDING_AVATARS_DIR.startswith(avatar_assets.AGENT_AVATAR_DIR + "/")
    assert avatar_assets.AGENT_AVATAR_URL == "/branding/agent-avatars"
    assert avatar_assets.BRANDING_AVATARS_URL == "/branding/avatars"


def test_sanitize_asset_key_blocks_path_traversal():
    assert avatar_assets.sanitize_asset_key("../../etc/passwd") == "etcpasswd"
    assert "/" not in avatar_assets.sanitize_asset_key("a/b\\c")
    assert avatar_assets.sanitize_asset_key(None) == "unknown"
    assert avatar_assets.sanitize_asset_key("") == "unknown"


def test_resolve_avatar_extension_prefers_mime_then_filename():
    assert avatar_assets.resolve_avatar_extension("image/png", "x.bin") == ".png"
    assert avatar_assets.resolve_avatar_extension("", "photo.JPEG") == ".jpg"
    assert avatar_assets.resolve_avatar_extension(None, "vector.svg") == ".svg"
    assert avatar_assets.resolve_avatar_extension("application/octet-stream", "x.exe") is None


def test_build_agent_identity_map_includes_avatar():
    class Row:
        def __init__(self, _id, name, display_name, avatar_url):
            self.id = _id
            self.name = name
            self.display_name = display_name
            self.avatar_url = avatar_url

    identity_map = build_agent_identity_map(
        [
            Row("a1", "kb", "知识库专家", "/branding/agent-avatars/a1_1.png"),
            Row("b1", "chatbi", "数据智能助手", None),
        ]
    )

    # 第三个元素就是历史消息要下发的 agent_avatar_url
    assert identity_map["a1"] == ("kb", "知识库专家", "/branding/agent-avatars/a1_1.png")
    assert identity_map["b1"] == ("chatbi", "数据智能助手", None)
    assert build_agent_identity_map(None) == {}


def test_can_edit_agent_meta_matches_endpoint_permission_rules():
    """上传端点与 PUT /agents/{id} 共用判定，这里把规则钉死避免漂移。"""
    owner_agent = _agent(created_by="alice")

    assert AgentManagerService.can_edit_agent_meta(owner_agent, {"user_name": "alice", "role": "user"}) is True
    assert AgentManagerService.can_edit_agent_meta(owner_agent, {"user_name": "bob", "role": "user"}) is False
    assert AgentManagerService.can_edit_agent_meta(owner_agent, {"user_name": "root", "role": "admin"}) is True
    assert (
        AgentManagerService.can_edit_agent_meta(_agent(is_system=True), {"user_name": "alice", "role": "user"})
        is False
    )
    assert (
        AgentManagerService.can_edit_agent_meta(_agent(is_system=True), {"user_name": "root", "role": "admin"})
        is True
    )
