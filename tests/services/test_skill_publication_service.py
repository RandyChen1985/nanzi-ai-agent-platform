from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.skill_publication import SkillPublication, SkillPublicationVersion
from app.services.skill_publication_service import (
    PublicationConflictError,
    PublicationNotFoundError,
    _publication_payload,
    approve_publication,
    create_personal_skill_snapshot,
    list_published_publications,
    mark_platform_skill_publications_revoked,
    materialize_snapshot,
    reject_publication,
    revoke_published_version,
    submit_personal_skill,
    withdraw_personal_skill_publication,
)


pytestmark = pytest.mark.no_infrastructure


# ---------------------------------------------------------------------------
# 会话替身：这些用例只验证状态机与磁盘副作用，不需要真数据库
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
    def __init__(self, *results):
        self._results = list(results)
        self.commits = 0
        self.flushes = 0
        self.added = []

    async def execute(self, *_args, **_kwargs):
        return self._results.pop(0) if self._results else _FakeResult([])

    async def commit(self):
        self.commits += 1

    async def flush(self):
        self.flushes += 1

    def add(self, obj):
        self.added.append(obj)


def _write_skill(root: Path, skill_id: str, *, body: str = "# v1\n") -> Path:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: test\n---\n\n{body}",
        encoding="utf-8",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")
    return skill_dir


def test_publication_models_use_stable_tables_and_required_columns():
    assert SkillPublication.__tablename__ == "skill_publications"
    assert SkillPublicationVersion.__tablename__ == "skill_publication_versions"
    assert {
        "platform_skill_id",
        "source_user_id",
        "source_personal_skill_id",
        "current_version",
        "status",
    } <= set(SkillPublication.__table__.columns.keys())
    assert {
        "publication_id",
        "version_number",
        "snapshot_path",
        "content_sha256",
        "file_count",
        "total_size",
        "review_comment",
    } <= set(SkillPublicationVersion.__table__.columns.keys())


def test_create_snapshot_is_isolated_from_personal_edits(tmp_path):
    personal_root = tmp_path / "personal"
    skill_dir = _write_skill(personal_root, "summarizer", body="# original\n")
    skills_root = tmp_path / "global"
    settings = SimpleNamespace(SKILLS_DIR=str(skills_root))
    user = {"user_id": 7, "user_name": "tester"}

    with patch(
        "app.services.skill_publication_service.get_user_personal_skills_dir",
        return_value=str(personal_root),
    ), patch("app.services.skill_publication_service.settings", settings):
        snapshot = create_personal_skill_snapshot(
            user=user,
            skill_id="summarizer",
            publication_id="publication-1",
            version_number=1,
        )

    original_hash = snapshot.content_sha256
    (skill_dir / "SKILL.md").write_text("---\nname: summarizer\n---\n\n# changed\n", encoding="utf-8")

    assert Path(snapshot.snapshot_path, "SKILL.md").read_text(encoding="utf-8").endswith("# original\n")
    assert snapshot.content_sha256 == original_hash
    assert snapshot.file_count == 2


def test_create_snapshot_rejects_hidden_files_and_symlinks(tmp_path):
    personal_root = tmp_path / "personal"
    skill_dir = _write_skill(personal_root, "unsafe")
    (skill_dir / ".env").write_text("TOKEN=secret", encoding="utf-8")
    settings = SimpleNamespace(SKILLS_DIR=str(tmp_path / "global"))

    with patch(
        "app.services.skill_publication_service.get_user_personal_skills_dir",
        return_value=str(personal_root),
    ), patch("app.services.skill_publication_service.settings", settings):
        with pytest.raises(ValueError, match="hidden"):
            create_personal_skill_snapshot(
                user={"user_id": 7},
                skill_id="unsafe",
                publication_id="publication-2",
                version_number=1,
            )


