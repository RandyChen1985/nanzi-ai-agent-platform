"""定义探测链路的真实数据库集成测试。

单元测试（``test_mcp_definition_probe.py``）用假 session 覆盖了分支判断，但有几件事
只有真库能验证：

* 探测写的是**变更表**、不是 ``sys_mcp_tool_cache``——「看一眼远端」绝不能顺手改掉
  已生效的定义，这条约束必须拿真实行比对，而不是看 mock 调用次数；
* 同一 ``(server, tool, kind)`` 的 pending 去重靠唯一组合 + 覆盖写，不是内存字典；
* ``dismiss`` 推进 ``definition_hash`` 之后，下一次探测确实不再报同一处差异；
* 连续失败计数要真落到 ``sys_mcp_servers.probe_failure_count``，并成功一次归零。

依赖 V223／V123 迁移已执行（变更表与两组新列）。表不存在时这些用例会直接失败，
这是有意的：迁移没跑就上线的结果就是探测接口 500。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List

import pytest
from sqlalchemy import delete, select, update

from app.core.orm import AsyncSessionLocal
from app.models.mcp import McpServer, McpToolCache, McpToolDefinitionChange
from app.services.mcp import definition_probe as probe
from app.services.mcp.definition_diff import definition_fingerprint

DESC_OLD = "按合同号查询客户基础信息"
DESC_NEW = "按合同号查询客户基础信息（含联系人电话）"

SCHEMA_OLD: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "contract_no": {"type": "string", "description": "合同号"},
    },
    "required": ["contract_no"],
}

SCHEMA_NEW: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "contract_no": {"type": "string", "description": "合同号"},
        "with_contact": {"type": "boolean", "description": "是否包含联系人"},
    },
    "required": ["contract_no"],
}


def _remote_tool(name: str, description: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """远端工具形状：dict 形态与 mcp SDK 对象形态都要能取到 schema（后者走属性）。"""
    return {"name": name, "description": description, "input_schema": schema}


class _Fixture:
    def __init__(self, server_id: str, server_name: str, tool_short: str) -> None:
        self.server_id = server_id
        self.server_name = server_name
        self.tool_short = tool_short
        self.tool_full = f"{server_name}:{tool_short}"

    def remote(self, description: str = DESC_OLD, schema: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        return [_remote_tool(self.tool_short, description, schema or SCHEMA_OLD)]


@pytest.fixture
async def probe_row():
    """建一条服务 + 一条工具缓存（``definition_hash`` 为空 = 还没有基线）。"""
    suffix = uuid.uuid4().hex[:8]
    fixture = _Fixture(str(uuid.uuid4()), f"probe-it-{suffix}", f"query_{suffix}")
    async with AsyncSessionLocal() as db:
        db.add(
            McpServer(
                id=fixture.server_id,
                server_name=fixture.server_name,
                sse_url="http://127.0.0.1:9/mcp/sse",
                credential_mode="static",
                enabled_status=1,
                scope="global",
                probe_failure_count=0,
            )
        )
        db.add(
            McpToolCache(
                id=str(uuid.uuid4()),
                server_id=fixture.server_id,
                tool_name=fixture.tool_full,
                tool_description=DESC_OLD,
                parameter_schema=json.dumps(SCHEMA_OLD, ensure_ascii=False),
                is_published=True,
                is_available=True,
                definition_hash=None,
            )
        )
        await db.commit()

    yield fixture

    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(McpToolDefinitionChange).where(
                McpToolDefinitionChange.server_id == fixture.server_id
            )
        )
        await db.execute(
            delete(McpToolCache).where(McpToolCache.server_id == fixture.server_id)
        )
        await db.execute(delete(McpServer).where(McpServer.id == fixture.server_id))
        await db.commit()


async def _set_baseline(server_id: str, description: str, schema: Dict[str, Any]) -> None:
    """把本地缓存设成「描述 + 参数 + 对应指纹」这一整套，模拟一次已完成同步的状态。"""
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(McpToolCache)
            .where(McpToolCache.server_id == server_id)
            .values(
                tool_description=description,
                parameter_schema=json.dumps(schema, ensure_ascii=False),
                definition_hash=definition_fingerprint(description, schema, None),
            )
        )
        await db.commit()


async def _set_hash(server_id: str, definition_hash: str | None) -> None:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(McpToolCache).where(McpToolCache.server_id == server_id)
            )
        ).scalars().first()
        row.definition_hash = definition_hash
        await db.commit()


async def _cache_row(server_id: str) -> McpToolCache:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                select(McpToolCache).where(McpToolCache.server_id == server_id)
            )
        ).scalars().first()


async def _changes(server_id: str, status: str | None = None) -> List[McpToolDefinitionChange]:
    async with AsyncSessionLocal() as db:
        stmt = select(McpToolDefinitionChange).where(
            McpToolDefinitionChange.server_id == server_id
        )
        if status:
            stmt = stmt.where(McpToolDefinitionChange.status == status)
        return list((await db.execute(stmt)).scalars().all())


async def _server(server_id: str) -> McpServer:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(select(McpServer).where(McpServer.id == server_id))
        ).scalars().first()


@pytest.fixture
def patch_remote(monkeypatch):
    """把远端 tools/list 换成可控列表（探测只调这一个外部入口）。

    返回 ``calls`` 列表（每次调用记录 server_id）：既能断言「发了几次请求」，
    也能断言「根本没发」——needs_user 那条分支正是靠它守住。
    """

    def _install(tools: List[Dict[str, Any]] | Exception) -> List[str]:
        calls: List[str] = []

        async def _fake_list(cls, server_id, user_info=None):  # noqa: ANN001
            calls.append(server_id)
            if isinstance(tools, Exception):
                raise tools
            # 冻结一份，防止被测代码原地修改
            return [dict(item) for item in tools]

        from app.services.ai.tools.mcp_client import McpClientService

        monkeypatch.setattr(McpClientService, "list_remote_tools", classmethod(_fake_list))
        return calls

    return _install


async def test_first_probe_builds_baseline_without_reporting_changes(probe_row, patch_remote) -> None:
    """``definition_hash`` 为空 = 还没有基线：只记基线，不报变更。

    否则上线当天会把每个服务每个工具都报一条，第一次打开页签就是几百条噪音。

    **基线必须落库**：早期实现只把 ``baseline_tools += 1`` 就 continue，不写指纹。
    后果是每次探测都判定「还没有基线」→ 永远在建基线 → 远端改了什么一次都报不出来，
    而界面上还会一直说「已为 N 个工具建立基线，之后的变化才会被报出来」。
    """
    patch_remote(probe_row.remote())

    result = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_MANUAL)

    assert result["status"] == probe.PROBE_OK
    assert result["baseline_tools"] == 1
    assert result["new_changes"] == 0
    assert await _changes(probe_row.server_id) == []

    # 基线按「本地当前生效定义」算并回写；已生效的描述/参数一个字都没动
    cached = await _cache_row(probe_row.server_id)
    assert cached.definition_hash == definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    assert cached.tool_description == DESC_OLD


async def test_probe_after_baseline_detects_remote_drift(probe_row, patch_remote) -> None:
    """建完基线之后，远端一改就必须报出来——否则基线白建。

    这条盯的是「存量服务上线即失效」那个坑：迁移后所有工具的 ``definition_hash`` 都是
    NULL，如果首次探测不回写，存量服务**永远不会**产生任何变更记录。
    """
    patch_remote(probe_row.remote())
    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)

    # 远端把描述改了（本地未同步）
    patch_remote(probe_row.remote(description=DESC_NEW))
    result = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)

    assert result["status"] == probe.PROBE_CHANGED, "基线建立后必须能发现远端漂移"
    assert result["baseline_tools"] == 0, "第二次探测不该再重建基线"
    kinds = {change["kind"] for change in result["changes"]}
    assert "description_changed" in kinds


async def test_description_change_is_recorded_but_cache_untouched(probe_row, patch_remote) -> None:
    """远端改了描述：变更表多一条 pending，已生效的本地定义一个字都不能动。"""
    old_hash = definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    await _set_hash(probe_row.server_id, old_hash)
    patch_remote(probe_row.remote(description=DESC_NEW))

    result = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_MANUAL)

    assert result["status"] == probe.PROBE_CHANGED
    assert result["new_changes"] == 1

    rows = await _changes(probe_row.server_id, status="pending")
    assert [row.change_kind for row in rows] == ["description_changed"]
    assert rows[0].tool_name == probe_row.tool_full
    assert rows[0].severity == "safe"
    assert DESC_NEW in (rows[0].after_value or "")
    assert DESC_OLD in (rows[0].before_value or "")

    cached = await _cache_row(probe_row.server_id)
    assert cached.tool_description == DESC_OLD, "探测不得改动已生效定义"
    assert cached.definition_hash == old_hash, "指纹只在应用/忽略时推进会"

    # 连续失败次数归零：成功探测一次就清掉历史失败
    server = await _server(probe_row.server_id)
    assert server.last_probe_status == probe.PROBE_CHANGED
    assert server.probe_failure_count == 0


async def test_new_optional_param_is_graded_safe(probe_row, patch_remote) -> None:
    """新增可选参数是 safe；同一个工具再探一次不会叠加第二条。"""
    await _set_hash(
        probe_row.server_id, definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    )
    patch_remote(probe_row.remote(schema=SCHEMA_NEW))

    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_MANUAL)
    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)

    rows = await _changes(probe_row.server_id, status="pending")
    assert [row.change_kind for row in rows] == ["param_added_optional"]
    assert rows[0].severity == "safe"
    # 第二次探测覆盖而不是叠加，且来源更新为最近一次
    assert rows[0].detected_by == probe.DETECTED_NIGHTLY


async def test_dismiss_advances_hash_and_stops_repeating(probe_row, patch_remote) -> None:
    """忽略要推进基线指纹，否则同一个差异每天凌晨都会被重新报一遍。"""
    await _set_hash(
        probe_row.server_id, definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    )
    patch_remote(probe_row.remote(description=DESC_NEW))

    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)
    rows = await _changes(probe_row.server_id, status="pending")
    assert len(rows) == 1

    dismissed = await probe.dismiss_changes([rows[0].id], user_id=1)
    assert dismissed["dismissed"] == 1

    cached = await _cache_row(probe_row.server_id)
    assert cached.definition_hash == definition_fingerprint(DESC_NEW, SCHEMA_OLD, None)

    # 再探一次：指纹已经对齐远端，不再产生新 pending
    again = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)
    assert again["status"] == probe.PROBE_OK
    assert await _changes(probe_row.server_id, status="pending") == []
    # 已忽略的记录仍在，历史不能被抹掉
    assert len(await _changes(probe_row.server_id, status="dismissed")) == 1


async def test_apply_syncs_the_whole_server_once(probe_row, patch_remote, monkeypatch) -> None:
    """应用是「按服务整批同步」：同一服务的多条变更只触发一次 sync_tools。"""
    await _set_hash(
        probe_row.server_id, definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    )
    patch_remote(probe_row.remote(description=DESC_NEW, schema=SCHEMA_NEW))
    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_MANUAL)
    rows = await _changes(probe_row.server_id, status="pending")
    assert len(rows) >= 2, "描述与参数都变了，应至少两条"

    from app.services.ai.tools.mcp_client import McpClientService

    calls: List[str] = []

    async def _fake_sync(cls, server_id, user_info=None, **kwargs):  # noqa: ANN001
        calls.append(server_id)
        return {"tool_count": 1, "remote_deleted_count": 0}

    monkeypatch.setattr(McpClientService, "sync_tools", classmethod(_fake_sync))

    result = await probe.apply_changes(
        [row.id for row in rows], user_id=1, user_info={"user_id": "1"}
    )

    assert result["applied"] >= 1
    assert calls == [probe_row.server_id], "同一服务必须只同步一次"


async def test_unreachable_counts_failures_and_success_resets(probe_row, patch_remote) -> None:
    """连不上远端：状态 unreachable、失败计数累加；恢复一次即归零。"""
    await _set_hash(
        probe_row.server_id, definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    )
    patch_remote(RuntimeError("connect timeout"))

    for _ in range(2):
        result = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)
        assert result["status"] == probe.PROBE_UNREACHABLE

    server = await _server(probe_row.server_id)
    assert server.probe_failure_count == 2
    assert server.last_probe_error and "connect timeout" in server.last_probe_error
    # 失败不产生变更记录，也不动缓存
    assert await _changes(probe_row.server_id) == []
    assert (await _cache_row(probe_row.server_id)).tool_description == DESC_OLD

    patch_remote(probe_row.remote())
    ok = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)
    assert ok["status"] == probe.PROBE_OK

    server = await _server(probe_row.server_id)
    assert server.probe_failure_count == 0
    assert server.last_probe_error is None


async def test_remote_empty_listing_is_treated_as_failure_not_as_full_removal(
    probe_row, patch_remote
) -> None:
    """远端返回 0 个工具但本地有可用工具：判故障，不能据此把全部工具标成删除。"""
    await _set_hash(
        probe_row.server_id, definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    )
    patch_remote([])

    result = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)

    assert result["status"] == probe.PROBE_UNREACHABLE
    assert await _changes(probe_row.server_id) == []
    assert (await _cache_row(probe_row.server_id)).is_available is True


async def test_user_assertion_server_is_skipped_in_background(probe_row, patch_remote) -> None:
    """需要用户签名的服务在后台链路降级为 needs_user，不发请求。"""
    async with AsyncSessionLocal() as db:
        server = (
            await db.execute(select(McpServer).where(McpServer.id == probe_row.server_id))
        ).scalars().first()
        server.user_assertion_enabled = True
        await db.commit()

    calls = patch_remote(probe_row.remote())

    result = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)
    assert result["status"] == probe.PROBE_NEEDS_USER
    assert calls == [], "无操作者身份时不该向远端发请求"

    # 页面内探测带上了操作者，就该照常执行
    result = await probe.probe_server(
        probe_row.server_id,
        detected_by=probe.DETECTED_MANUAL,
        user_info={"user_id": "42"},
    )
    assert result["status"] != probe.PROBE_NEEDS_USER


def _param_of(row) -> str:
    """从变更行的 after/before JSON 里取参数名（非参数级变更为空串）。"""
    for raw in (row.after_value, row.before_value):
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("param"):
            return str(payload["param"])
    return ""


async def test_dismissed_tool_removed_is_not_re_reported(probe_row, patch_remote) -> None:
    """忽略「远端已删除该工具」之后，第二天凌晨不能再报同一条。

    这条差异没有「远端定义」可以推进指纹（远端确实没有这个工具了），所以忽略动作
    本身必须留下痕迹，否则每天都会重新建一条 pending，等于把人的确认当没发生。
    """
    await _set_hash(
        probe_row.server_id, definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    )
    # 远端换成一个完全不同的工具名 → 本地的那个就「消失」了
    patch_remote([_remote_tool("another_tool", "另一个工具", SCHEMA_OLD)])

    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)
    removed = [row for row in await _changes(probe_row.server_id, "pending")
               if row.change_kind == "tool_removed"]
    assert len(removed) == 1

    await probe.dismiss_changes([removed[0].id], user_id=1)

    again = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)
    assert again["status"] != probe.PROBE_CHANGED or not any(
        change["kind"] == "tool_removed" for change in again["changes"]
    ), "已忽略的 tool_removed 不能重复出现在待办里"
    still_pending = [row for row in await _changes(probe_row.server_id, "pending")
                     if row.change_kind == "tool_removed"]
    assert still_pending == []


async def test_dismissed_tool_added_is_not_re_reported(probe_row, patch_remote) -> None:
    """忽略「远端新增工具」之后也不再重复报（本地没有该工具行，指纹无处可推）。"""
    await _set_hash(
        probe_row.server_id, definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    )
    patch_remote(
        probe_row.remote()
        + [_remote_tool("brand_new", "新工具", {"type": "object", "properties": {}})]
    )

    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)
    added = [row for row in await _changes(probe_row.server_id, "pending")
             if row.change_kind == "tool_added"]
    assert len(added) == 1

    await probe.dismiss_changes([added[0].id], user_id=1)
    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_NIGHTLY)

    still_pending = [row for row in await _changes(probe_row.server_id, "pending")
                     if row.change_kind == "tool_added"]
    assert still_pending == []


async def test_two_params_changed_in_one_tool_do_not_collide(probe_row, patch_remote) -> None:
    """同一工具的两个参数各改一次描述：必须是两条独立待办，且不会互相吞掉。

    去重键早期只有 (tool_name, change_kind)：第一次探测插两条同键 pending，第二次
    探测构造 ``{key: row}`` 时后一条覆盖前一条，先插入的那行从此既不更新也不在
    ``observed`` 里——它变成点进去看不到内容的僵尸行，远端把参数改回去了也删不掉。
    """
    base = {
        "type": "object",
        "properties": {
            "p1": {"type": "string", "description": "参数一"},
            "p2": {"type": "string", "description": "参数二"},
        },
        "required": [],
    }
    await _set_baseline(probe_row.server_id, DESC_OLD, base)
    drifted = {
        "type": "object",
        "properties": {
            "p1": {"type": "string", "description": "参数一（已改）"},
            "p2": {"type": "string", "description": "参数二（已改）"},
        },
        "required": [],
    }
    patch_remote([_remote_tool(probe_row.tool_short, DESC_OLD, drifted)])

    first = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_MANUAL)
    assert first["status"] == probe.PROBE_CHANGED
    pending = await _changes(probe_row.server_id, "pending")
    assert len(pending) == 2, "两个参数各一条待办"
    assert {_param_of(row) for row in pending} == {"p1", "p2"}

    # 再探一次：不该新增、不该丢信息、也不该留下脱管的重复行
    second = await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_MANUAL)
    assert second["new_changes"] == 0, "已有 pending 不算新增（否则天天发告警）"
    pending = await _changes(probe_row.server_id, "pending")
    assert len(pending) == 2
    assert {_param_of(row) for row in pending} == {"p1", "p2"}

    # 远端把 p2 改回去：只剩 p1 那条，p2 的待办要被清掉
    half = {
        "type": "object",
        "properties": {
            "p1": {"type": "string", "description": "参数一（已改）"},
            "p2": {"type": "string", "description": "参数二"},
        },
        "required": [],
    }
    patch_remote([_remote_tool(probe_row.tool_short, DESC_OLD, half)])
    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_MANUAL)
    pending = await _changes(probe_row.server_id, "pending")
    assert {_param_of(row) for row in pending} == {"p1"}


async def test_apply_attributes_unselected_same_server_changes_to_the_operator(
    probe_row, patch_remote, monkeypatch
) -> None:
    """应用是整服务同步：用户没勾的那些差异其实也一起生效了，处理人不能留空。

    留空会显示成「系统自动应用」，看起来像探测自己动手改了工具——而实际上是人点的。
    """
    await _set_hash(
        probe_row.server_id, definition_fingerprint(DESC_OLD, SCHEMA_OLD, None)
    )
    patch_remote(probe_row.remote(description=DESC_NEW, schema=SCHEMA_NEW))
    await probe.probe_server(probe_row.server_id, detected_by=probe.DETECTED_MANUAL)
    rows = await _changes(probe_row.server_id, status="pending")
    assert len(rows) >= 2

    from app.services.ai.tools.mcp_client import McpClientService

    async def _fake_sync(cls, server_id, user_info=None, **kwargs):  # noqa: ANN001
        # 模拟真实 sync_tools：它会把该服务所有 pending 一并标成 applied（不写处理人）
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(McpToolDefinitionChange)
                .where(
                    McpToolDefinitionChange.server_id == server_id,
                    McpToolDefinitionChange.status == "pending",
                )
                .values(
                    status="applied",
                    handled_at=datetime.now().replace(microsecond=0),
                )
            )
            await db.commit()
        return {"tool_count": 1, "remote_deleted_count": 0}

    monkeypatch.setattr(McpClientService, "sync_tools", classmethod(_fake_sync))

    # 只把「第一条」交给 apply，其余同服务变更也应当绑定到同一个处理人
    applied_id = rows[0].id
    await probe.apply_changes([applied_id], user_id=42, user_info={"user_id": "42"})

    settled = await _changes(probe_row.server_id, status="applied")
    assert len(settled) >= 2
    assert {row.handled_by for row in settled} == {42}, "未勾选但一起生效的记录也要记处理人"
