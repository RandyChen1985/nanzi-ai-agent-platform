"""个人技能分享服务：文件快照 / 落地 / 状态流转。

与发布链路共用同一套「只复制、不移动」的快照与原子落地语义，因此这里只验证
磁盘副作用与状态机，不依赖数据库。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.skill_share import SkillShare
from app.services import skill_share_service


pytestmark = pytest.mark.no_infrastructure


def _settings(tmp_path: Path):
    skills_root = tmp_path / "global"
    skills_root.mkdir(exist_ok=True)
    return SimpleNamespace(SKILLS_DIR=str(skills_root))


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


def test_share_model_uses_stable_table_and_columns():
    assert SkillShare.__tablename__ == "skill_shares"
    assert {
        "id",
        "source_user_id",
        "source_skill_id",
        "name",
        "description",
        "message",
        "target_user_id",
        "status",
        "snapshot_path",
        "content_sha256",
        "file_count",
        "total_size",
        "accepted_skill_id",
        "created_at",
        "responded_at",
    } <= set(SkillShare.__table__.columns.keys())


def test_create_share_snapshot_copies_tree(tmp_path):
    settings = _settings(tmp_path)
    source = _write_skill(tmp_path / "personal", "summarizer")
    share_id = "share-1"

    with patch.object(skill_share_service, "settings", settings):
        snapshot = skill_share_service.create_share_snapshot(
            source_path=str(source), share_id=share_id
        )

    snapshot_dir = Path(snapshot.snapshot_path)
    assert snapshot_dir.is_dir()
    assert (snapshot_dir / "SKILL.md").is_file()
    assert (snapshot_dir / "references" / "guide.md").read_text(encoding="utf-8") == "guide"
    assert snapshot.file_count == 2
    assert snapshot.total_size > 0
    assert len(snapshot.content_sha256) == 64
    # 源目录保持原样（只是复制，不移动）
    assert (source / "SKILL.md").is_file()


def test_create_share_snapshot_rejects_hidden_file(tmp_path):
    settings = _settings(tmp_path)
    source = _write_skill(tmp_path / "personal", "leaky")
    (source / ".env").write_text("secret", encoding="utf-8")

    with patch.object(skill_share_service, "settings", settings):
        with pytest.raises(ValueError):
            skill_share_service.create_share_snapshot(
                source_path=str(source), share_id="share-hidden"
            )

    assert not (Path(settings.SKILLS_DIR).parent / "skill_share_snapshots" / "share-hidden").exists()


def test_create_share_snapshot_replaces_existing_when_refreshing(tmp_path):
    """刷新同一条待接受分享时，旧快照必须被新内容替换，而不是叠加两层文件。"""
    settings = _settings(tmp_path)
    source = _write_skill(tmp_path / "personal", "summarizer", body="# v1\n")

    with patch.object(skill_share_service, "settings", settings):
        first = skill_share_service.create_share_snapshot(
            source_path=str(source), share_id="share-refresh"
        )
        (source / "SKILL.md").write_text("# v2\n", encoding="utf-8")
        second = skill_share_service.create_share_snapshot(
            source_path=str(source), share_id="share-refresh"
        )

    assert first.snapshot_path == second.snapshot_path
    assert Path(second.snapshot_path).joinpath("SKILL.md").read_text(encoding="utf-8") == "# v2\n"
    assert second.content_sha256 != first.content_sha256


def test_materialize_share_snapshot_uses_suffix_on_conflict(tmp_path):
    settings = _settings(tmp_path)
    snapshot_root = Path(settings.SKILLS_DIR).parent / "skill_share_snapshots"
    source = _write_skill(tmp_path / "personal", "summarizer")
    with patch.object(skill_share_service, "settings", settings):
        snapshot = skill_share_service.create_share_snapshot(
            source_path=str(source), share_id="share-conflict"
        )

    target_root = tmp_path / "receiver" / "skills"
    target_root.mkdir(parents=True)
    # 接收人已有一个同名技能
    (target_root / "summarizer").mkdir()
    (target_root / "summarizer" / "SKILL.md").write_text("mine", encoding="utf-8")

    result = skill_share_service.materialize_share_snapshot(
        snapshot_path=snapshot.snapshot_path,
        skills_root=str(target_root),
        preferred_skill_id="summarizer",
    )

    assert result.skill_id == "summarizer-2"
    assert (target_root / "summarizer" / "SKILL.md").read_text(encoding="utf-8") == "mine"
    copied = target_root / "summarizer-2"
    assert copied.is_dir()
    assert (copied / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert (copied / "references" / "guide.md").is_file()

    # 再冲突一次 → -3
    result2 = skill_share_service.materialize_share_snapshot(
        snapshot_path=snapshot.snapshot_path,
        skills_root=str(target_root),
        preferred_skill_id="summarizer",
    )
    assert result2.skill_id == "summarizer-3"


def test_materialize_share_snapshot_rejects_missing_snapshot(tmp_path):
    target_root = tmp_path / "receiver" / "skills"
    target_root.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        skill_share_service.materialize_share_snapshot(
            snapshot_path=str(tmp_path / "nope"),
            skills_root=str(target_root),
            preferred_skill_id="summarizer",
        )


# ---------------------------------------------------------------------------
# 状态流转：分享 / 撤回 / 接受 / 拒绝
# ---------------------------------------------------------------------------
from unittest.mock import AsyncMock  # noqa: E402

from app.models.user import User  # noqa: E402
from app.services.portal_notification_service import PortalNotificationService  # noqa: E402


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

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeDbSession:
    def __init__(self, *results):
        self._results = list(results)
        self.added = []
        self.flushed = 0

    async def execute(self, *_args, **_kwargs):
        return self._results.pop(0) if self._results else _FakeResult([])

    async def flush(self):
        self.flushed += 1

    def add(self, obj):
        self.added.append(obj)


def _share_row(**overrides):
    row = SkillShare(
        id="share-1",
        source_user_id=7,
        source_skill_id="summarizer",
        name="Summarizer",
        description="desc",
        message="try it",
        target_user_id=9,
        status="PENDING",
        content_sha256="a" * 64,
        file_count=1,
        total_size=10,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _notification_mock():
    return patch.object(PortalNotificationService, "create", new=AsyncMock())


async def test_share_skill_creates_pending_with_snapshot_and_notification(tmp_path):
    settings = _settings(tmp_path)
    source_root = tmp_path / "personal"
    _write_skill(source_root, "summarizer")
    user = {"user_id": 7, "user_name": "alice"}
    target = User(id=9, user_name="bob", real_name="Bob", status=1)
    session = _FakeDbSession(_FakeResult([target]), _FakeResult([]))

    with patch.object(skill_share_service, "settings", settings), patch.object(
        skill_share_service, "get_user_personal_skills_dir", return_value=str(source_root)
    ), _notification_mock() as notify:
        result = await skill_share_service.share_skill(
            session, user=user, skill_id="summarizer", target_user_ids=[9], message="try it"
        )

    assert len(result["created"]) == 1
    assert result["refreshed"] == []
    row = session.added[0]
    assert row.status == "PENDING"
    assert row.target_user_id == 9
    assert row.message == "try it"
    assert row.source_skill_id == "summarizer"
    assert Path(row.snapshot_path).is_dir()
    assert (Path(row.snapshot_path) / "SKILL.md").is_file()
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["user_id"] == 9
    assert "分享" in notify.await_args.kwargs["title"]


async def test_share_skill_rejects_self(tmp_path):
    settings = _settings(tmp_path)
    session = _FakeDbSession()
    with patch.object(skill_share_service, "settings", settings):
        with pytest.raises(ValueError):
            await skill_share_service.share_skill(
                session, user={"user_id": 7, "user_name": "alice"},
                skill_id="summarizer", target_user_ids=[7], message=None,
            )


async def test_share_skill_rejects_unknown_target(tmp_path):
    settings = _settings(tmp_path)
    source_root = tmp_path / "personal"
    _write_skill(source_root, "summarizer")
    session = _FakeDbSession(_FakeResult([]), _FakeResult([]))
    with patch.object(skill_share_service, "settings", settings), patch.object(
        skill_share_service, "get_user_personal_skills_dir", return_value=str(source_root)
    ):
        with pytest.raises(ValueError):
            await skill_share_service.share_skill(
                session, user={"user_id": 7, "user_name": "alice"},
                skill_id="summarizer", target_user_ids=[404], message=None,
            )


async def test_share_skill_refreshes_existing_pending(tmp_path):
    settings = _settings(tmp_path)
    source_root = tmp_path / "personal"
    _write_skill(source_root, "summarizer", body="# v1\n")
    user = {"user_id": 7, "user_name": "alice"}
    target = User(id=9, user_name="bob", real_name="Bob", status=1)
    existing = _share_row(snapshot_path=str(tmp_path / "old"))
    session = _FakeDbSession(_FakeResult([target]), _FakeResult([existing]))

    with patch.object(skill_share_service, "settings", settings), patch.object(
        skill_share_service, "get_user_personal_skills_dir", return_value=str(source_root)
    ), _notification_mock():
        result = await skill_share_service.share_skill(
            session, user=user, skill_id="summarizer", target_user_ids=[9], message="updated"
        )

    assert result["created"] == []
    assert len(result["refreshed"]) == 1
    assert session.added == []  # 刷新复用同一行，不新增记录
    assert existing.message == "updated"
    assert existing.snapshot_path == str(Path(settings.SKILLS_DIR).parent / "skill_share_snapshots" / existing.id)
    assert Path(existing.snapshot_path).is_dir()


async def test_accept_share_copies_snapshot_and_marks_accepted(tmp_path):
    settings = _settings(tmp_path)
    source_root = tmp_path / "personal"
    _write_skill(source_root, "summarizer")
    with patch.object(skill_share_service, "settings", settings):
        snapshot = skill_share_service.create_share_snapshot(
            source_path=str(_write_skill(tmp_path / "src", "summarizer")), share_id="share-accept"
        )

    row = _share_row(
        id="share-accept",
        snapshot_path=snapshot.snapshot_path,
        content_sha256=snapshot.content_sha256,
        target_user_id=9,
    )
    receiver_root = tmp_path / "receiver" / "skills"
    receiver_root.mkdir(parents=True)
    source_user = User(id=7, user_name="alice", real_name="Alice", status=1)
    session = _FakeDbSession(_FakeResult([row]), _FakeResult([source_user]))

    with patch.object(skill_share_service, "settings", settings), patch.object(
        skill_share_service, "get_user_personal_skills_dir", return_value=str(receiver_root)
    ), _notification_mock() as notify:
        result = await skill_share_service.accept_share(
            session, user={"user_id": 9, "user_name": "bob"}, share_id="share-accept"
        )

    assert row.status == "ACCEPTED"
    assert row.accepted_skill_id == "summarizer"
    assert row.responded_at is not None
    assert row.snapshot_path is None
    assert result["accepted_skill_id"] == "summarizer"
    assert (receiver_root / "summarizer" / "SKILL.md").is_file()
    assert not Path(snapshot.snapshot_path).exists()
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["user_id"] == 7


async def test_accept_share_rejects_non_target(tmp_path):
    row = _share_row(target_user_id=99)
    session = _FakeDbSession(_FakeResult([row]))
    with pytest.raises(skill_share_service.SharePermissionError):
        await skill_share_service.accept_share(
            session, user={"user_id": 9, "user_name": "bob"}, share_id="share-1"
        )


async def test_accept_share_rejects_already_handled(tmp_path):
    row = _share_row(status="ACCEPTED")
    session = _FakeDbSession(_FakeResult([row]))
    with pytest.raises(skill_share_service.ShareConflictError):
        await skill_share_service.accept_share(
            session, user={"user_id": 9, "user_name": "bob"}, share_id="share-1"
        )


async def test_accept_share_with_missing_snapshot_raises_conflict(tmp_path):
    settings = _settings(tmp_path)
    row = _share_row(snapshot_path=str(tmp_path / "gone"), target_user_id=9)
    receiver_root = tmp_path / "receiver" / "skills"
    receiver_root.mkdir(parents=True)
    session = _FakeDbSession(_FakeResult([row]))
    with patch.object(skill_share_service, "settings", settings), patch.object(
        skill_share_service, "get_user_personal_skills_dir", return_value=str(receiver_root)
    ):
        with pytest.raises(skill_share_service.ShareConflictError):
            await skill_share_service.accept_share(
                session, user={"user_id": 9, "user_name": "bob"}, share_id="share-1"
            )
    assert row.status == "PENDING"


async def test_withdraw_share_only_owner_and_removes_snapshot(tmp_path):
    settings = _settings(tmp_path)
    source_root = tmp_path / "personal"
    _write_skill(source_root, "summarizer")
    with patch.object(skill_share_service, "settings", settings):
        snapshot = skill_share_service.create_share_snapshot(
            source_path=str(_write_skill(tmp_path / "src2", "summarizer")), share_id="share-withdraw"
        )
    row = _share_row(id="share-withdraw", snapshot_path=snapshot.snapshot_path, source_user_id=7)
    session = _FakeDbSession(_FakeResult([row]))
    with patch.object(skill_share_service, "settings", settings):
        with pytest.raises(skill_share_service.SharePermissionError):
            await skill_share_service.withdraw_share(
                session, user={"user_id": 8, "user_name": "eve"}, share_id="share-withdraw"
            )

    session = _FakeDbSession(_FakeResult([row]))
    with patch.object(skill_share_service, "settings", settings), _notification_mock() as notify:
        result = await skill_share_service.withdraw_share(
            session, user={"user_id": 7, "user_name": "alice"}, share_id="share-withdraw"
        )
    assert row.status == "WITHDRAWN"
    assert row.snapshot_path is None
    assert not Path(snapshot.snapshot_path).exists()
    assert result["status"] == "WITHDRAWN"
    assert notify.await_args.kwargs["user_id"] == 9


async def test_reject_share_marks_rejected(tmp_path):
    settings = _settings(tmp_path)
    source_root = tmp_path / "personal"
    _write_skill(source_root, "summarizer")
    with patch.object(skill_share_service, "settings", settings):
        snapshot = skill_share_service.create_share_snapshot(
            source_path=str(_write_skill(tmp_path / "src3", "summarizer")), share_id="share-reject"
        )
    row = _share_row(id="share-reject", snapshot_path=snapshot.snapshot_path, target_user_id=9)
    session = _FakeDbSession(_FakeResult([row]), _FakeResult([User(id=7, user_name="alice", real_name="Alice", status=1)]))
    with patch.object(skill_share_service, "settings", settings), _notification_mock():
        result = await skill_share_service.reject_share(
            session, user={"user_id": 9, "user_name": "bob"}, share_id="share-reject"
        )
    assert row.status == "REJECTED"
    assert result["status"] == "REJECTED"
    assert not Path(snapshot.snapshot_path).exists()


async def test_share_skill_rejects_empty_targets(tmp_path):
    user = {"user_id": 7, "user_name": "alice"}
    session = _FakeDbSession(_FakeResult([]), _FakeResult([]))

    with pytest.raises(ValueError, match="接收人"):
        await skill_share_service.share_skill(
            session, user=user, skill_id="summarizer", target_user_ids=[], message=None
        )


async def test_share_skill_rejects_too_many_targets(tmp_path):
    from app.services.skill_share_service import MAX_SHARE_TARGETS

    user = {"user_id": 7, "user_name": "alice"}
    session = _FakeDbSession(_FakeResult([]), _FakeResult([]))
    targets = list(range(100, 100 + MAX_SHARE_TARGETS + 1))

    with pytest.raises(ValueError, match="最多"):
        await skill_share_service.share_skill(
            session, user=user, skill_id="summarizer", target_user_ids=targets, message=None
        )


async def test_share_skill_rejects_too_long_message(tmp_path):
    from app.services.skill_share_service import MAX_MESSAGE_LENGTH

    user = {"user_id": 7, "user_name": "alice"}
    session = _FakeDbSession(_FakeResult([]), _FakeResult([]))

    with pytest.raises(ValueError, match="附言"):
        await skill_share_service.share_skill(
            session,
            user=user,
            skill_id="summarizer",
            target_user_ids=[9],
            message="x" * (MAX_MESSAGE_LENGTH + 1),
        )


class _ScalarCount:
    """count() 查询只用到 .scalar()。"""

    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


async def test_list_received_shares_applies_default_limit():
    row = _share_row(status="ACCEPTED", accepted_skill_id="summarizer-2")
    session = _FakeDbSession(_FakeResult([(row, User(id=7, user_name="alice"))]), _ScalarCount(1))
    statements = []
    original = session.execute

    async def capture(stmt, *args, **kwargs):
        statements.append(stmt)
        return await original(stmt, *args, **kwargs)

    session.execute = capture

    result = await skill_share_service.list_received_shares(
        session, user={"user_id": 9}, status="all"
    )

    assert result["items"][0]["id"] == "share-1"
    assert result["pending_count"] == 1
    # 「全部」不能把历史记录全量吐给前端，默认必须带 LIMIT
    assert getattr(statements[0], "_limit_clause", None) is not None


async def test_list_received_shares_respects_custom_limit_and_caps_it():
    session = _FakeDbSession(_FakeResult([]), _ScalarCount(0))
    statements = []
    original = session.execute

    async def capture(stmt, *args, **kwargs):
        statements.append(stmt)
        return await original(stmt, *args, **kwargs)

    session.execute = capture

    await skill_share_service.list_received_shares(
        session, user={"user_id": 9}, status="all", limit=9999
    )

    limit_clause = getattr(statements[0], "_limit_clause", None)
    assert limit_clause is not None
    assert limit_clause.value == skill_share_service.MAX_LIST_LIMIT