def test_materialize_snapshot_uses_unique_platform_id_and_preserves_source(tmp_path):
    snapshot_root = tmp_path / "snapshots" / "publication-3" / "v1"
    snapshot_root.mkdir(parents=True)
    (snapshot_root / "SKILL.md").write_text(
        "---\nname: Shared\nenabled: true\n---\n\n# v1\n",
        encoding="utf-8",
    )
    skills_root = tmp_path / "global"
    source_root = tmp_path / "personal" / "shared"
    source_root.mkdir(parents=True)
    (source_root / "SKILL.md").write_text("personal", encoding="utf-8")
    settings = SimpleNamespace(SKILLS_DIR=str(skills_root))

    with patch("app.services.skill_publication_service.settings", settings):
        result = materialize_snapshot(
            snapshot_path=str(snapshot_root),
            platform_skill_id="public-shared-1234abcd",
            version_number=1,
        )

    active_file = skills_root / "public-shared-1234abcd" / "SKILL.md"
    assert result.materialized_path == str(skills_root / "public-shared-1234abcd")
    assert active_file.exists()
    assert active_file.read_text(encoding="utf-8").endswith("# v1\n")
    assert (source_root / "SKILL.md").read_text(encoding="utf-8") == "personal"


# ---------------------------------------------------------------------------
# 下架（revoke）：目录必须消失，可重新发布的凭据必须保留
# ---------------------------------------------------------------------------
PLATFORM_SKILL_ID = "public-demo-12345678"


def _write_personal_skill(root: Path, skill_id: str = "demo", *, body: str = "# v1\n") -> Path:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: demo\n---\n\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def _snapshot_dir(tmp_path: Path, *, publication_id: str = "publication-1", version: int = 1) -> Path:
    """在 publication_snapshot_root() 之下造一个快照目录（清理逻辑只认这个根）。"""
    path = tmp_path / "skill_publication_snapshots" / publication_id / f"v{version}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text("---\nname: demo\n---\n\n# v1\n", encoding="utf-8")
    return path


def _pair(
    tmp_path: Path,
    *,
    publication_status: str = "PUBLISHED",
    version_status: str = "APPROVED",
    current_version: int | None = 1,
    materialized: bool = True,
    platform_skill_id: str | None = PLATFORM_SKILL_ID,
):
    skills_root = tmp_path / "global"
    skill_dir = skills_root / PLATFORM_SKILL_ID
    if materialized:
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n\n# v1\n", encoding="utf-8")
    snapshot = _snapshot_dir(tmp_path)
    publication = SkillPublication(
        id="publication-1",
        platform_skill_id=platform_skill_id,
        source_user_id=7,
        source_personal_skill_id="demo",
        name="demo",
        description="demo 描述",
        current_version=current_version,
        status=publication_status,
    )
    version = SkillPublicationVersion(
        id="version-1",
        publication_id="publication-1",
        version_number=1,
        status=version_status,
        snapshot_path=str(snapshot),
        content_sha256="0" * 64,
        file_count=1,
        total_size=10,
        submitted_by=7,
        published_at=datetime.now(),
        materialized_path=str(skill_dir) if materialized else None,
    )
    return publication, version, skill_dir, snapshot


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(SKILLS_DIR=str(tmp_path / "global"))


@pytest.mark.asyncio
async def test_revoke_removes_materialized_dir_and_keeps_republish_material(tmp_path):
    """下架的副作用：公共目录真的消失；platform_skill_id 与 snapshot_path 必须保留。"""
    publication, version, skill_dir, snapshot = _pair(tmp_path)
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        payload = await revoke_published_version(
            session, version_id="version-1", reviewer={"user_id": 1}, comment="内容不合规"
        )

    assert not skill_dir.exists(), "下架后公共技能目录必须真的消失"
    assert version.status == "REVOKED"
    assert version.materialized_path is None
    assert publication.status == "UNPUBLISHED"
    assert publication.current_version is None
    # 重新发布要靠这两个：平台技能 ID（目录名稳定）与快照（重新物化的源）
    assert publication.platform_skill_id == PLATFORM_SKILL_ID
    assert version.snapshot_path == str(snapshot) and snapshot.is_dir()
    assert publication.revoked_at is not None and publication.revoked_by == 1
    # 作者侧看到「已下架」+ 下架原因，而不是继续显示「已发布」
    assert payload["publication_status"] == "REVOKED"
    assert payload["last_review_comment"] == "内容不合规"
    assert payload["platform_skill_id"] == PLATFORM_SKILL_ID


