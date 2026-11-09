"""注入接线的行为测试。

验证的是「值有没有落到正确的位置」，以及三条不能被绕过的边界：
手填的真实值不被覆盖、域名不符时拒绝、解析器故障时退回原样。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.core.orm import AsyncSessionLocal
from app.models.secret import (
    SecretBinding,
    SecretGrant,
    SecretUsageLog,
    SecretUserPref,
    UserSecret,
)
from app.services.secrets import center
from app.services.secrets.injection import (
    apply_api_tool_bindings,
    apply_mcp_bindings,
    apply_script_env_bindings,
    host_of,
    _current_user,
    _looks_unfilled,
)
from app.services.secrets.policy import SCENE_API_TOOL, SCENE_MCP, SCENE_SCRIPT_ENV

SECRET_TABLES = [
    SecretUsageLog.__table__,
    SecretBinding.__table__,
    SecretGrant.__table__,
    SecretUserPref.__table__,
    UserSecret.__table__,
]

USER_ID = "pytest-7"
PLAINTEXT = "tok-real-value-123"


@pytest.fixture(autouse=True)
async def _clean():
    # 只清理本用例建的凭据，绝不清表：这里连的是开发库。
    from tests.secret_test_guard import (
        purge_created_after,
        snapshot_secret_ids,
        snapshot_user_prefs,
    )

    async with AsyncSessionLocal() as session:
        keep = await snapshot_secret_ids(session)
        keep_users = await snapshot_user_prefs(session)
    try:
        yield
    finally:
        async with AsyncSessionLocal() as session:
            await purge_created_after(
                session, keep_secret_ids=keep, keep_user_ids=keep_users
            )


async def _seed_api_tool_binding(*, hosts=None, scenarios=None, tool_id="tool-1", slot="header:Authorization"):
    # 默认白名单就是工具要打的那个域名：空白名单表示「只允许内部目标」，
    # 那是有意为之的 fail-closed，不是这里想测的东西。
    async with AsyncSessionLocal() as session:
        secret = await center.create_secret(
            session,
            owner_id=USER_ID,
            secret_key=f"api.token.{uuid.uuid4().hex[:6]}",
            name="API Token",
            value=PLAINTEXT,
            allowed_scenarios=scenarios or [SCENE_API_TOOL],
            allowed_hosts=hosts if hosts is not None else ["api.example.com"],
        )
        await center.bind_secret(
            session,
            secret_id=secret.id,
            consumer_type="api_tool",
            consumer_id=tool_id,
            slot=slot,
            bound_by_user_id=USER_ID,
        )
        return secret


def _config(tool_id="tool-1", url="https://api.example.com/v1/items", name="my_tool"):
    return SimpleNamespace(id=tool_id, url_template=url, name=name)


USER = {"user_id": USER_ID, "dept_code": "DEPT-DATA"}


class TestHelpers:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("", True),
            (None, True),
            ("****", True),
            ("••••••••••••", True),
            ("${CREDENTIAL:jira.prod.token}", True),
            ("real-value", False),
            ("Bearer abc123", False),
        ],
    )
    def test_looks_unfilled(self, value, expected):
        assert _looks_unfilled(value) is expected

    def test_host_of(self):
        assert host_of("https://api.example.com/v1/items") == "api.example.com"
        assert host_of("not a url") is None
        assert host_of(None) is None


class TestApiToolInjection:
    async def test_placeholder_is_replaced_after_binding(self):
        await _seed_api_tool_binding()

        headers = {"Authorization": "${CREDENTIAL:jira.prod.token}"}
        result = await apply_api_tool_bindings(_config(), headers, user_info=USER)

        assert result["Authorization"] == PLAINTEXT

    async def test_empty_header_is_filled(self):
        await _seed_api_tool_binding()

        result = await apply_api_tool_bindings(
            _config(), {"Authorization": ""}, user_info=USER
        )

        assert result["Authorization"] == PLAINTEXT

    async def test_manual_value_is_never_overwritten(self):
        await _seed_api_tool_binding()

        result = await apply_api_tool_bindings(
            _config(), {"Authorization": "Bearer hand-written"}, user_info=USER
        )

        assert result["Authorization"] == "Bearer hand-written"

    async def test_without_binding_nothing_changes(self):
        result = await apply_api_tool_bindings(
            _config(), {"Authorization": "${CREDENTIAL:x}"}, user_info=USER
        )

        assert result["Authorization"] == "${CREDENTIAL:x}"

    async def test_other_tool_binding_does_not_apply(self):
        await _seed_api_tool_binding(tool_id="tool-1")

        result = await apply_api_tool_bindings(
            _config(tool_id="tool-2"), {"Authorization": ""}, user_info=USER
        )

        assert result["Authorization"] == ""

    async def test_host_mismatch_is_denied(self):
        await _seed_api_tool_binding(hosts=["api.github.com"])

        result = await apply_api_tool_bindings(
            _config(url="https://evil.example.com/v1"), {"Authorization": ""}, user_info=USER
        )

        assert result["Authorization"] == ""

    async def test_scene_mismatch_is_denied(self):
        # 只允许 MCP 场景的凭据，不能被 API 工具拿去用。
        await _seed_api_tool_binding(scenarios=[SCENE_MCP])

        result = await apply_api_tool_bindings(
            _config(), {"Authorization": ""}, user_info=USER
        )

        assert result["Authorization"] == ""

    async def test_missing_user_returns_headers_untouched(self):
        await _seed_api_tool_binding()

        result = await apply_api_tool_bindings(
            _config(), {"Authorization": ""}, user_info={"user_id": ""}
        )

        assert result["Authorization"] == ""

    async def test_non_credential_headers_are_left_alone(self):
        await _seed_api_tool_binding()

        headers = {"X-Trace-Id": "${CREDENTIAL:x}", "Accept": "application/json"}
        result = await apply_api_tool_bindings(_config(), headers, user_info=USER)

        # 没有绑定的 header 原样返回，不能因为「长得像占位符」就清空
        assert result["X-Trace-Id"] == "${CREDENTIAL:x}"
        assert result["Accept"] == "application/json"

    async def test_usage_log_is_written(self):
        from sqlalchemy import select

        secret = await _seed_api_tool_binding()
        await apply_api_tool_bindings(_config(), {"Authorization": ""}, user_info=USER)

        async with AsyncSessionLocal() as session:
            # 只看这条凭据的流水：共享库里还有别的用例和真实调用留下的记录
            rows = (
                await session.execute(
                    select(SecretUsageLog).where(SecretUsageLog.secret_id == secret.id)
                )
            ).scalars().all()

        assert len(rows) == 1
        assert rows[0].scene == SCENE_API_TOOL
        assert rows[0].result == "success"


class TestMcpInjection:
    async def test_placeholder_replaced(self):
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mcp.token",
                name="MCP Token",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.atlassian.net"],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="server-1",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
            )

        server = SimpleNamespace(id="server-1", sse_url="https://mcp.atlassian.net/sse")
        result = await apply_mcp_bindings(
            server, {"Authorization": "${CREDENTIAL:mcp}"}, user_info=USER
        )

        assert result["Authorization"] == PLAINTEXT

    async def test_static_token_used_when_no_binding(self):
        """没绑定时完全退回旧行为：``fixed_token`` 配的静态 Token 照常发出。"""
        server = SimpleNamespace(id="server-1", sse_url="https://mcp.atlassian.net/sse")
        result = await apply_mcp_bindings(
            server, {"Authorization": "Bearer already-configured"}, user_info=USER
        )

        assert result["Authorization"] == "Bearer already-configured"

    async def test_binding_overrides_static_token(self):
        """MCP 的规则与 API 工具相反：绑定优先。

        ``fixed_token`` 是服务级单值（管理员配一次、全平台共用），绑定是用户的
        显式动作。不覆盖的话，只要配过 ``fixed_token``，绑定就永远不生效——
        ``resolve_mcp_auth_headers`` 会在 ``fixed_token`` 存在时无条件写
        ``Authorization``，等到注入层时那一格早就有值了。
        """
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mcp.personal",
                name="MCP 个人 Token",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.atlassian.net"],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="server-1",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
            )

        server = SimpleNamespace(id="server-1", sse_url="https://mcp.atlassian.net/sse")
        result = await apply_mcp_bindings(
            server, {"Authorization": "Bearer shared-service-token"}, user_info=USER
        )

        assert result["Authorization"] == PLAINTEXT

    async def test_non_credential_headers_untouched(self):
        """``Accept`` 之类不查库、不改动——每多查一个 header 就多一次解密。"""
        server = SimpleNamespace(id="server-1", sse_url="https://mcp.atlassian.net/sse")
        result = await apply_mcp_bindings(
            server, {"Accept": "application/json"}, user_info=USER
        )

        assert result["Accept"] == "application/json"

    async def test_empty_valued_header_is_filled_from_binding(self):
        """「其他 Header」只绑凭据、值留空时，运行时必须把值补上。

        前端 ``planMcpAuthHeaders`` 会把「绑了凭据但没填值」的行写成
        ``{"X-Tenant": ""}``——这是刻意的，因为注入循环只遍历配置里已存在的
        Header 名。这条契约能不能通，全看空值是否仍被认成「待填」：一旦
        ``_looks_unfilled("")`` 不再是真，绑定就永远查不到，而界面上还写着
        「来自凭据」。
        """
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mcp.tenant",
                name="租户标识",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.atlassian.net"],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="server-tenant",
                slot="header:X-Tenant",
                bound_by_user_id=USER_ID,
            )

        server = SimpleNamespace(id="server-tenant", sse_url="https://mcp.atlassian.net/sse")
        result = await apply_mcp_bindings(server, {"X-Tenant": ""}, user_info=USER)

        assert result["X-Tenant"] == PLAINTEXT


    async def test_bound_header_overrides_existing_value(self):
        """绑了凭据的 Header 必须**无条件**覆盖配置里的旧值。

        旧实现只处理「值是空的 / 是占位符」或「名字像凭据头」两种 Header，于是
        ``X-Tenant`` 这种带旧值、名字又不含 token/key/auth 的行被直接 ``continue``
        —— 连数据库都不查，使用记录里一条都没有，用户只看到「绑了但没生效」。
        前端绑定后值输入框被隐藏，用户也无法把旧值清空，等于死锁。
        """
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mcp.tenant-override",
                name="租户标识",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.atlassian.net"],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="server-headers",
                slot="header:X-Tenant",
                bound_by_user_id=USER_ID,
            )

        server = SimpleNamespace(id="server-headers", sse_url="https://mcp.atlassian.net/sse")
        result = await apply_mcp_bindings(
            server, {"X-Tenant": "old-configured-value"}, user_info=USER
        )

        assert result["X-Tenant"] == PLAINTEXT

    async def test_bound_header_is_added_when_absent_from_config(self):
        """配置里没有这个 Header、但槽位有绑定 —— 要补上，绑定就是显式意图。"""
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mcp.extra-header",
                name="额外 Header",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.atlassian.net"],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="server-absent",
                slot="header:X-Api-Key",
                bound_by_user_id=USER_ID,
            )

        server = SimpleNamespace(id="server-absent", sse_url="https://mcp.atlassian.net/sse")
        result = await apply_mcp_bindings(
            server, {"Accept": "application/json"}, user_info=USER
        )

        assert result["X-Api-Key"] == PLAINTEXT
        assert result["Accept"] == "application/json", "无关 Header 不受影响"

    async def test_bound_slot_name_matches_header_case_insensitively(self):
        """槽位名与配置里的 Header 大小写不同时，要覆盖原 key，而不是多出一个。"""
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mcp.case-fold",
                name="大小写",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.atlassian.net"],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="server-case",
                slot="header:x-tenant",
                bound_by_user_id=USER_ID,
            )

        server = SimpleNamespace(id="server-case", sse_url="https://mcp.atlassian.net/sse")
        result = await apply_mcp_bindings(
            server, {"X-Tenant": "old", "Accept": "application/json"}, user_info=USER
        )

        assert result["X-Tenant"] == PLAINTEXT
        assert "x-tenant" not in result, "不能因为大小写不同就多发一个 Header"


    async def test_reports_bound_slots_for_session_isolation(self):
        """把「这个用户在这个服务上有绑定」回传给调用方。

        调用方（``call_remote_tool``）据此决定出站连接能否跨用户复用：SSE 的
        Header 在建连时冻结，共用会话就是把先建连那个人的凭据发给所有人。
        """
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mcp.report-used",
                name="会话隔离",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.atlassian.net"],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="server-used",
                slot="header:X-Tenant",
                bound_by_user_id=USER_ID,
            )

        server = SimpleNamespace(id="server-used", sse_url="https://mcp.atlassian.net/sse")
        used: dict = {}
        await apply_mcp_bindings(
            server, {"Accept": "application/json"}, user_info=USER, used=used
        )

        assert used["user_id"] == USER_ID
        assert used["slots"] == ["header:X-Tenant"]

    async def test_reports_nothing_when_service_has_no_binding(self):
        """没绑定的服务不能被打上「需要隔离」的标记，否则连接复用白白丢掉。"""
        server = SimpleNamespace(id="server-none", sse_url="https://mcp.atlassian.net/sse")
        used: dict = {}
        await apply_mcp_bindings(
            server, {"Accept": "application/json"}, user_info=USER, used=used
        )

        assert not used.get("slots")


class TestCurrentUserResolution:
    """两条上下文链路都要读得到——漏一条就是静默失效。

    ``current_user_info`` 是 HTTP 请求的载体，``AgentContext.user_dimensions`` 是
    后台/定时任务的载体。只读其中一个不会报错，只会让绑定解析拿不到 user_id，
    然后工具退回「凭据没填」，看起来像用户没配凭据。
    """

    def test_explicit_user_info_wins(self):
        assert _current_user({"user_id": "9", "dept_code": "D1"}) == ("9", "D1")

    def test_reads_agent_context_dimensions(self):
        from app.core.context import AgentContext, agent_context

        token = agent_context.set(
            AgentContext(agent_id="a1", agent_name="测试智能体", user_dimensions={"user_id": 42, "dept_code": "DEPT-A"})
        )
        try:
            assert _current_user(None) == ("42", "DEPT-A")
        finally:
            agent_context.reset(token)

    def test_falls_back_to_current_user_info(self):
        from app.core.context import AgentContext, agent_context
        from app.utils.context import current_user_info

        # 有 AgentContext 但没有用户维度 → 必须继续往下读 current_user_info
        ctx_token = agent_context.set(AgentContext(agent_id="a1", agent_name="测试智能体"))
        info_token = current_user_info.set({"user_id": "7", "dept_code": "DEPT-B"})
        try:
            assert _current_user(None) == ("7", "DEPT-B")
        finally:
            current_user_info.reset(info_token)
            agent_context.reset(ctx_token)

    def test_returns_empty_without_any_context(self):
        from app.core.context import agent_context
        from app.utils.context import current_user_info

        ctx_token = agent_context.set(None)
        info_token = current_user_info.set(None)
        try:
            assert _current_user(None) == ("", "")
        finally:
            current_user_info.reset(info_token)
            agent_context.reset(ctx_token)

    def test_scheduled_task_carries_dept_code(self):
        """定时任务的 user_info 必须带 dept_code。

        少了它，管理员授给「部门」的系统凭据在定时任务里会全部判成
        ``denied_by_grant``——同一个任务手动点一次却正常，极难排查。
        """
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2] / "app/services/ai/scheduler_service.py"
        ).read_text(encoding="utf-8")
        marker = source.index('"is_scheduled_task": True,')
        window = source[max(0, marker - 900) : marker]
        assert '"dept_code"' in window, "定时任务的 user_info 少了 dept_code"


class TestScheduledTaskRequest:
    """定时任务这条链路的两个前置条件。

    任务主体是**创建者**（不是「谁在看页面」），且必须带上部门 code——凭据授权
    可以是部门级的。这两点任何一处缺失，定时任务里的凭据都会静默失效。
    """

    async def _grant_to_dept(self, secret_id: str):
        async with AsyncSessionLocal() as session:
            await center.grant_secret(
                session,
                secret_id=secret_id,
                grantee_type="dept",
                grantee_id="DEPT-DATA",
            )

    async def test_dept_grant_needs_dept_code_in_user_info(self):
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id="admin-1",
                secret_key="sys.dept.token",
                name="部门共享 Token",
                value=PLAINTEXT,
                owner_scope="system",
                allowed_scenarios=[SCENE_API_TOOL],
                allowed_hosts=["api.example.com"],
            )
        await self._grant_to_dept(secret.id)
        async with AsyncSessionLocal() as session:
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="api_tool",
                consumer_id="tool-1",
                slot="header:Authorization",
                bound_by_user_id=None,
            )

        # 有部门 code（定时任务补齐之后的样子）→ 解析成功
        ok = await apply_api_tool_bindings(
            _config(),
            {"Authorization": ""},
            user_info={"user_id": USER_ID, "dept_code": "DEPT-DATA"},
        )
        assert ok["Authorization"] == PLAINTEXT

        # 少了部门 code（补齐之前的样子）→ 部门授权匹配不上
        missing = await apply_api_tool_bindings(
            _config(),
            {"Authorization": ""},
            user_info={"user_id": USER_ID},
        )
        assert missing["Authorization"] == ""


class TestScriptEnvInjection:
    """脚本环境变量注入。

    **默认拿不到任何东西**：场景开关两层（平台策略 + 用户偏好）默认都是关。
    这里验证的是「关着的时候一定拿不到」以及「值渲染进 env 文件时不会变成命令」。
    """

    async def test_disabled_by_default_returns_nothing(self):
        from app.services.secrets.injection import apply_script_env_bindings

        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="script.db.password",
                name="脚本用数据库密码",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_SCRIPT_ENV],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="script_tool",
                consumer_id="script-1",
                slot="env:DB_PASSWORD",
                bound_by_user_id=USER_ID,
            )

        tool = SimpleNamespace(id="script-1", name="my_script")

        # 平台策略默认关 → 即使绑定了也拿不到
        assert await apply_script_env_bindings(tool, user_info=USER) == {}

    async def test_returns_value_when_both_switches_on(self, monkeypatch):
        """两层开关都打开之后，脚本确实能拿到变量。

        这条把「建凭据 → 绑定 → 解析 → 渲染成 env 文件内容」整条链串起来。
        单独的 resolver 测试只验到「解析成功」，验不到槽位前缀 (`env:`) 和
        变量名提取这一段——那段错了的话，界面显示绑定成功、脚本却读不到。
        """
        from app.services.secrets.injection import build_env_file_content
        from app.services.secrets.resolver import SecretResolver

        async def _flags():
            # 第三个是单次注入上限；留默认 3，用例只验证各自那一层
            return True, True, 3

        monkeypatch.setattr(SecretResolver, "_platform_flags", staticmethod(_flags))

        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="script.enabled.password",
                name="脚本用数据库密码",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_SCRIPT_ENV],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="script_tool",
                consumer_id="script-9",
                slot="env:DB_PASSWORD",
                bound_by_user_id=USER_ID,
            )
            session.add(SecretUserPref(user_id=USER_ID, allow_script_env=True))
            await session.commit()

        tool = SimpleNamespace(id="script-9", name="my_script")
        env = await apply_script_env_bindings(tool, user_info=USER)

        assert env == {"DB_PASSWORD": PLAINTEXT}
        # 这段内容会被 `.` 加载，所以必须立刻是安全的形态
        assert build_env_file_content(env) == f"DB_PASSWORD='{PLAINTEXT}'\n"

    def test_shell_quote_blocks_command_substitution(self):
        from app.services.secrets.injection import build_env_file_content, shell_quote

        dangerous = "$(rm -rf /) `whoami` ; echo pwned"
        rendered = build_env_file_content({"TOKEN": dangerous})

        # 单引号包裹后，$ / 反引号 / 分号都不再是语法
        assert rendered == "TOKEN='" + dangerous + "'\n"
        assert shell_quote("plain") == "'plain'"

    def test_single_quote_is_escaped(self):
        from app.services.secrets.injection import shell_quote

        # ' → '\'' ：结束当前单引号、转义一个单引号、重新开一个
        assert shell_quote("it's") == "'it'\\''s'"

    async def test_other_users_binding_is_not_used(self):
        from app.services.secrets.injection import list_bound_slots

        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id="42",
                secret_key="script.other",
                name="别人的凭据",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_SCRIPT_ENV],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="script_tool",
                consumer_id="script-2",
                slot="env:OTHER_SECRET",
                bound_by_user_id="42",
            )

        # 别人的个人绑定不该出现在我的槽位列表里
        assert await list_bound_slots("script_tool", "script-2", prefix="env:", user_id=USER_ID) == []
        assert await list_bound_slots("script_tool", "script-2", prefix="env:", user_id="42") == ["env:OTHER_SECRET"]


class TestScriptRuntimeWiring:
    """运行时要真的把执行身份传下去。

    单测直接调 `apply_script_env_bindings(tool, user_info=...)` 永远是通过的，
    接线处漏掉 `user_info` 却没人发现——`_current_user(None)` 返回空 user_id，
    函数安静地返回 `{}`，脚本里那些变量就一直是空的，不报错也不告警。
    """

    def test_runner_passes_user_info_into_env_binding(self):
        import inspect

        from app.services.ai.tools import script_tool_runtime

        source = inspect.getsource(script_tool_runtime)
        call = source[source.index("apply_script_env_bindings("):]
        call = call[: call.index(")") + 1]
        assert "user_info=self.user_info" in call
