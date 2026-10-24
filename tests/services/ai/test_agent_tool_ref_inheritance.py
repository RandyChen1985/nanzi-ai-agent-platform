"""新建模板 / 复制智能体时，工具引用的可见性拦截。

两条路径都会把「别人的工具配置」搬给当前用户，而「源的所有者能用」不等于
「搬过来的人能用」：

* **套模板新建**：通用助手的模板就是主助手，主助手用管理员的私有 MCP 服务配好了
  工具；普通用户走「新建智能体」向导时，后端套模板把 8 个引用整份搬了过来。
* **复制智能体**：源版本的工具列表深拷贝进副本。

这两条路都是照抄配置，产出的智能体一保存就挂着「无权访问该 MCP 服务」的告警，
而工具选择器只渲染可见服务——用户既看不见那些引用，也没有取消勾选的入口。

修复口径：只剔除 ``server_forbidden``（结构性无权访问）。服务被临时禁用、工具下线
属于源侧状态问题，照旧保留，否则搬一次就悄悄丢配置，比告警更难发现。
"""
import uuid

import pytest
from sqlalchemy import delete, select

from app.models.agent import AIAgent, AIAgentVersion
from app.models.mcp import McpServer, McpToolCache
from app.services.ai.agent_manager import AgentManagerService


async def _make_servers(session):
    """一对服务：别人的私有服务 + 公共平台服务。"""
    private_server = McpServer(
        id=str(uuid.uuid4()),
        server_name=f"t-{uuid.uuid4().hex[:8]}-private",
        sse_url="http://localhost:1/sse",
        enabled_status=1,
        scope="personal",
        user_id=100001,
    )
    global_server = McpServer(
        id=str(uuid.uuid4()),
        server_name=f"t-{uuid.uuid4().hex[:8]}-global",
        sse_url="http://localhost:2/sse",
        enabled_status=1,
        scope="global",
    )
    session.add_all([private_server, global_server])
    await session.flush()
    for server, tool in ((private_server, "secret-tool"), (global_server, "public-tool")):
        session.add(
            McpToolCache(
                id=str(uuid.uuid4()),
                server_id=server.id,
                tool_name=tool,
                is_published=True,
                is_available=True,
            )
        )
    await session.flush()
    return private_server, global_server


async def _drop_servers(session, *servers):
    await session.execute(
        delete(McpToolCache).where(McpToolCache.server_id.in_([s.id for s in servers]))
    )
    for server in servers:
        await session.delete(server)
    await session.commit()


@pytest.mark.asyncio
async def test_duplicate_agent_drops_tools_the_copier_cannot_access(db_session):
    """复制智能体时，剔除「源的所有者能用、复制人无权用」的 MCP 工具引用。"""
    private_server, global_server = await _make_servers(db_session)

    source = AIAgent(
        id=str(uuid.uuid4()),
        name=f"dup-src-{uuid.uuid4().hex[:8]}",
        display_name="源智能体",
        # 复制人就是所有者：本用例验证的是「工具引用剔除」，复制权限由
        # test_agent_duplicate_permission 覆盖（别人创建的智能体一律不能复制）
        created_by="copier",
        is_enabled=True,
        engine_type="LOCAL",
    )
    db_session.add(source)
    await db_session.flush()
    db_session.add(
        AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=source.id,
            version_number=1,
            system_prompt="prompt",
            model_name="deepseek-chat",
            tools=[
                f"{private_server.server_name}:secret-tool",
                f"{global_server.server_name}:public-tool",
            ],
            status="PUBLISHED",
        )
    )
    await db_session.flush()
    await db_session.commit()

    copier = {"role": "user", "user_name": "copier", "user_id": 200002, "id": 200002}
    data = type("D", (), {"name": f"dup-copy-{uuid.uuid4().hex[:8]}", "display_name": "副本"})()

    copied = await AgentManagerService.duplicate_agent(db_session, source.id, data, user=copier)
    assert copied is not None

    try:
        rows = await db_session.execute(
            select(AIAgentVersion.tools).where(AIAgentVersion.agent_id == copied.id)
        )
        names = [t if isinstance(t, str) else t.get("name") for t in (rows.scalars().first() or [])]

        assert f"{private_server.server_name}:secret-tool" not in names, "无权使用的工具不该被复制"
        assert f"{global_server.server_name}:public-tool" in names, "公共工具必须保留"
        assert [d["name"] for d in copied.dropped_tool_refs] == [
            f"{private_server.server_name}:secret-tool"
        ]
    finally:
        await db_session.execute(
            delete(AIAgentVersion).where(AIAgentVersion.agent_id.in_([source.id, copied.id]))
        )
        await db_session.delete(copied)
        await db_session.delete(source)
        await db_session.commit()
        await _drop_servers(db_session, private_server, global_server)


@pytest.mark.asyncio
async def test_template_tools_are_filtered_by_actual_viewer(db_session):
    """套模板时按「谁在用模板」判定：无权者被剔除，服务所有者原样保留。"""
    private_server, global_server = await _make_servers(db_session)

    template_tools = [
        f"{private_server.server_name}:secret-tool",
        f"{global_server.server_name}:public-tool",
        "read_file",
    ]

    try:
        user = {"role": "user", "user_name": "tpl-user", "user_id": 200002, "id": 200002}
        filtered, dropped = await AgentManagerService.drop_inaccessible_mcp_tools(
            db_session, template_tools, user
        )

        assert f"{private_server.server_name}:secret-tool" not in filtered, "无权使用的工具必须剔除"
        assert f"{global_server.server_name}:public-tool" in filtered, "公共工具必须保留"
        assert "read_file" in filtered, "静态工具与 MCP 无关，不能被误伤"
        assert [d["name"] for d in dropped] == [f"{private_server.server_name}:secret-tool"]

        # 服务所有者自己套同一个模板时，引用应当原样保留
        owner = {"role": "admin", "user_name": "tpl-admin", "user_id": 100001, "id": 100001}
        filtered_owner, dropped_owner = await AgentManagerService.drop_inaccessible_mcp_tools(
            db_session, template_tools, owner
        )
        assert f"{private_server.server_name}:secret-tool" in filtered_owner
        assert dropped_owner == []
    finally:
        await _drop_servers(db_session, private_server, global_server)
