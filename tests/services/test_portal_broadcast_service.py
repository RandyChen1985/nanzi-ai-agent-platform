"""管理员站内信群发的解析与发送契约。

这些用例刻意不碰真实数据库（项目 conftest 连的是开发库）：群发一次会写入成百
上千行，跑测试时真发出去既污染数据又无法回收。这里用假 session 固定住三件事：

1. 部门范围按物化路径前缀匹配含下级（不是"只发直属成员"）；
2. 三个维度取并集并去重，只保留启用账号；
3. ``confirm_total`` 与重新解析的结果不一致时必须拒绝发送。
"""

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.no_infrastructure


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def scalars(self):
        return _FakeScalars(self._rows)

    def scalar(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """按调用顺序返回预设结果，并记录执行过的语句与参数。

    也模拟 ``add`` / ``flush`` / ``commit`` / ``rollback``：群发会在同一事务里
    先写发送记录、拿自增 id 写进消息 metadata，再批量插站内信。
    """

    def __init__(self, results):
        self._results = list(results)
        self.statements = []
        self.parameters = []
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.commit = AsyncMock(side_effect=self._mark_commit)
        self.rollback = AsyncMock(side_effect=self._mark_rollback)

    def _mark_commit(self):
        self.committed = True

    def _mark_rollback(self):
        self.rolled_back = True

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        # 模拟自增主键：记录需要 id 才能写进消息 metadata
        for index, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = index

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        self.parameters.append(args[0] if args else kwargs.get("parameters"))
        if not self._results:
            return _FakeResult([])
        return self._results.pop(0)


def _audience(**kwargs):
    from app.schemas.portal_broadcast import BroadcastAudience

    return BroadcastAudience(**kwargs)


def test_merge_recipient_scope_unions_and_dedupes():
    from app.services.portal_broadcast_service import merge_recipient_scope

    merged = merge_recipient_scope([1, 2], {2, 3}, {3, 4})
    assert merged == {1, 2, 3, 4}


def test_merge_recipient_scope_drops_invalid_values():
    from app.services.portal_broadcast_service import merge_recipient_scope

    merged = merge_recipient_scope([0, -1, "abc", None, 5], [], [])
    assert merged == {5}


def test_audience_normalizes_and_dedupes_ids():
    audience = _audience(user_ids=[3, 3, 0, -2, "7"], dept_ids=[1, 1], role_ids=[])

    assert audience.user_ids == [3, 7]
    assert audience.dept_ids == [1]
    assert audience.is_empty() is False


def test_audience_is_empty_when_nothing_selected():
    assert _audience().is_empty() is True


def test_send_request_strips_and_validates():
    from app.schemas.portal_broadcast import BroadcastSendRequest

    payload = BroadcastSendRequest(
        audience={"dept_ids": [7]},
        title="  系统维护通知  ",
        content="  今晚 22:00 维护  ",
        level="NOT-A-LEVEL",
        confirm_total=3,
    )
    assert payload.title == "系统维护通知"
    assert payload.content == "今晚 22:00 维护"
    # 非法级别归一成 info，而不是把脏值写进库让前端渲染不出颜色
    assert payload.level == "info"


def test_send_request_rejects_blank_content():
    from pydantic import ValidationError

    from app.schemas.portal_broadcast import BroadcastSendRequest

    with pytest.raises(ValidationError):
        BroadcastSendRequest(
            audience={"dept_ids": [7]}, title="标题", content="   ", level="info"
        )


@pytest.mark.asyncio
async def test_resolve_recipients_matches_dept_subtree_and_filters_disabled():
    from app.services.portal_broadcast_service import PortalBroadcastService

    session = _FakeSession(
        [
            _FakeResult([(7, "/1/7/")]),          # 勾选的部门及其 path
            _FakeResult([7, 23]),                 # 子树部门 ID
            _FakeResult([101, 102]),              # 部门成员
            _FakeResult([102, 103]),              # 角色成员
            _FakeResult([(101, "zhangsan", None), (102, "lisi", "李四")]),  # 启用的账号
        ]
    )

    resolved = await PortalBroadcastService.resolve_recipients(
        session, _audience(dept_ids=[7], role_ids=[2])
    )

    assert [item["user_id"] for item in resolved] == [101, 102]
    assert resolved[1]["real_name"] == "李四"
    # 部门那次查询必须用 path 前缀匹配，否则「含下级」就退化成只发直属成员
    subtree_sql = str(session.statements[1])
    assert "ai_agent_departments.path LIKE" in subtree_sql


@pytest.mark.asyncio
async def test_resolve_recipients_returns_empty_without_scope():
    from app.services.portal_broadcast_service import PortalBroadcastService

    session = _FakeSession([])

    assert await PortalBroadcastService.resolve_recipients(session, _audience()) == []
    assert session.statements == []


@pytest.mark.asyncio
async def test_send_rejects_when_audience_changed_since_preview():
    from app.services.portal_broadcast_service import (
        BroadcastAudienceChanged,
        PortalBroadcastService,
    )

    session = _FakeSession(
        [
            _FakeResult([(101, "zhangsan", None), (102, "lisi", "李四")]),
        ]
    )

    with pytest.raises(BroadcastAudienceChanged) as exc:
        await PortalBroadcastService.send(
            session,
            operator={"user_id": 1, "user_name": "admin"},
            audience=_audience(user_ids=[101, 102]),
            title="维护通知",
            content="正文",
            level="info",
            confirm_total=1,
        )

    assert exc.value.expected == 1
    assert exc.value.actual == 2
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_writes_one_row_per_recipient_with_broadcast_metadata():
    from app.services.portal_broadcast_service import PortalBroadcastService

    session = _FakeSession(
        [
            _FakeResult([(101, "zhangsan", None), (102, "lisi", "李四")]),
        ]
    )

    sent = await PortalBroadcastService.send(
        session,
        operator={"user_id": 9, "user_name": "admin", "real_name": "管理员"},
        audience=_audience(user_ids=[101, 102]),
        title="系统维护通知",
        content="今晚 22:00 维护",
        level="warning",
        confirm_total=2,
    )

    assert sent == 2
    session.commit.assert_awaited_once()

    # 同一事务里先写发送记录（历史列表 + 回填重发都靠它）
    from app.models.portal_broadcast import PortalBroadcastRecord

    assert len(session.added) == 1
    record = session.added[0]
    assert isinstance(record, PortalBroadcastRecord)
    assert record.title == "系统维护通知"
    assert record.content == "今晚 22:00 维护"
    assert record.level == "warning"
    assert record.recipient_count == 2
    assert record.audience_user_ids == [101, 102]
    assert record.sender_user_id == 9

    # 最后一次 execute 是批量插入：取它带的行参数校验落库内容
    rows = session.parameters[-1]
    assert isinstance(rows, list) and len(rows) == 2
    assert [row["user_id"] for row in rows] == [101, 102]
    assert {row["category"] for row in rows} == {"system_broadcast"}
    assert {row["level"] for row in rows} == {"warning"}
    assert {row["title"] for row in rows} == {"系统维护通知"}
    assert rows[0]["meta_info"]["source"] == "admin_broadcast"
    assert rows[0]["meta_info"]["sender_user_id"] == 9
    assert rows[0]["meta_info"]["audience"]["recipient_total"] == 2
    # 消息能反查是哪次群发
    assert rows[0]["meta_info"]["broadcast_record_id"] == record.id
    # 未读：群发消息落库时 read_at 必须是 NULL，否则用户看不到红点
    assert all(row["read_at"] is None for row in rows)


def _dependency_calls(route) -> set:
    calls = set()

    def walk(dependant):
        for dep in dependant.dependencies:
            calls.add(dep.call)
            walk(dep)

    walk(route.dependant)
    return calls


def test_every_broadcast_endpoint_requires_admin():
    """群发端点的唯一权限口径是 require_admin。

    这条用例防的是「以后新增一个端点忘了加鉴权」：只要 router 里多出第三个路径，
    交集断言就会失败，而不是等上线后由普通用户发现能群发。
    """
    from app.api.portal.endpoints import broadcast
    from app.core.dependencies import require_admin

    routes = {route.path: route for route in broadcast.router.routes}
    assert set(routes) == {"/preview", "/send", "/records", "/records/{record_id}"}

    for path, route in routes.items():
        assert require_admin in _dependency_calls(route), f"{path} 缺少 require_admin 依赖"


def test_build_rows_actually_insertable_into_real_table():
    """用真实表结构（SQLite 内存库）跑一次批量插入。

    假 session 无法发现「参数键名与列名不一致」这类编译期错误——那正是
    ``meta_info`` / ``metadata`` 最容易写反的地方，所以这里真的插一次。
    """
    from datetime import datetime

    from sqlalchemy import create_engine, insert, select

    from app.core.orm import Base
    from app.models.portal_notification import PortalNotification
    from app.models.user import User
    from app.services.portal_broadcast_service import PortalBroadcastService

    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[User.__table__, PortalNotification.__table__]
    )

    rows = PortalBroadcastService._build_rows(
        recipients=[{"user_id": 101, "user_name": "a"}, {"user_id": 102, "user_name": "b"}],
        title="系统维护通知",
        content="今晚 22:00 维护",
        level="warning",
        metadata={"source": "admin_broadcast"},
    )
    # SQLite 的 BigInteger 主键不自增，显式给 id 才能验证行本身
    for index, row in enumerate(rows, start=1):
        row["id"] = index

    with engine.begin() as conn:
        conn.execute(insert(PortalNotification), rows)

    with engine.connect() as conn:
        stored = conn.execute(
            select(PortalNotification.user_id, PortalNotification.title, PortalNotification.read_at)
            .order_by(PortalNotification.user_id)
        ).all()

    assert [row[0] for row in stored] == [101, 102]
    assert {row[1] for row in stored} == {"系统维护通知"}
    assert all(row[2] is None for row in stored)