@pytest.mark.asyncio
async def test_revoke_is_idempotent_and_returns_conflict(tmp_path):
    """重复下架：第二次必须是 409（冲突），绝不能 500。"""
    publication, version, _skill_dir, _snapshot = _pair(tmp_path, version_status="REVOKED")
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        with pytest.raises(PublicationConflictError):
            await revoke_published_version(
                session, version_id="version-1", reviewer={"user_id": 1}, comment="再次下架"
            )


@pytest.mark.parametrize(
    "publication_status,version_status,current_version",
    [
        ("UNPUBLISHED", "APPROVED", None),  # 已下架
        ("PUBLISHED", "SUPERSEDED", 2),  # 历史版本
        ("PUBLISHED", "PENDING", 1),  # 还没过审
        ("PENDING", "APPROVED", None),  # 未发布
    ],
)
@pytest.mark.asyncio
async def test_revoke_rejects_non_current_published_version(
    tmp_path, publication_status, version_status, current_version
):
    publication, version, _skill_dir, _snapshot = _pair(
        tmp_path,
        publication_status=publication_status,
        version_status=version_status,
        current_version=current_version,
    )
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        with pytest.raises(PublicationConflictError):
            await revoke_published_version(
                session, version_id="version-1", reviewer={"user_id": 1}, comment="下架"
            )


@pytest.mark.asyncio
async def test_revoke_requires_comment_and_reports_not_found(tmp_path):
    publication, version, _skill_dir, _snapshot = _pair(tmp_path)
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        with pytest.raises(ValueError):
            await revoke_published_version(
                session, version_id="version-1", reviewer={"user_id": 1}, comment="   "
            )

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        with pytest.raises(PublicationNotFoundError):
            await revoke_published_version(
                _FakeDbSession(_FakeResult([])),
                version_id="missing",
                reviewer={"user_id": 1},
                comment="下架",
            )


@pytest.mark.asyncio
async def test_revoke_ignores_materialized_path_outside_skills_root(tmp_path, caplog):
    """防目录穿越：materialized_path 越界时**绝不删它**，但也别让一次下架整体失败。

    陈旧的越界记录（历史部署换过 SKILLS_DIR）只跳过 + 告警；约定位置照删，下架照常完成。
    """
    outside = tmp_path.parent / "victim"
    outside.mkdir(exist_ok=True)
    (outside / "SKILL.md").write_text("# victim\n", encoding="utf-8")
    publication, version, skill_dir, _snapshot = _pair(tmp_path)
    version.materialized_path = str(outside)
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        payload = await revoke_published_version(
            session, version_id="version-1", reviewer={"user_id": 1}, comment="下架"
        )

    assert outside.is_dir(), "技能库之外的目录一律不删"
    assert (outside / "SKILL.md").is_file()
    assert not skill_dir.exists(), "约定位置（SKILLS_DIR/<id>）仍然要删"
    assert payload["publication_status"] == "REVOKED"
    assert "跳过不可信的物化记录" in caplog.text


@pytest.mark.asyncio
async def test_revoke_without_platform_skill_id_conflicts(tmp_path):
    publication, version, _skill_dir, _snapshot = _pair(tmp_path, platform_skill_id=None)
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        with pytest.raises(PublicationConflictError):
            await revoke_published_version(
                session, version_id="version-1", reviewer={"user_id": 1}, comment="下架"
            )


@pytest.mark.asyncio
async def test_list_published_publications_matches_admin_contract(tmp_path):
    """管理端下架列表的字段契约（少一个字段前端就渲染不出来）。"""
    publication, version, _skill_dir, _snapshot = _pair(tmp_path)
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    items = await list_published_publications(session)

    assert len(items) == 1
    assert set(items[0]) == {
        "publication_id",
        "version_id",
        "skill_id",
        "platform_skill_id",
        "name",
        "description",
        "publication_status",
        "version_number",
        "source_user_id",
        "submitted_by",
        "published_at",
        "reviewed_by",
        "file_count",
        "total_size",
    }
    assert items[0]["publication_status"] == "PUBLISHED"
    assert items[0]["platform_skill_id"] == PLATFORM_SKILL_ID


