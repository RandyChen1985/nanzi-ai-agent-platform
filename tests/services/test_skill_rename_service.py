"""技能重命名的行为测试。

重命名 = 目录改名 + **三处引用同步**。只改目录会得到「技能还在，但智能体挂不上、
会话里的 chip 指向不存在的 ID、发布记录变成源已不存在的孤儿」这种静默断链，
所以这里既验证拒绝条件，也验证同步编排与报告内容。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services import skill_rename_service as svc
from app.services.conversation_resource_service import ConversationResourceService

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def skill_tree(tmp_path):
    root = tmp_path / "skills"
    (root / "old-skill").mkdir(parents=True)
    (root / "old-skill" / "SKILL.md").write_text("# 旧技能\n", encoding="utf-8")
    (root / "old-skill" / "run.py").write_text("print(1)\n", encoding="utf-8")
    (root / "taken").mkdir()
    (root / "taken" / "SKILL.md").write_text("# 已存在\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# ID 校验
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "   ", "中文技能", "with space", "a/b", "a.b", "a" * 129])
def test_rejects_invalid_new_id(bad):
    with pytest.raises(svc.SkillRenameError):
        svc.validate_new_skill_id(bad)


@pytest.mark.parametrize("reserved", sorted(svc.RESERVED_SKILL_IDS))
def test_rejects_reserved_new_id(reserved):
    """保留字会被 /skills 下更早注册的字面路由遮蔽。"""
    with pytest.raises(svc.SkillRenameError):
        svc.validate_new_skill_id(reserved)


def test_accepts_valid_new_id():
    assert svc.validate_new_skill_id("  new-skill_2  ") == "new-skill_2"


# ---------------------------------------------------------------------------
# 目录改名
# ---------------------------------------------------------------------------
def test_rename_directory_moves_content(skill_tree):
    new_dir = svc.rename_skill_directory(
        str(skill_tree / "old-skill"), new_skill_id="new-skill"
    )
    assert new_dir == str(skill_tree / "new-skill")
    assert (skill_tree / "new-skill" / "SKILL.md").read_text(encoding="utf-8") == "# 旧技能\n"
    assert (skill_tree / "new-skill" / "run.py").is_file()
    assert not (skill_tree / "old-skill").exists()


def test_rename_directory_refuses_existing_target(skill_tree):
    with pytest.raises(svc.SkillRenameError, match="已被占用"):
        svc.rename_skill_directory(str(skill_tree / "old-skill"), new_skill_id="taken")
    # 两个技能都必须原样保留
    assert (skill_tree / "old-skill" / "SKILL.md").is_file()
    assert (skill_tree / "taken" / "SKILL.md").read_text(encoding="utf-8") == "# 已存在\n"


def test_rename_directory_refuses_same_id(skill_tree):
    with pytest.raises(svc.SkillRenameError, match="相同"):
        svc.rename_skill_directory(str(skill_tree / "old-skill"), new_skill_id="old-skill")


def test_rename_directory_refuses_non_skill_dir(skill_tree):
    (skill_tree / "not-a-skill").mkdir()
    with pytest.raises(svc.SkillRenameError, match="SKILL.md"):
        svc.rename_skill_directory(str(skill_tree / "not-a-skill"), new_skill_id="moved")


def test_rename_directory_refuses_missing_source(tmp_path):
    with pytest.raises(svc.SkillRenameError, match="不存在"):
        svc.rename_skill_directory(str(tmp_path / "nope"), new_skill_id="anything")


# ---------------------------------------------------------------------------
# 引用同步
# ---------------------------------------------------------------------------
class _FakeRedis:
    """最小 Redis 替身：支持 scan/get/set，并记录每次写入的 TTL。"""

    def __init__(self, store):
        self.store = store
        self.ttls = []

    async def scan(self, cursor=0, match=None, count=200):
        return 0, list(self.store.keys())

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls.append(ex)


@pytest.mark.asyncio
async def test_sync_conversation_mounts_rewrites_skill_ids(monkeypatch):
    """会话挂载列表里的技能 ID 必须被改写，其它字段保持不变。"""
    store = {
        "conversation:1:c1:resource_scope_v1": json.dumps(
            {
                "project_name": "p",
                "skills": [
                    {"id": "old-skill", "name": "旧", "scope": "global"},
                    {"id": "other", "name": "别的", "scope": "global"},
                ],
            }
        ),
        "conversation:1:c2:resource_scope_v1": json.dumps({"skills": [{"id": "other"}]}),
    }
    fake = _FakeRedis(store)
    monkeypatch.setattr(
        "app.services.conversation_resource_service.get_redis", AsyncMock(return_value=fake)
    )

    updated = await svc.sync_conversation_mounts(old_skill_id="old-skill", new_skill_id="new-skill")

    assert updated == 1
    c1 = json.loads(store["conversation:1:c1:resource_scope_v1"])
    assert [item["id"] for item in c1["skills"]] == ["new-skill", "other"]
    assert c1["project_name"] == "p", "改写技能列表不能顺手丢掉范围里的其它字段"
    # 不相关的会话不应被改写
    assert json.loads(store["conversation:1:c2:resource_scope_v1"]) == {
        "skills": [{"id": "other"}]
    }


@pytest.mark.asyncio
async def test_sync_conversation_mounts_keeps_thirty_day_ttl(monkeypatch):
    """写回会话范围必须带 30 天 TTL。

    真实缺陷：重命名链路自己写了一份 `redis.set(key, value)` 不带 `ex`，把
    ConversationResourceService.replace 写入的 30 天有效期抹成了**永不过期**——键再也
    不会被回收，而且产品语义（会话范围的存活时间）被悄悄改掉。
    """
    store = {
        "conversation:1:c1:resource_scope_v1": json.dumps(
            {"skills": [{"id": "old-skill", "scope": "global"}]}
        )
    }
    fake = _FakeRedis(store)
    monkeypatch.setattr(
        "app.services.conversation_resource_service.get_redis", AsyncMock(return_value=fake)
    )

    await svc.sync_conversation_mounts(old_skill_id="old-skill", new_skill_id="new-skill")

    assert fake.ttls == [ConversationResourceService.TTL_SECONDS]
    assert ConversationResourceService.TTL_SECONDS == 2592000


@pytest.mark.asyncio
async def test_remove_conversation_mounts_drops_skill_and_keeps_ttl(monkeypatch):
    """删除/下架路径：挂载项被移除（而不是改名），写回同样带 TTL。"""
    store = {
        "conversation:1:c1:resource_scope_v1": json.dumps(
            {
                "skills": [
                    {"id": "gone-skill", "scope": "global"},
                    {"id": "keep", "scope": "global"},
                ]
            }
        )
    }
    fake = _FakeRedis(store)
    monkeypatch.setattr(
        "app.services.conversation_resource_service.get_redis", AsyncMock(return_value=fake)
    )

    updated = await svc.remove_conversation_mounts(skill_id="gone-skill", scope="global")

    assert updated == 1
    assert [item["id"] for item in json.loads(store["conversation:1:c1:resource_scope_v1"])["skills"]] == ["keep"]
    assert fake.ttls == [ConversationResourceService.TTL_SECONDS]


@pytest.mark.asyncio
async def test_mount_rewrite_respects_scope(monkeypatch):
    """同名个人技能与公共技能并存时，只动指定 scope 的那一条。

    个人技能重命名若顺手改掉公共技能的同名挂载，用户会在会话里拿到一个
    「名字对、内容不对」的技能。
    """
    store = {
        "conversation:1:c1:resource_scope_v1": json.dumps(
            {
                "skills": [
                    {"id": "same", "scope": "personal"},
                    {"id": "same", "scope": "global"},
                ]
            }
        )
    }
    fake = _FakeRedis(store)
    monkeypatch.setattr(
        "app.services.conversation_resource_service.get_redis", AsyncMock(return_value=fake)
    )

    await svc.sync_conversation_mounts(
        old_skill_id="same", new_skill_id="renamed", scope="personal"
    )

    items = json.loads(store["conversation:1:c1:resource_scope_v1"])["skills"]
    assert [(item["id"], item["scope"]) for item in items] == [
        ("renamed", "personal"),
        ("same", "global"),
    ]


@pytest.mark.asyncio
async def test_sync_conversation_mounts_survives_redis_failure(monkeypatch):
    """Redis 不可用不能让重命名失败：目录已经改好了。"""
    monkeypatch.setattr(
        "app.services.conversation_resource_service.get_redis",
        AsyncMock(side_effect=RuntimeError("redis down")),
    )
    assert await svc.sync_conversation_mounts(old_skill_id="a", new_skill_id="b") == 0


# ---------------------------------------------------------------------------
# 智能体白名单同步（ai_agent_versions.skills）
# ---------------------------------------------------------------------------
class _FakeScalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _FakeScalars(self._rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDbSession:
    """只支持本项目用到的 execute/commit 的最小会话替身（不连真库）。"""

    def __init__(self, *results):
        self._results = list(results)
        self.commits = 0

    async def execute(self, *_args, **_kwargs):
        return self._results.pop(0) if self._results else _FakeResult([])

    async def commit(self):
        self.commits += 1


def _agent_version(agent_id: str, skills, status: str = "DRAFT"):
    from app.models.agent import AIAgentVersion

    return AIAgentVersion(
        id=f"version-{agent_id}",
        agent_id=agent_id,
        version_number=1,
        system_prompt="p",
        skills=skills,
        status=status,
    )


@pytest.mark.asyncio
async def test_sync_agent_bindings_rewrites_json_whitelist():
    """白名单在 ai_agent_versions.skills（不是 ai_agents），并且必须整体赋值。"""
    version = _agent_version("agent-1", ["old-skill", "other"])
    session = _FakeDbSession(_FakeResult([version]))

    changed = await svc.sync_agent_bindings(
        session, old_skill_id="old-skill", new_skill_id="new-skill"
    )

    assert changed == ["agent-1"]
    assert version.skills == ["new-skill", "other"]


@pytest.mark.asyncio
async def test_remove_agent_skill_bindings_drops_deleted_skill():
    """删除/下架公共技能时，白名单里的该技能必须被移除。"""
    hit = _agent_version("agent-1", ["gone", "keep"])
    miss = _agent_version("agent-2", ["keep"])
    session = _FakeDbSession(_FakeResult([hit, miss]))

    changed = await svc.remove_agent_skill_bindings(session, skill_id="gone")

    assert changed == ["agent-1"]
    assert hit.skills == ["keep"]
    assert miss.skills == ["keep"]


# ---------------------------------------------------------------------------
# 编排与报告
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rename_skill_syncs_personal_publications(skill_tree, monkeypatch):
    lineage = AsyncMock(return_value=3)
    mounts = AsyncMock(return_value=5)
    monkeypatch.setattr(svc, "sync_publication_lineage", lineage)
    monkeypatch.setattr(svc, "sync_conversation_mounts", mounts)

    report = await svc.rename_skill(
        AsyncMock(),
        current_dir=str(skill_tree / "old-skill"),
        old_skill_id="old-skill",
        new_skill_id="new-skill",
        scope="personal",
    )

    assert report["skill_id"] == "new-skill"
    assert report["previous_skill_id"] == "old-skill"
    assert report["synced"]["publications"] == 3
    assert report["synced"]["conversations"] == 5
    assert report["warnings"] == []
    assert (skill_tree / "new-skill" / "SKILL.md").is_file()
    lineage.assert_awaited_once()
    # 个人技能不该去动智能体白名单（个人技能不参与公共技能白名单）
    assert "agents" not in report["synced"]


@pytest.mark.asyncio
async def test_rename_skill_syncs_agent_bindings_for_global(skill_tree, monkeypatch):
    bindings = AsyncMock(return_value=["agent-1"])
    lineage = AsyncMock(return_value=1)
    mounts = AsyncMock(return_value=0)
    monkeypatch.setattr(svc, "sync_agent_bindings", bindings)
    monkeypatch.setattr(svc, "sync_publication_lineage", lineage)
    monkeypatch.setattr(svc, "sync_conversation_mounts", mounts)

    report = await svc.rename_skill(
        AsyncMock(),
        current_dir=str(skill_tree / "old-skill"),
        old_skill_id="old-skill",
        new_skill_id="new-skill",
        scope="global",
    )

    assert report["synced"]["agents"] == ["agent-1"]
    bindings.assert_awaited_once()
    # 平台技能改名同样要重映射发布谱系里的 platform_skill_id
    assert report["synced"]["publications"] == 1
    assert lineage.await_args.kwargs["scope"] == "global"


@pytest.mark.asyncio
async def test_rename_skill_reports_warning_when_sync_fails(skill_tree, monkeypatch):
    """引用同步失败不回滚目录改名，但必须如实警告，不能假装成功了。"""
    monkeypatch.setattr(
        svc, "sync_publication_lineage", AsyncMock(side_effect=RuntimeError("db down"))
    )
    monkeypatch.setattr(svc, "sync_conversation_mounts", AsyncMock(return_value=0))

    report = await svc.rename_skill(
        AsyncMock(),
        current_dir=str(skill_tree / "old-skill"),
        old_skill_id="old-skill",
        new_skill_id="new-skill",
        scope="personal",
    )

    assert report["warnings"], "同步失败必须出现在 warnings 里"
    # 目录改名依然生效
    assert (skill_tree / "new-skill" / "SKILL.md").is_file()
    assert not (skill_tree / "old-skill").exists()


@pytest.mark.asyncio
async def test_rename_skill_rejects_taken_id_before_touching_disk(skill_tree):
    with pytest.raises(svc.SkillRenameError):
        await svc.rename_skill(
            AsyncMock(),
            current_dir=str(skill_tree / "old-skill"),
            old_skill_id="old-skill",
            new_skill_id="taken",
            scope="personal",
        )
    assert (skill_tree / "old-skill" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# 发布谱系：两个 scope 各自要改的字段
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sync_publication_lineage_remaps_platform_skill_id_for_global():
    """平台技能改名必须把 platform_skill_id（+ 已物化目录记录）一起改。

    不改的话老发布记录指向一个不存在的技能：审核端「已发布」的技能点不开，下架/删除还会
    拿着旧 ID 去删一个不存在的目录（真目录反而留下来）。
    """
    from app.models.skill_publication import SkillPublication, SkillPublicationVersion

    publication = SkillPublication(
        id="publication-1",
        platform_skill_id="old-skill",
        source_user_id=7,
        source_personal_skill_id="demo",
        name="demo",
        status="PUBLISHED",
    )
    version = SkillPublicationVersion(
        id="version-1",
        publication_id="publication-1",
        version_number=1,
        status="APPROVED",
        snapshot_path="/snapshots/publication-1/v1",
        content_sha256="0" * 64,
        submitted_by=7,
        materialized_path="/skills/old-skill",
    )
    session = _FakeDbSession(_FakeResult([publication]), _FakeResult([version]))

    count = await svc.sync_publication_lineage(
        session, old_skill_id="old-skill", new_skill_id="new-skill", scope="global"
    )

    assert count == 1
    assert publication.platform_skill_id == "new-skill"
    assert publication.source_personal_skill_id == "demo", "平台技能改名不该动来源个人技能 ID"
    assert version.materialized_path == "/skills/new-skill"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_sync_publication_lineage_only_touches_source_for_personal():
    """个人技能改名只改来源个人技能 ID，不能误改平台技能 ID。"""
    from app.models.skill_publication import SkillPublication

    publication = SkillPublication(
        id="publication-1",
        platform_skill_id="public-demo-12345678",
        source_user_id=7,
        source_personal_skill_id="old-skill",
        name="demo",
        status="PUBLISHED",
    )
    session = _FakeDbSession(_FakeResult([publication]))

    count = await svc.sync_publication_lineage(
        session, old_skill_id="old-skill", new_skill_id="new-skill", scope="personal"
    )

    assert count == 1
    assert publication.source_personal_skill_id == "new-skill"
    assert publication.platform_skill_id == "public-demo-12345678"


# ---------------------------------------------------------------------------
# 删除/下架后的引用清理
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remove_skill_references_cleans_all_three_places(monkeypatch):
    """删除公共技能：白名单 + 会话挂载 + 发布血缘三处都要动，并如实回报。"""
    bindings = AsyncMock(return_value=["agent-1"])
    mounts = AsyncMock(return_value=2)
    revoked = AsyncMock(return_value=1)
    monkeypatch.setattr(svc, "remove_agent_skill_bindings", bindings)
    monkeypatch.setattr(svc, "remove_conversation_mounts", mounts)
    monkeypatch.setattr(
        "app.services.skill_publication_service.mark_platform_skill_publications_revoked", revoked
    )

    report = await svc.remove_skill_references(
        _FakeDbSession(), skill_id="public-demo-12345678", scope="global", actor={"user_id": 1}
    )

    assert report["synced"] == {"agents": ["agent-1"], "publications": 1, "conversations": 2}
    assert report["warnings"] == []
    # 平台技能必须扫全库，因此**不能**带用户边界（owner_id=None）
    mounts.assert_awaited_once_with(
        skill_id="public-demo-12345678", scope="global", owner_id=None
    )


@pytest.mark.asyncio
async def test_remove_skill_references_never_silently_succeeds(monkeypatch):
    """同步失败必须进 warnings；没有数据库会话时也不能假装同步成功。"""
    monkeypatch.setattr(
        svc, "remove_agent_skill_bindings", AsyncMock(side_effect=RuntimeError("db down"))
    )
    monkeypatch.setattr(
        "app.services.skill_publication_service.mark_platform_skill_publications_revoked",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(svc, "remove_conversation_mounts", AsyncMock(return_value=0))

    report = await svc.remove_skill_references(
        _FakeDbSession(), skill_id="public-demo-12345678", scope="global"
    )
    assert len(report["warnings"]) == 2

    no_session = await svc.remove_skill_references(None, skill_id="public-demo-12345678", scope="global")
    assert no_session["warnings"], "没有会话时必须提示未同步，而不是静默成功"
    assert no_session["synced"].get("agents") is None


@pytest.mark.asyncio
async def test_remove_skill_references_personal_warns_about_live_platform_copy(monkeypatch):
    """删除个人技能不会替用户下架平台副本，但必须明确提示。"""
    monkeypatch.setattr(svc, "remove_conversation_mounts", AsyncMock(return_value=0))
    monkeypatch.setattr(
        "app.services.skill_publication_service.list_active_platform_skills_for_personal_skill",
        AsyncMock(return_value=["public-demo-12345678"]),
    )

    report = await svc.remove_skill_references(
        _FakeDbSession(), skill_id="demo", scope="personal", actor={"user_id": 7}
    )

    assert any("public-demo-12345678" in item for item in report["warnings"])


# ---------------------------------------------------------------------------
# 审查回归（第四轮 review）：改名必须同步「禁用技能」权限行
# ---------------------------------------------------------------------------


class _RowcountSession:
    """记录 update 语句并给出 rowcount 的最简会话替身。"""

    def __init__(self, rowcount: int = 1):
        self._rowcount = rowcount
        self.statements = []
        self.commits = 0

    async def execute(self, statement, *_args, **_kwargs):
        self.statements.append(statement)
        return _RowcountResult(self._rowcount)

    async def commit(self):
        self.commits += 1


class _RowcountResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


@pytest.mark.asyncio
async def test_sync_forbidden_skill_permissions_rewrites_id():
    """★ 平台技能改名必须把 ``forbidden_skill`` 权限行的 resource_id 一起改。

    不改就是**权限旁路**：管理员禁用了某平台技能，把它改个名字，禁用规则静默失配，
    同一个技能换个 ID 立刻对所有人重新可用（fail-open），运行时的 ``skill_paths``
    与令牌也随之把它当作可用技能。
    """
    session = _RowcountSession(rowcount=3)

    changed = await svc.sync_forbidden_skill_permissions(
        session, old_skill_id="public-demo-12345678", new_skill_id="public-demo-abcdef01"
    )

    assert changed == 3
    assert session.commits == 1, "改完要落库"
    assert len(session.statements) == 1
    statement = session.statements[0]
    assert statement.table.name == "ai_agent_resource_permissions"
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "forbidden_skill" in sql
    assert "public-demo-12345678" in sql and "public-demo-abcdef01" in sql


@pytest.mark.asyncio
async def test_sync_forbidden_skill_permissions_is_quiet_when_nothing_to_change():
    """没有匹配行时不要空提交（改名主流程还要继续做别的同步）。"""
    session = _RowcountSession(rowcount=0)

    changed = await svc.sync_forbidden_skill_permissions(
        session, old_skill_id="public-demo-12345678", new_skill_id="public-demo-abcdef01"
    )

    assert changed == 0
    assert session.commits == 0


@pytest.mark.asyncio
async def test_rename_skill_reports_permission_sync(skill_tree, monkeypatch):
    """平台技能改名把权限行同步结果回报给调用方；失败也要如实警告而不是静默成功。"""
    calls = []

    async def fake_sync(session, *, old_skill_id, new_skill_id):
        calls.append((old_skill_id, new_skill_id))
        return 2

    monkeypatch.setattr(svc, "sync_agent_bindings", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "sync_publication_lineage", AsyncMock(return_value=0))
    monkeypatch.setattr(svc, "sync_forbidden_skill_permissions", fake_sync)
    monkeypatch.setattr(
        "app.services.skill_rename_service.ConversationResourceService.rewrite_skill_mounts",
        AsyncMock(return_value=0),
    )

    report = await svc.rename_skill(
        AsyncMock(),
        current_dir=str(skill_tree / "old-skill"),
        old_skill_id="old-skill",
        new_skill_id="new-skill",
        scope="global",
    )

    assert calls == [("old-skill", "new-skill")]
    assert report["synced"]["permissions"] == 2


@pytest.mark.asyncio
async def test_rename_skill_warns_when_permission_sync_fails(skill_tree, monkeypatch):
    """权限行没同步必须出现在 warnings 里：这是权限旁路，不能混在"改了 0 个"里消失。"""
    monkeypatch.setattr(svc, "sync_agent_bindings", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "sync_publication_lineage", AsyncMock(return_value=0))
    monkeypatch.setattr(
        svc,
        "sync_forbidden_skill_permissions",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    monkeypatch.setattr(
        "app.services.skill_rename_service.ConversationResourceService.rewrite_skill_mounts",
        AsyncMock(return_value=0),
    )

    report = await svc.rename_skill(
        AsyncMock(),
        current_dir=str(skill_tree / "old-skill"),
        old_skill_id="old-skill",
        new_skill_id="new-skill",
        scope="global",
    )

    assert any("禁用技能" in warning for warning in report["warnings"])
    assert (skill_tree / "new-skill" / "SKILL.md").is_file(), "主操作（改名）不回滚"


@pytest.mark.asyncio
async def test_personal_rename_does_not_touch_global_forbidden_rows(skill_tree, monkeypatch):
    """个人技能改名不碰权限表：那会跨用户改到别人禁用的同名技能配置。"""
    called = []
    monkeypatch.setattr(svc, "sync_publication_lineage", AsyncMock(return_value=0))
    monkeypatch.setattr(
        svc,
        "sync_forbidden_skill_permissions",
        AsyncMock(side_effect=lambda *a, **k: called.append(1)),
    )
    monkeypatch.setattr(
        "app.services.skill_rename_service.ConversationResourceService.rewrite_skill_mounts",
        AsyncMock(return_value=0),
    )

    await svc.rename_skill(
        AsyncMock(),
        current_dir=str(skill_tree / "old-skill"),
        old_skill_id="old-skill",
        new_skill_id="new-skill",
        scope="personal",
    )

    assert called == []


def test_reserved_ids_cover_every_single_segment_literal_route():
    """个人技能下所有「单段字面路由」都必须是保留字。

    这些路由注册在 ``/{skill_id}`` **之前**（FastAPI 按注册顺序匹配），同 ID 的技能详情页
    会被它们顶掉。``share-targets`` 就是这么漏的：路由存在、保留字集合里却没有它，
    于是可以创建一个永远打不开的技能。
    """
    from app.api.portal.endpoints import personal_skills, skills

    for module, label in ((personal_skills, "个人技能"), (skills, "平台技能")):
        for route in module.router.routes:
            path = str(getattr(route, "path", ""))
            segments = [seg for seg in path.strip("/").split("/") if seg]
            # 只关心会与 /{skill_id} 直接冲突的单段字面路径
            if len(segments) != 1 or segments[0].startswith("{"):
                continue
            assert segments[0] in svc.RESERVED_SKILL_IDS, (
                f"{label}字面路由 /{segments[0]} 未登记为保留字，同名技能详情页会被遮蔽"
            )