def _record(**overrides):
    from datetime import datetime

    from app.models.portal_broadcast import PortalBroadcastRecord

    values = {
        "id": 5,
        "sender_user_id": 9,
        "sender_user_name": "admin",
        "sender_real_name": "管理员",
        "title": "系统维护通知",
        "content": "第一行\n\n第二行",
        "level": "warning",
        "audience_user_ids": [101, 102],
        "audience_dept_ids": [],
        "audience_role_ids": [3],
        "recipient_count": 2,
        "created_at": datetime(2026, 11, 14, 22, 0),
    }
    values.update(overrides)
    return PortalBroadcastRecord(**values)


def test_record_to_summary_flattens_content_and_keeps_audience():
    from app.services.portal_broadcast_service import PortalBroadcastService

    summary = PortalBroadcastService.record_to_summary(_record())

    assert summary["id"] == 5
    # 列表页不需要换行结构，压平成一行，避免多行 Markdown 撑高行高
    assert summary["content_preview"] == "第一行 第二行"
    assert summary["audience"] == {
        "user_ids": [101, 102],
        "dept_ids": [],
        "role_ids": [3],
    }
    assert summary["recipient_count"] == 2
    assert summary["sender_real_name"] == "管理员"
    assert summary["created_at"] == "2026-11-14T22:00:00"