def test_publication_payload_shows_revoked_instead_of_published():
    """要求 3：作者在自己的发布记录里看到「已下架」。"""
    publication = SkillPublication(
        id="publication-1",
        platform_skill_id=PLATFORM_SKILL_ID,
        source_user_id=7,
        source_personal_skill_id="demo",
        name="demo",
        status="UNPUBLISHED",
    )
    version = SkillPublicationVersion(
        id="version-1",
        publication_id="publication-1",
        version_number=1,
        status="REVOKED",
        snapshot_path="/tmp/x",
        content_sha256="0" * 64,
        submitted_by=7,
        review_comment="内容不合规",
    )

    payload = _publication_payload(publication, version)

    assert payload["publication_status"] == "REVOKED"
    assert payload["last_review_comment"] == "内容不合规"


# ---------------------------------------------------------------------------
# 重新提交 → 重新审核：复用同一个 platform_skill_id
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resubmit_reuses_platform_skill_id_after_revoke(tmp_path):
    personal_root = tmp_path / "personal"
    _write_personal_skill(personal_root, "demo")
    publication, revoked_version, _skill_dir, _snapshot = _pair(
        tmp_path, publication_status="UNPUBLISHED", version_status="REVOKED", current_version=None
    )
    # first(): 查已有发布谱系 → 命中（复用而不是新建）；scalars().all(): 该谱系已有 v1
    session = _FakeDbSession(_FakeResult([publication]), _FakeResult([revoked_version]))

    with patch(
        "app.services.skill_publication_service.get_user_personal_skills_dir",
        return_value=str(personal_root),
    ), patch("app.services.skill_publication_service.settings", _settings(tmp_path)), patch(
        "app.services.skill_publication_service.notify_skill_publication_reviewers",
        AsyncMock(),
    ):
        payload = await submit_personal_skill(session, user={"user_id": 7}, skill_id="demo")

    assert payload["platform_skill_id"] == PLATFORM_SKILL_ID, "重新提交必须复用原平台技能 ID"
    assert payload["version_number"] == 2, "重新提交是新版本，不能覆盖已下架的 v1"
    assert payload["publication_status"] == "PENDING"
    assert publication.status == "PENDING"