def test_record_to_summary_truncates_long_content():
    from app.services.portal_broadcast_service import PortalBroadcastService

    summary = PortalBroadcastService.record_to_summary(_record(content="字" * 500))

    assert len(summary["content_preview"]) == 161  # 160 + 省略号
    assert summary["content_preview"].endswith("…")


def test_record_detail_keeps_full_content_for_resend():
    from app.services.portal_broadcast_service import PortalBroadcastService

    detail = PortalBroadcastService.record_to_detail(_record(content="长正文" * 100))

    # 回填重发用的是原文，不能是预览
    assert detail["content"] == "长正文" * 100


@pytest.mark.asyncio
async def test_list_records_short_circuits_when_table_empty():
    from app.services.portal_broadcast_service import PortalBroadcastService

    session = _FakeSession([_FakeResult([0])])

    items, total = await PortalBroadcastService.list_records(session, limit=10, offset=0)

    assert (items, total) == ([], 0)
    # 表为空时不该再去查行
    assert len(session.statements) == 1


def test_broadcast_record_is_insertable_into_real_table():
    """发送记录表也要用真实表结构插一次：列名写错在这里就暴露。"""
    from sqlalchemy import create_engine, insert, select

    from app.core.orm import Base
    from app.models.portal_broadcast import PortalBroadcastRecord

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[PortalBroadcastRecord.__table__])

    row = {
        # SQLite 的 BIGSERIAL 不自增，显式给主键才能验证行本身
        "id": 1,
        "sender_user_id": 9,
        "sender_user_name": "admin",
        "sender_real_name": "管理员",
        "title": "系统维护通知",
        "content": "今晚 22:00 维护",
        "level": "warning",
        "audience_user_ids": [101],
        "audience_dept_ids": [7],
        "audience_role_ids": [],
        "recipient_count": 1,
        "created_at": __import__("datetime").datetime(2026, 11, 14, 22, 0),
    }
    with engine.begin() as conn:
        conn.execute(insert(PortalBroadcastRecord), row)

    with engine.connect() as conn:
        stored = conn.execute(
            select(
                PortalBroadcastRecord.title,
                PortalBroadcastRecord.audience_user_ids,
                PortalBroadcastRecord.recipient_count,
            )
        ).one()

    assert stored[0] == "系统维护通知"
    assert stored[1] == [101]
    assert stored[2] == 1