@pytest.mark.asyncio
async def test_reapprove_materializes_back_to_the_same_platform_dir(tmp_path):
    """下架后重新审核：目录用同一个 platform_skill_id 重新物化出来。"""
    publication, _old_version, _skill_dir, snapshot = _pair(
        tmp_path, publication_status="UNPUBLISHED", version_status="REVOKED", current_version=None
    )
    pending = SkillPublicationVersion(
        id="version-2",
        publication_id="publication-1",
        version_number=2,
        status="PENDING",
        snapshot_path=str(snapshot),
        content_sha256="0" * 64,
        file_count=1,
        total_size=10,
        submitted_by=7,
    )
    session = _FakeDbSession(_FakeResult([(pending, publication)]), _FakeResult([]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        payload = await approve_publication(session, version_id="version-2", reviewer={"user_id": 1})

    target = tmp_path / "global" / PLATFORM_SKILL_ID
    assert target.is_dir() and (target / "SKILL.md").is_file()
    assert payload["platform_skill_id"] == PLATFORM_SKILL_ID
    assert pending.materialized_path == str(target)
    assert publication.status == "PUBLISHED" and publication.current_version == 2


@pytest.mark.asyncio
async def test_mark_platform_skill_publications_revoked_marks_active_lineage(tmp_path):
    """删除公共技能走「标记下架」：保留 platform_skill_id，供重新提交复用。"""
    publication, version, _skill_dir, _snapshot = _pair(tmp_path)
    session = _FakeDbSession(_FakeResult([publication]), _FakeResult([version]))

    count = await mark_platform_skill_publications_revoked(
        session, platform_skill_id=PLATFORM_SKILL_ID, actor_user_id=1, comment="平台技能目录已被删除"
    )

    assert count == 1
    assert publication.status == "UNPUBLISHED"
    assert publication.current_version is None
    assert publication.platform_skill_id == PLATFORM_SKILL_ID
    assert publication.revoked_by == 1
    assert version.status == "REVOKED"
    assert version.materialized_path is None
    assert version.review_comment == "平台技能目录已被删除"


@pytest.mark.asyncio
async def test_full_lifecycle_revoke_then_republish_reuses_platform_skill_id(tmp_path):
    """完整闭环：提交 → 审核通过 → 下架列表 → 下架 → 再提交 → 再审核通过。

    钉死两件事：
    1. 下架后公共目录真的消失，且从「可下架列表」里消失；
    2. 重新发布复用**同一个** ``platform_skill_id``，目录以同一个名字重新物化出来。
    """
    personal_root = tmp_path / "personal"
    _write_personal_skill(personal_root, "demo")
    session = _FakeDbSession(
        _FakeResult([]),  # submit#1：查发布谱系（无）
        _FakeResult([]),  # submit#1：查已有版本（无）
        _FakeResult([]),  # approve#1：update 历史 APPROVED → SUPERSEDED
        _FakeResult([]),  # list：无（下面手工喂）
        _FakeResult([]),  # revoke：update? （无）
        _FakeResult([None]),  # submit#2：查发布谱系（占位，下面手工喂）
        _FakeResult([]),  # submit#2：查已有版本（占位）
        _FakeResult([]),  # approve#2：update（占位）
    )

    def _settings_ns():
        return _settings(tmp_path)

    with patch(
        "app.services.skill_publication_service.get_user_personal_skills_dir",
        return_value=str(personal_root),
    ), patch("app.services.skill_publication_service.settings", _settings_ns()), patch(
        "app.services.skill_publication_service.notify_skill_publication_reviewers",
        AsyncMock(),
    ):
        # 1. 提交
        submitted = await submit_personal_skill(session, user={"user_id": 7}, skill_id="demo")
        publication = session.added[0]
        version_v1 = session.added[1]
        assert submitted["publication_status"] == "PENDING"
        assert submitted["platform_skill_id"] is None

        # 2. 审核通过（materialize 到公共技能库）
        session._results = [_FakeResult([(version_v1, publication)]), _FakeResult([])]
        approved = await approve_publication(
            session, version_id=version_v1.id, reviewer={"user_id": 1}
        )
        platform_skill_id = approved["platform_skill_id"]
        target = tmp_path / "global" / platform_skill_id
        assert platform_skill_id and target.is_dir()

        # 3. 下架列表能看到它，且用真实的 version_id
        session._results = [_FakeResult([(version_v1, publication)])]
        items = await list_published_publications(session)
        assert [item["version_id"] for item in items] == [version_v1.id]
        assert items[0]["platform_skill_id"] == platform_skill_id

        # 4. 下架
        session._results = [_FakeResult([(version_v1, publication)])]
        revoked = await revoke_published_version(
            session, version_id=version_v1.id, reviewer={"user_id": 1}, comment="内容不合规"
        )
        assert not target.exists(), "下架后公共技能目录必须真的消失"
        assert revoked["publication_status"] == "REVOKED"
        assert revoked["last_review_comment"] == "内容不合规"
        session._results = [_FakeResult([])]
        assert await list_published_publications(session) == [], "已下架的技能不该出现在可下架列表里"

        # 5. 作者重新提交
        session._results = [_FakeResult([publication]), _FakeResult([version_v1])]
        resubmitted = await submit_personal_skill(session, user={"user_id": 7}, skill_id="demo")
        version_v2 = session.added[2]
        assert resubmitted["platform_skill_id"] == platform_skill_id, "重新提交必须复用原平台技能 ID"
        assert resubmitted["version_number"] == 2

        # 6. 再次审核通过：用同一个 ID 重新物化
        session._results = [_FakeResult([(version_v2, publication)]), _FakeResult([])]
        approved_again = await approve_publication(
            session, version_id=version_v2.id, reviewer={"user_id": 1}
        )

    assert approved_again["platform_skill_id"] == platform_skill_id
    assert target.is_dir() and (target / "SKILL.md").is_file(), "重新审核必须把目录物化回来"


# ---------------------------------------------------------------------------
# 快照清理：驳回 / 撤销
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reject_cleans_orphan_snapshot_and_keeps_published_one(tmp_path):
    """驳回清理本次版本快照；已发布版本的快照必须原样保留。"""
    publication, version, _skill_dir, snapshot = _pair(
        tmp_path, publication_status="PENDING", version_status="PENDING", current_version=None
    )
    published_snapshot = _snapshot_dir(tmp_path, version=99)
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        await reject_publication(
            session, version_id="version-1", reviewer={"user_id": 1}, comment="不通过"
        )

    assert not snapshot.exists(), "驳回的版本不会再被审核，快照应被清理"
    assert published_snapshot.is_dir(), "其它版本的快照不能被顺手删掉"
    assert publication.status == "UNPUBLISHED", "没有线上版本也没有待审版本时不该是 PENDING"


@pytest.mark.asyncio
async def test_withdraw_cleans_orphan_snapshot(tmp_path):
    publication, version, _skill_dir, snapshot = _pair(
        tmp_path, publication_status="PENDING", version_status="PENDING", current_version=None
    )
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        await withdraw_personal_skill_publication(session, user={"user_id": 7}, skill_id="demo")

    assert version.status == "WITHDRAWN"
    assert not snapshot.exists()
    assert publication.status == "UNPUBLISHED"


@pytest.mark.asyncio
async def test_snapshot_cleanup_refuses_path_outside_snapshot_root(tmp_path):
    """防目录穿越：快照路径不在发布快照根之下时，一个字节都不许删。"""
    publication, version, _skill_dir, _snapshot = _pair(
        tmp_path, publication_status="PENDING", version_status="PENDING", current_version=None
    )
    victim = tmp_path / "not-a-snapshot-root" / "v1"
    victim.mkdir(parents=True)
    (victim / "SKILL.md").write_text("keep", encoding="utf-8")
    version.snapshot_path = str(victim)
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        await reject_publication(
            session, version_id="version-1", reviewer={"user_id": 1}, comment="不通过"
        )

    assert victim.is_dir() and (victim / "SKILL.md").read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_revoke_rejects_corrupted_platform_skill_id_without_touching_disk(tmp_path):
    """platform_skill_id 被人为改坏时：报状态冲突（409 口径），绝不按脏 ID 删目录。"""
    settings = _settings(tmp_path)
    skills_root = Path(settings.SKILLS_DIR)
    victim = skills_root / "safe-skill"
    victim.mkdir(parents=True)
    (victim / "SKILL.md").write_text("# safe\n", encoding="utf-8")
    publication, version, _skill_dir, _snapshot = _pair(
        tmp_path, materialized=False, platform_skill_id="../../etc"
    )
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", settings):
        with pytest.raises(PublicationConflictError):
            await revoke_published_version(
                session, version_id=version.id, reviewer={"user_id": 1}, comment="下架"
            )

    assert victim.is_dir(), "脏 ID 不能牵连技能库里的其它目录"
    assert publication.status == "PUBLISHED" and version.status == "APPROVED"


@pytest.mark.asyncio
async def test_revoke_never_deletes_a_recorded_path_belonging_to_another_skill(tmp_path):
    """materialized_path 被改成别的技能目录（脏数据）时不能连坐：只删自己的约定位置。"""
    settings = _settings(tmp_path)
    skills_root = Path(settings.SKILLS_DIR)
    bystander = skills_root / "another-skill"
    bystander.mkdir(parents=True)
    (bystander / "SKILL.md").write_text("# 别人的技能\n", encoding="utf-8")

    publication, version, own_dir, _snapshot = _pair(tmp_path)
    version.materialized_path = str(bystander)  # 人工改库/历史脏数据
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", settings):
        await revoke_published_version(
            session, version_id=version.id, reviewer={"user_id": 1}, comment="下架"
        )

    assert bystander.is_dir(), "记录位置指向别的技能时必须跳过，不能误删"
    assert not own_dir.exists(), "自己的约定位置照常删除"


# ---------------------------------------------------------------------------
# 审查回归（第四轮 review）：物化失败的磁盘安全 / 重新提交后的下架能力 / 状态流转并发保护
# ---------------------------------------------------------------------------


def test_materialize_staging_failure_keeps_live_platform_skill(tmp_path):
    """★ 物化**准备阶段**失败绝不能删掉正在线上服务的平台技能目录。

    旧实现的 except 分支无条件 ``rmtree(target)``：拷快照/校验（甚至 ``os.replace`` 目标
    到备份那一步自身）失败时，target 还是**正在服务的那份线上内容**，却被删掉且没有备份
    可恢复——"审核通过失败"的代价是线上技能消失。
    """
    skills_root = tmp_path / "global"
    live = skills_root / PLATFORM_SKILL_ID
    live.mkdir(parents=True)
    (live / "SKILL.md").write_text("LIVE-CONTENT", encoding="utf-8")
    snapshot = _snapshot_dir(tmp_path)

    with (
        patch("app.services.skill_publication_service.settings", _settings(tmp_path)),
        patch(
            "app.services.skill_publication_service._copy_tree_contents",
            side_effect=RuntimeError("disk full"),
        ),
    ):
        with pytest.raises(RuntimeError):
            materialize_snapshot(
                snapshot_path=str(snapshot),
                platform_skill_id=PLATFORM_SKILL_ID,
                version_number=2,
            )

    assert live.is_dir(), "准备阶段失败时线上目录必须原样保留"
    assert (live / "SKILL.md").read_text(encoding="utf-8") == "LIVE-CONTENT"
    # 临时目录不能留在技能库里（否则会被运行时当技能扫到）
    leftovers = [p.name for p in skills_root.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_materialize_write_failure_restores_previous_content(tmp_path):
    """写元数据失败发生在"新内容已就位"之后：必须回滚成上一版内容，而不是留个空目录。"""
    skills_root = tmp_path / "global"
    live = skills_root / PLATFORM_SKILL_ID
    live.mkdir(parents=True)
    (live / "SKILL.md").write_text("LIVE-CONTENT", encoding="utf-8")
    snapshot = _snapshot_dir(tmp_path)

    with (
        patch("app.services.skill_publication_service.settings", _settings(tmp_path)),
        patch(
            "app.services.skill_publication_service._write_platform_metadata",
            side_effect=RuntimeError("read-only fs"),
        ),
    ):
        with pytest.raises(RuntimeError):
            materialize_snapshot(
                snapshot_path=str(snapshot),
                platform_skill_id=PLATFORM_SKILL_ID,
                version_number=2,
            )

    assert (live / "SKILL.md").read_text(encoding="utf-8") == "LIVE-CONTENT", "必须回滚到上一版"
    assert [p.name for p in skills_root.iterdir() if p.name.startswith(".")] == []


@pytest.mark.asyncio
async def test_revoke_still_works_after_a_new_version_was_submitted(tmp_path):
    """★ 已发布谱系又提交了一版（status 被写成 PENDING）时，线上版本仍然必须能下架。

    下架判据此前要求 ``publication.status == "PUBLISHED"``，而"作者提交新版本"会把这条
    汇总状态改成 PENDING → 管理员再也下架不掉正在线上服务的那一版（真实故障）。
    现在只按版本事实判定：版本 APPROVED 且版本号 == current_version。
    """
    publication, version, skill_dir, _snapshot = _pair(
        tmp_path, publication_status="PENDING", version_status="APPROVED", current_version=1
    )
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    with patch("app.services.skill_publication_service.settings", _settings(tmp_path)):
        payload = await revoke_published_version(
            session, version_id="version-1", reviewer={"user_id": 1}, comment="内容不合规"
        )

    assert not skill_dir.exists(), "下架必须真的删掉线上目录"
    assert version.status == "REVOKED"
    assert payload["publication_status"] == "REVOKED"


@pytest.mark.asyncio
async def test_list_published_shows_version_clobbered_to_pending(tmp_path):
    """可下架列表同样只看版本事实：汇总状态被写成 PENDING 的在线技能仍要出现在列表里。"""
    publication, version, _skill_dir, _snapshot = _pair(
        tmp_path, publication_status="PENDING", version_status="APPROVED", current_version=1
    )
    session = _FakeDbSession(_FakeResult([(version, publication)]))

    rows = await list_published_publications(session)

    assert [row["publication_id"] for row in rows] == ["publication-1"]
    assert rows[0]["platform_skill_id"] == PLATFORM_SKILL_ID


@pytest.mark.asyncio
async def test_submit_resubmission_keeps_publication_published(tmp_path):
    """★ 已上线谱系重新提交新版本时，汇总状态必须保持 PUBLISHED（否则下架能力丢失）。

    "有待审新版本"由版本行表达（payload.pending_version），不该把线上状态压成 PENDING。
    """
    publication, approved, _skill_dir, _snapshot = _pair(tmp_path)
    publication.status = "PUBLISHED"
    personal_root = tmp_path / "personal"
    _write_personal_skill(personal_root, "demo", body="\n# v2\n")
    session = _FakeDbSession(
        _FakeResult([publication]),  # 查已有谱系
        _FakeResult([approved]),  # 查已有版本
    )

    with (
        patch("app.services.skill_publication_service.settings", _settings(tmp_path)),
        patch(
            "app.services.skill_publication_service.get_user_personal_skills_dir",
            return_value=str(personal_root),
        ),
        patch(
            "app.services.skill_publication_service.notify_skill_publication_reviewers",
            new_callable=AsyncMock,
        ),
    ):
        payload = await submit_personal_skill(
            session, user={"user_id": 7}, skill_id="demo"
        )

    assert publication.status == "PUBLISHED", "线上还有已发布版本时不能把汇总状态压成 PENDING"
    assert payload["pending_version"] == 2
    assert payload["current_public_version"] == 1


def test_state_transitions_take_row_locks():
    """★ 四个状态流转都要行锁：并发"通过/驳回/下架/撤销"不能再各自读到 PENDING 各写一半。

    两位审核人同时点「通过」时，没有行锁就会双双物化（目录互相覆盖、版本状态互相覆盖），
    最终库里写着谁也不知道的一版。锁在事务里，第二个请求重读后会拿到 409。
    """
    import ast
    import inspect

    from app.services import skill_publication_service as svc

    expected = {
        "approve_publication",
        "reject_publication",
        "revoke_published_version",
        "withdraw_personal_skill_publication",
    }
    for name in expected:
        source = inspect.getsource(getattr(svc, name))
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "with_for_update"
        ]
        assert calls, f"{name} 缺少行锁（with_for_update）"


class _RecordingSession(_FakeDbSession):
    """记录下发给 DB 的语句：假 session 不执行 SQL，只能靠编译后的语句校验过滤条件。"""

    def __init__(self, *results):
        super().__init__(*results)
        self.statements = []

    async def execute(self, *args, **kwargs):
        if args:
            self.statements.append(args[0])
        return await super().execute(*args, **kwargs)


@pytest.mark.asyncio
async def test_revoke_query_covers_legacy_pending_status_with_live_version(tmp_path):
    """``status="PENDING"`` 但仍有 ``current_version`` 的谱系同样是「在线」，必须一起下架。

    这条状态是历史实现留下的：作者又提交了一版就把汇总状态改写成 PENDING，而线上那版
    仍在生效。删除平台技能目录时若只按 ``status == "PUBLISHED"`` 过滤，会漏掉它——目录
    已删、库里仍写着在线已发布，重新提交也拿不回同一个 platform_skill_id。
    （``list_published_publications`` / ``_assert_current_published`` 早就改成了同一判据。）
    """
    publication, version, _skill_dir, _snapshot = _pair(tmp_path)
    publication.status = "PENDING"  # 又被提交了一版，汇总状态被改写
    assert publication.current_version == 1
    session = _RecordingSession(_FakeResult([publication]), _FakeResult([version]))

    count = await mark_platform_skill_publications_revoked(
        session, platform_skill_id=PLATFORM_SKILL_ID, actor_user_id=1
    )

    sql = str(session.statements[0])
    assert "current_version IS NOT NULL" in sql, "过滤条件必须按版本事实，而不是只看汇总状态"
    assert count == 1
    assert publication.status == "UNPUBLISHED"
    assert publication.current_version is None
    assert version.status == "REVOKED"


@pytest.mark.asyncio
async def test_revoke_leaves_never_published_lineage_untouched(tmp_path):
    """从未通过审核（无 current_version）的谱系不该被「删除技能」顺手改状态。"""
    publication, version, _skill_dir, _snapshot = _pair(tmp_path)
    publication.status = "PENDING"
    publication.current_version = None
    version.status = "PENDING"
    session = _RecordingSession(_FakeResult([]), _FakeResult([]))

    count = await mark_platform_skill_publications_revoked(
        session, platform_skill_id=PLATFORM_SKILL_ID, actor_user_id=1
    )

    sql = str(session.statements[0])
    assert "current_version IS NOT NULL" in sql  # 空结果来自 DB 过滤，不是靠函数早退
    assert count == 0
    assert publication.status == "PENDING"
    assert version.status == "PENDING"
