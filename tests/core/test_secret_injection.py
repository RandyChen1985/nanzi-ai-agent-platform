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
    apply_mcp_arg_bindings,
    apply_mcp_bindings,
    apply_script_env_bindings,
    host_of,
    _current_user,
    _looks_unfilled,
)
from app.services.secrets.policy import SCENE_API_TOOL, SCENE_MCP, SCENE_SCRIPT_ENV
from app.services.secrets.resolver import secret_resolver

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
            # 引用形如 ${secret:标识.字段}，整段或前缀拼接都算待填
            ("${secret:mail.smtp.password}", True),
            ("Bearer ${secret:mail.smtp.token}", True),
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


class TestPersonalOverrideOfPlatformHeader:
    """平台配的 header 值里写着管理员的凭据标识，普通用户绑自己的凭据去覆盖。

    这是「个人覆盖平台默认」的核心场景。它能不能成，取决于 header 值的写法：
    ``${CREDENTIAL:xxx}`` 不参与标识校验，注入时用**当前绑定**的 value_path；
    ``${secret:xxx.field}`` 会校验标识一致，一旦用户换绑自己的凭据就必然
    reference_mismatch —— 所以绑定时不能写后者。
    """

    async def test_user_binding_wins_and_takes_its_own_field(self):
        async with AsyncSessionLocal() as session:
            admin_secret = await center.create_secret(
                session,
                owner_id=None,
                owner_scope="system",
                secret_key="platform.mail",
                name="平台邮箱",
                value='{"host": "smtp.platform.com", "password": "PlatformPwd!1"}',
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.example.com"],
                value_format="json",
                value_template="smtp",
                value_secret_fields=["password"],
            )
            await center.bind_secret(
                session,
                secret_id=admin_secret.id,
                consumer_type="mcp_server",
                consumer_id="srv-override",
                slot="header:Authorization",
                bound_by_user_id=None,
                value_path="password",
            )

            mine = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="my.mail",
                name="我的邮箱",
                value='{"host": "smtp.mine.com", "password": "MyOwnPwd!2026"}',
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.example.com"],
                value_format="json",
                value_template="smtp",
                value_secret_fields=["password"],
            )
            await center.bind_secret(
                session,
                secret_id=mine.id,
                consumer_type="mcp_server",
                consumer_id="srv-override",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
                value_path="password",
            )

        server = SimpleNamespace(
            id="srv-override", server_name="ovr", sse_url="https://mcp.example.com/sse"
        )
        headers = await apply_mcp_bindings(
            server,
            # 值里写的是**管理员**那条的标识：用户换绑后不应因此失败
            {"Authorization": "Bearer ${CREDENTIAL:platform.mail}"},
            user_info=USER,
        )

        assert headers["Authorization"] == "Bearer MyOwnPwd!2026"


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

        # 换的是 token，不是认证方案：原值是 ``Bearer xxx``，注入后仍是 Bearer。
        # 原来这里断言裸值，等于把「凭据注入会吃掉 Bearer 前缀」这个 bug 写成了
        # 预期——两个入口（固定 Token / 凭据中心）形态不一致，调用侧就 401。
        assert result["Authorization"] == f"Bearer {PLAINTEXT}"

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


class TestMcpArgInjection:
    """MCP 工具参数级注入：声明过的参数从 schema 剔除，运行时按 arg: 槽位补。

    和 Header 那条链路的根本区别：Header 有「配置里的默认值」可以退回，
    这里没有——模型看不到这个参数，凭据是唯一来源，取不到就是缺参数。
    """

    #: 区分「没传」和「显式传 None」——两者含义完全相反
    _UNSET = object()

    def _tool(self, tool_name: str = "server:query-customer", params=_UNSET):
        return SimpleNamespace(
            id="tool-1",
            server_id="server-1",
            tool_name=tool_name,
            credential_params='["api_key"]' if params is TestMcpArgInjection._UNSET else params,
        )

    def test_slot_uses_short_tool_name(self):
        """槽位用 ``:`` 后的短名——服务已由 consumer_id 限定，再带一遍就重复了。"""
        from app.services.secrets.injection import mcp_arg_slots

        pairs = mcp_arg_slots(self._tool())

        assert pairs == [("api_key", "arg:query-customer:api_key")]

    def test_broken_json_declares_nothing(self):
        """存储写坏了按「没声明」处理：宁可多一个参数，也不能让工具整个不可用。"""
        from app.services.secrets.injection import mcp_arg_slots

        assert mcp_arg_slots(self._tool(params="{oops")) == []

    def test_no_declaration_is_noop(self):
        from app.services.secrets.injection import mcp_arg_slots

        assert mcp_arg_slots(self._tool(params=None)) == []

    async def test_value_filled_from_binding(self):
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="crm.tenant",
                name="CRM 租户令牌",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_MCP],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="server-1",
                slot="arg:query-customer:api_key",
                bound_by_user_id=USER_ID,
            )

        result = await apply_mcp_arg_bindings(
            self._tool(), {"keyword": "13800138000"}, user_info=USER
        )

        assert result["api_key"] == PLAINTEXT
        # 普通业务参数原样透传
        assert result["keyword"] == "13800138000"

    async def test_missing_binding_is_recorded(self):
        """没绑要记进 denials——调用方据此报错，而不是带着空值发出去等 401。"""
        denials: dict[str, str] = {}

        result = await apply_mcp_arg_bindings(
            self._tool(), {"keyword": "x"}, user_info=USER, denials=denials
        )

        assert "api_key" not in result
        assert denials.get("arg:query-customer:api_key") is not None

    async def test_model_supplied_value_is_overridden(self):
        """模型若从上下文猜出值传了进来，凭据仍然优先——它是唯一可信来源。"""
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="crm.tenant2",
                name="CRM 租户令牌 2",
                value=PLAINTEXT,
                allowed_scenarios=[SCENE_MCP],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="server-1",
                slot="arg:query-customer:api_key",
                bound_by_user_id=USER_ID,
            )

        result = await apply_mcp_arg_bindings(
            self._tool(), {"api_key": "guessed-by-model"}, user_info=USER
        )

        assert result["api_key"] == PLAINTEXT

    async def test_undeclared_tool_is_untouched(self):
        result = await apply_mcp_arg_bindings(
            self._tool(params="[]"), {"api_key": "whatever"}, user_info=USER
        )

        assert result["api_key"] == "whatever"


class TestMcpFactoryDropsCredentialParams:
    """工厂侧：声明过的参数不能出现在模型可见的 args_schema 里。"""

    def _record(self, params):
        return SimpleNamespace(
            id="tool-1",
            server_id="server-1",
            tool_name="server:query-customer",
            tool_description="查客户",
            credential_params=params,
            parameter_schema=(
                '{"type":"object","properties":{'
                '"keyword":{"type":"string"},"api_key":{"type":"string"}},'
                '"required":["keyword","api_key"]}'
            ),
        )

    def test_credential_param_removed_from_schema(self):
        from app.services.ai.tools.mcp_factory import McpToolFactory

        tool = McpToolFactory.create_tool(self._record('["api_key"]'))

        fields = set(tool.args_schema.model_fields.keys())
        assert "keyword" in fields
        assert "api_key" not in fields

    def test_without_declaration_nothing_is_dropped(self):
        from app.services.ai.tools.mcp_factory import McpToolFactory

        tool = McpToolFactory.create_tool(self._record(None))

        assert "api_key" in set(tool.args_schema.model_fields.keys())


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


SMTP_JSON = '{"host": "smtp.exmail.qq.com", "port": 465, "username": "bot@nanzi.ai", "password": "Sup3rSecret"}'


async def _seed_structured_binding(
    *,
    value: str = SMTP_JSON,
    secret_type: str = "credential",
    slot: str = "header:Authorization",
    tool_id: str = "tool-1",
    value_format: str = "json",
):
    """建一条结构化凭据并绑到 header 槽位，返回 (secret, key)。"""
    key = f"mail.smtp.{uuid.uuid4().hex[:6]}"
    async with AsyncSessionLocal() as session:
        secret = await center.create_secret(
            session,
            owner_id=USER_ID,
            secret_key=key,
            name="告警邮箱",
            value=value,
            secret_type=secret_type,
            allowed_scenarios=[SCENE_API_TOOL],
            allowed_hosts=["api.example.com"],
            value_format=value_format,
            value_template="smtp" if value_format == "json" else None,
            value_secret_fields=["password"] if value_format == "json" else [],
        )
        await center.bind_secret(
            session,
            secret_id=secret.id,
            consumer_type="api_tool",
            consumer_id=tool_id,
            slot=slot,
            bound_by_user_id=USER_ID,
        )
    return secret, key


class TestStructuredReferenceInjection:
    """``${secret:标识.字段}`` 在注入层的实际行为。

    重点是「失败必须变红」：解析不出来时这个 header 不能被注入成任何东西，
    尤其不能退回整段明文——那会把含密码的 JSON 原样发出去。
    """

    async def test_field_reference_fills_header(self):
        _, key = await _seed_structured_binding()

        result = await apply_api_tool_bindings(
            _config(), {"Authorization": f"${{secret:{key}.password}}"}, user_info=USER
        )

        assert result["Authorization"] == "Sup3rSecret"

    async def test_prefix_text_is_kept(self):
        _, key = await _seed_structured_binding()

        result = await apply_api_tool_bindings(
            _config(), {"Authorization": f"Bearer ${{secret:{key}.username}}"}, user_info=USER
        )

        assert result["Authorization"] == "Bearer bot@nanzi.ai"

    async def test_integral_float_port_is_normalized(self):
        # 绑定是按槽位的：X-Port 要有自己的绑定，否则连引用都走不到
        _, key = await _seed_structured_binding(slot="header:X-Port")

        result = await apply_api_tool_bindings(
            _config(), {"X-Port": f"${{secret:{key}.port}}"}, user_info=USER
        )

        assert result["X-Port"] == "465"

    async def test_key_mismatch_leaves_placeholder_and_records_denial(self):
        await _seed_structured_binding()
        denials: dict[str, str] = {}

        result = await apply_api_tool_bindings(
            _config(),
            {"Authorization": "${secret:some.other.key.password}"},
            user_info=USER,
            denials=denials,
        )

        # 保留原占位符：让「凭据未填」的既有报错路径把问题说出来
        assert result["Authorization"] == "${secret:some.other.key.password}"
        assert denials["header:Authorization"] == "reference_mismatch"

    async def test_missing_field_records_denial(self):
        _, key = await _seed_structured_binding()
        denials: dict[str, str] = {}

        await apply_api_tool_bindings(
            _config(),
            {"Authorization": f"${{secret:{key}.nope}}"},
            user_info=USER,
            denials=denials,
        )

        assert denials["header:Authorization"] == "reference_field_missing"

    async def test_single_value_credential_cannot_be_indexed(self):
        _, key = await _seed_structured_binding(value=PLAINTEXT, value_format="text")
        denials: dict[str, str] = {}

        result = await apply_api_tool_bindings(
            _config(),
            {"Authorization": f"${{secret:{key}.password}}"},
            user_info=USER,
            denials=denials,
        )

        assert denials["header:Authorization"] == "reference_not_json"
        # 关键：绝不能退回整段单值明文
        assert result["Authorization"] != PLAINTEXT

    async def test_whole_key_reference_injects_serialized_json(self):
        secret, key = await _seed_structured_binding()

        result = await apply_api_tool_bindings(
            _config(), {"Authorization": f"${{secret:{key}}}"}, user_info=USER
        )

        # 整段引用给的是加密前的那份 JSON 原文
        assert result["Authorization"].startswith('{"host": "smtp.exmail.qq.com"')
        assert result["Authorization"] == SMTP_JSON

    async def test_reference_does_not_bypass_binding(self):
        """没绑到这个工具就没有凭据：引用不能自己按 key 找到值。"""
        _, key = await _seed_structured_binding(tool_id="tool-1")
        denials: dict[str, str] = {}

        result = await apply_api_tool_bindings(
            _config(tool_id="other-tool"),
            {"Authorization": f"${{secret:{key}.password}}"},
            user_info=USER,
            denials=denials,
        )

        assert result["Authorization"] == f"${{secret:{key}.password}}"


class TestUnresolvedReferenceIsBlocked:
    """未解析的 ``${secret:...}`` 必须被当成「凭据未填」拦下。

    否则注入失败（没绑定、字段写错、key 不匹配）时，这句占位符会被当作
    「用户手填的真实值」原样发到外部接口——用户看到的是对方的 401，
    完全猜不到是自己把字段名写错了。
    """

    def test_placeholder_detection_covers_reference_syntax(self):
        from app.services.ai.tools.tool_credentials import (
            is_credential_placeholder,
            unresolved_credential_headers,
        )

        assert is_credential_placeholder("${CREDENTIAL:x-api-key}") is True
        assert is_credential_placeholder("${secret:mail.smtp.token}") is True
        assert is_credential_placeholder("Bearer ${secret:mail.smtp.token}") is True
        assert is_credential_placeholder("real-value") is False

        pending = unresolved_credential_headers(
            {"Authorization": "Bearer ${secret:mail.smtp.token}", "X-Trace": "abc"}
        )
        assert pending == ["Authorization"]

    async def test_failed_reference_stays_pending_so_the_call_aborts(self):
        await _seed_structured_binding()
        from app.services.ai.tools.tool_credentials import unresolved_credential_headers

        headers = {"Authorization": "${secret:other.key.password}"}
        result = await apply_api_tool_bindings(_config(), headers, user_info=USER)

        # 注入层（正确地）没有替换它
        assert result["Authorization"] == "${secret:other.key.password}"
        # 调用方据此抛 UnresolvedCredentialError，而不是把占位符发出去
        assert unresolved_credential_headers(result) == ["Authorization"]

    async def test_mcp_header_name_case_mismatch_still_renders_reference(self):
        """槽位名与配置里的 Header 名大小写不同时，不能误判成「没有引用」。"""
        key = f"mail.smtp.{uuid.uuid4().hex[:6]}"
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key=key,
                name="告警邮箱",
                value=SMTP_JSON,
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.example.com"],
                value_format="json",
                value_template="smtp",
                value_secret_fields=["password"],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="srv-case",
                slot="header:authorization",
                bound_by_user_id=USER_ID,
            )

        server = SimpleNamespace(
            id="srv-case", server_name="case", sse_url="https://mcp.example.com/sse"
        )
        result = await apply_mcp_bindings(
            server,
            {"Authorization": f"Bearer ${{secret:{key}.username}}"},
            user_info=USER,
        )

        assert result["Authorization"] == "Bearer bot@nanzi.ai"

    async def test_mcp_unbound_reference_is_denied_not_sent_out(self):
        """头里写着 ``${secret:...}`` 却没有任何绑定时，必须记一条拒绝。

        MCP 的注入只遍历**绑定槽位**：没有绑定时整个头原样返回。若不额外检查，
        这句占位符会被原样发给外部 MCP 服务。API 工具侧由
        ``unresolved_credential_headers`` 拦，MCP 侧要在这里对齐。
        """
        server = SimpleNamespace(
            id=f"no-binding-{uuid.uuid4().hex[:6]}",
            server_name="unbound",
            sse_url="https://mcp.example.com/sse",
        )
        denials: dict[str, str] = {}
        headers = {"Authorization": "Bearer ${secret:mail.smtp.token}"}

        result = await apply_mcp_bindings(
            server, headers, user_info=USER, denials=denials
        )

        assert result["Authorization"] == "Bearer ${secret:mail.smtp.token}"
        assert denials == {"header:Authorization": "reference_unbound"}

    async def test_mcp_unbound_legacy_placeholder_is_denied(self):
        server = SimpleNamespace(
            id=f"no-binding-{uuid.uuid4().hex[:6]}",
            server_name="unbound",
            sse_url="https://mcp.example.com/sse",
        )
        denials: dict[str, str] = {}

        await apply_mcp_bindings(
            server,
            {"X-Api-Key": "${CREDENTIAL:x-api-key}"},
            user_info=USER,
            denials=denials,
        )

        assert denials == {"header:X-Api-Key": "credential_unbound"}

    async def test_mcp_plain_headers_are_not_flagged(self):
        """普通 Header 不该被记成拒绝：绝大多数头本来就不绑凭据。"""
        server = SimpleNamespace(
            id=f"no-binding-{uuid.uuid4().hex[:6]}",
            server_name="unbound",
            sse_url="https://mcp.example.com/sse",
        )
        denials: dict[str, str] = {}

        await apply_mcp_bindings(
            server, {"Accept": "application/json"}, user_info=USER, denials=denials
        )

        assert denials == {}

    async def test_mcp_case_insensitive_denial_is_not_duplicated(self):
        """槽位名与 Header 名大小写不同时，不能同时给出两条矛盾拒因。"""
        key = f"mail.smtp.{uuid.uuid4().hex[:6]}"
        async with AsyncSessionLocal() as session:
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key=key,
                name="停用的邮箱",
                value=SMTP_JSON,
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=["mcp.example.com"],
                value_format="json",
                value_template="smtp",
                value_secret_fields=["password"],
            )
            # 绑在**小写**槽位上（配置里的 Header 名是 Authorization）
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="srv-case2",
                slot="header:authorization",
                bound_by_user_id=USER_ID,
            )
            await center.set_secret_status(session, secret_id=secret.id, status_value="disabled")

        server = SimpleNamespace(
            id="srv-case2", server_name="case2", sse_url="https://mcp.example.com/sse"
        )
        denials: dict[str, str] = {}
        await apply_mcp_bindings(
            server,
            {"Authorization": "${secret:" + key + ".password}"},
            user_info=USER,
            denials=denials,
        )

        # 只应剩「凭据已停用」，不该再加一条「这个头没有绑定」
        assert list(denials.values()) == ["disabled"]
        assert len({slot.lower() for slot in denials}) == 1


class TestValuePathExecution:
    """字段选好之后，执行那一步还差什么。

    三个容易漏的点：数字字段别变成字符串、一条凭据供多个参数只解密一次、
    取不到值时流水不能还写着成功。
    """

    JSON_VALUE = (
        '{"host": "smtp.exmail.qq.com", "port": 465, "tls": true, '
        '"tags": ["a", "b"], "username": "alert-bot@nanzi.ai", '
        '"password": "Sup3rSecret!2026"}'
    )

    async def _secret(self, session, key: str, **kwargs):
        options = {
            "owner_id": USER_ID,
            "secret_key": key,
            "name": "告警邮箱",
            "value": self.JSON_VALUE,
            "allowed_scenarios": [SCENE_MCP, SCENE_API_TOOL],
            "allowed_hosts": [],
            "value_format": "json",
            "value_template": "smtp",
            "value_secret_fields": ["password"],
        }
        options.update(kwargs)
        return await center.create_secret(session, **options)

    async def test_numeric_field_keeps_its_type(self):
        """`port: 465` 注进 MCP 参数必须是数字，不是 "465"。

        MCP 服务端按 schema 校验参数类型，字符串化的端口会被判类型不符——
        而错误信息只会说「参数 port 类型错」，看不出是平台这边转成了字符串。
        """
        async with AsyncSessionLocal() as session:
            rec = await self._secret(session, "mail.typed.n")
            await center.bind_secret(
                session,
                secret_id=rec.id,
                consumer_type="mcp_server",
                consumer_id="srv-typed-n",
                slot="arg:query:port",
                bound_by_user_id=USER_ID,
                value_path="port",
            )

        tool = SimpleNamespace(
            id="tool-typed-n",
            tool_name="server:query",
            server_id="srv-typed-n",
            credential_params='["port"]',
        )
        args = await apply_mcp_arg_bindings(tool, {}, user_info=USER)
        assert args["port"] == 465
        assert not isinstance(args["port"], str)

    async def test_boolean_and_array_fields_keep_their_type(self):
        async with AsyncSessionLocal() as session:
            rec_b = await self._secret(session, "mail.typed.b")
            secret_id = rec_b.id
            for name in ("tls", "tags"):
                await center.bind_secret(
                    session,
                    secret_id=secret_id,
                    consumer_type="mcp_server",
                    consumer_id="srv-typed-b",
                    slot=f"arg:query:{name}",
                    bound_by_user_id=USER_ID,
                    value_path=name,
                )

        tool = SimpleNamespace(
            id="tool-typed-b",
            tool_name="server:query",
            server_id="srv-typed-b",
            credential_params='["tls", "tags"]',
        )
        args = await apply_mcp_arg_bindings(tool, {}, user_info=USER)
        assert args["tls"] is True
        assert args["tags"] == ["a", "b"]

    async def test_header_still_gets_text(self):
        """Header 天生是文本：数字字段必须字符串化，否则 httpx 直接报错。"""
        async with AsyncSessionLocal() as session:
            rec_h = await self._secret(
                session, "mail.typed.h", allowed_hosts=["mcp.example.com"]
            )
            await center.bind_secret(
                session,
                secret_id=rec_h.id,
                consumer_type="mcp_server",
                consumer_id="srv-typed-h",
                slot="header:X-Port",
                bound_by_user_id=USER_ID,
                value_path="port",
            )

        server = SimpleNamespace(
            id="srv-typed-h", server_name="typed-h", sse_url="https://mcp.example.com/sse"
        )
        headers = await apply_mcp_bindings(
            server, {"X-Port": "${CREDENTIAL:mail.typed.h}"}, user_info=USER
        )
        assert headers["X-Port"] == "465"

    async def test_failed_injection_marks_the_usage_log_failed(self):
        """解密成功但没取出值，流水不能还写着 success。

        否则统计会把「配置错了」算成「用得好」：一条结构化凭据绑在 header 上却没选
        字段，每次都注入失败，报表里却是满屏成功。
        """
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            rec = await self._secret(
                session, "mail.audit.f", allowed_hosts=["mcp.example.com"]
            )
            await center.bind_secret(
                session,
                secret_id=rec.id,
                consumer_type="mcp_server",
                consumer_id="srv-audit",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
                # header 位点允许「不选字段」（值里可以写引用），这里正是走整段的情况
                value_path="",
            )

        server = SimpleNamespace(
            id="srv-audit", server_name="audit", sse_url="https://mcp.example.com/sse"
        )
        denials: dict[str, str] = {}
        await apply_mcp_bindings(
            server,
            {"Authorization": "${CREDENTIAL:mail.audit.f}"},
            user_info=USER,
            denials=denials,
        )
        assert denials["header:Authorization"] == "structured_without_path"

        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(SecretUsageLog).where(SecretUsageLog.secret_id == rec.id)
                )
            ).scalars().all()

        assert len(rows) == 1, "一次调用只该有一条流水"
        assert rows[0].result == "structured_without_path"

    async def test_json_null_field_is_a_value_not_a_failure(self):
        """``"proxy": null`` 是合法取值。

        用 ``None`` 兼作「取不到」的失败信号，会把正常的 null 判成引用失败：
        参数凭空缺一个、流水还记一条失败。
        """
        async with AsyncSessionLocal() as session:
            rec = await self._secret(
                session,
                "mail.null.f",
                value='{"host": "smtp.example.com", "proxy": null, "port": 465}',
                value_secret_fields=[],
            )
            await center.bind_secret(
                session,
                secret_id=rec.id,
                consumer_type="mcp_server",
                consumer_id="srv-null",
                slot="arg:tool:proxy",
                bound_by_user_id=USER_ID,
                value_path="proxy",
            )

        tool = SimpleNamespace(
            id="tool-null",
            tool_name="server:tool",
            server_id="srv-null",
            credential_params='["proxy"]',
        )
        denials: dict[str, str] = {}
        args = await apply_mcp_arg_bindings(
            tool, {"proxy": "placeholder"}, user_info=USER, denials=denials
        )

        assert "proxy" in args, "null 也应该被注入，而不是当作取不到"
        assert args["proxy"] is None
        assert denials == {}

    async def test_same_secret_is_decrypted_once_per_call(self):
        """一条凭据供多个参数时，一次调用只解密一次。

        三个参数指向同一条凭据就是三次 RSA 解密，而它们本来就该是同一份明文。
        """
        async with AsyncSessionLocal() as session:
            rec_c = await self._secret(session, "mail.cache.1")
            secret_id = rec_c.id
            for name in ("username", "password", "host"):
                await center.bind_secret(
                    session,
                    secret_id=secret_id,
                    consumer_type="mcp_server",
                    consumer_id="srv-cache",
                    slot=f"arg:query:{name}",
                    bound_by_user_id=USER_ID,
                    value_path=name,
                )

        calls = []
        original = secret_resolver._decrypt

        def counting(ciphertext):
            calls.append(ciphertext)
            return original(ciphertext)

        tool = SimpleNamespace(
            id="tool-cache",
            tool_name="server:query",
            server_id="srv-cache",
            credential_params='["username", "password", "host"]',
        )
        monkeypatch_decrypt = counting
        secret_resolver._decrypt = staticmethod(monkeypatch_decrypt)
        try:
            args = await apply_mcp_arg_bindings(tool, {}, user_info=USER)
        finally:
            secret_resolver._decrypt = staticmethod(original)

        assert args["username"] == "alert-bot@nanzi.ai"
        assert args["password"] == "Sup3rSecret!2026"
        assert args["host"] == "smtp.exmail.qq.com"
        assert len(calls) == 1, f"同一次调用解密了 {len(calls)} 次"


class TestBindingPicksField:
    """绑定声明了 ``value_path`` 时只注那一段，绝不整段 JSON 外发。

    「整段给」在单值凭据上是对的，在结构化凭据上几乎必然错：调用方拿到
    `{"host":…,"password":…}` 当密码用，错误点还离配置处很远。
    """

    JSON_VALUE = (
        '{"host": "smtp.exmail.qq.com", "port": 465, '
        '"username": "alert-bot@nanzi.ai", "password": "Sup3rSecret!2026"}'
    )

    async def _structured_secret(
        self,
        session,
        key: str,
        *,
        template: str = "smtp",
        hosts=("mcp.example.com",),
        name: str = "告警邮箱",
    ):
        return await center.create_secret(
            session,
            owner_id=USER_ID,
            secret_key=key,
            name=name,
            value=self.JSON_VALUE,
            allowed_scenarios=[SCENE_MCP, SCENE_API_TOOL],
            # arg 位点没有可解析的目标域名（工具记录不在库里），那条用例不设白名单
            allowed_hosts=list(hosts),
            value_format="json",
            value_template=template,
            value_secret_fields=["password"],
        )

    async def test_header_credential_placeholder_injects_only_that_field(self):
        """``${CREDENTIAL:key}`` + value_path=password → 注入的是密码，不是整段 JSON。"""
        async with AsyncSessionLocal() as session:
            secret = await self._structured_secret(session, "mail.pick.a")
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="srv-pick-a",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
                value_path="password",
            )

        server = SimpleNamespace(
            id="srv-pick-a", server_name="pick-a", sse_url="https://mcp.example.com/sse"
        )
        denials: dict[str, str] = {}
        headers = await apply_mcp_bindings(
            server, {"Authorization": "${CREDENTIAL:mail.pick.a}"}, user_info=USER, denials=denials
        )

        assert headers["Authorization"] == "Sup3rSecret!2026"
        assert "host" not in headers["Authorization"]
        assert denials == {}

    async def test_explicit_reference_wins_over_binding_path(self):
        """值里写了字段引用时以引用为准：同一凭据可以给不同槽位取不同字段。"""
        async with AsyncSessionLocal() as session:
            secret = await self._structured_secret(session, "mail.pick.b")
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="srv-pick-b",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
                value_path="password",
            )

        server = SimpleNamespace(
            id="srv-pick-b", server_name="pick-b", sse_url="https://mcp.example.com/sse"
        )
        headers = await apply_mcp_bindings(
            server,
            {"Authorization": "Bearer ${secret:mail.pick.b.host}"},
            user_info=USER,
        )

        assert headers["Authorization"] == "Bearer smtp.exmail.qq.com"

    async def test_missing_field_is_denied_not_sent_whole(self):
        """字段后来被删掉时记拒绝、保留原值——**不能**退回整段 JSON 发出去。"""
        async with AsyncSessionLocal() as session:
            secret = await self._structured_secret(session, "mail.pick.c")
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="srv-pick-c",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
                value_path="password",
            )
            # 轮换成没有 password 的结构，模拟「绑定时有、后来没了」
            await center.rotate_secret(
                session,
                secret_id=secret.id,
                new_value='{"host": "smtp.exmail.qq.com"}',
                rotated_by=USER_ID,
            )

        server = SimpleNamespace(
            id="srv-pick-c", server_name="pick-c", sse_url="https://mcp.example.com/sse"
        )
        denials: dict[str, str] = {}
        headers = await apply_mcp_bindings(
            server, {"Authorization": "${CREDENTIAL:mail.pick.c}"}, user_info=USER, denials=denials
        )

        assert headers["Authorization"] == "${CREDENTIAL:mail.pick.c}"
        assert denials.get("header:Authorization") == "reference_field_missing"

    async def test_mcp_arg_gets_only_the_field(self):
        """MCP 工具参数是整值注入，字段选取只能靠绑定里的 value_path。"""
        # credential_params 是**参数名数组**；槽位由工具短名 + 参数名推出来
        tool = SimpleNamespace(
            id="tool-pick",
            server_id="srv-pick-d",
            tool_name="server:query-customer",
            credential_params='["api_key"]',
        )
        async with AsyncSessionLocal() as session:
            secret = await self._structured_secret(session, "mail.pick.d", hosts=())
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="srv-pick-d",
                slot="arg:query-customer:api_key",
                bound_by_user_id=USER_ID,
                value_path="password",
            )

        result = await apply_mcp_arg_bindings(tool, {"keyword": "x"}, user_info=USER)

        assert result["api_key"] == "Sup3rSecret!2026"

    async def test_rule_binding_also_takes_a_field(self):
        """规则只决定「哪一条凭据」，取哪个字段仍由绑定声明——命中后照样按它取。"""
        async with AsyncSessionLocal() as session:
            # name 设成 user_id，好让规则按 user.user_id 精确命中它
            secret = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mail.pick.e",
                name=USER_ID,
                value=self.JSON_VALUE,
                allowed_scenarios=[SCENE_MCP, SCENE_API_TOOL],
                allowed_hosts=["mcp.example.com"],
                value_format="json",
                value_template="smtp",
                value_secret_fields=["password"],
            )
            await center.bind_secret(
                session,
                secret_id=secret.id,
                consumer_type="mcp_server",
                consumer_id="srv-pick-e",
                slot="header:Authorization",
                bound_by_user_id=USER_ID,
                value_path="password",
            )
            # 同槽位再挂一条规则，规则自己也约定「取 password」
            await center.bind_rule(
                session,
                consumer_type="mcp_server",
                consumer_id="srv-pick-e",
                slot="header:Authorization",
                rule={"key_source": "user.user_id", "target": "name", "match": "exact"},
                bound_by_user_id=USER_ID,
                value_path="password",
            )

        server = SimpleNamespace(
            id="srv-pick-e", server_name="pick-e", sse_url="https://mcp.example.com/sse"
        )
        headers = await apply_mcp_bindings(
            server, {"Authorization": "${CREDENTIAL:mail.pick.e}"}, user_info=USER
        )

        # 规则命中也按 value_path 取字段：规则挑中的凭据结构未必一致，
        # 但「取哪个字段」是规则自己要声明的约定
        assert headers["Authorization"] == "Sup3rSecret!2026"

    async def test_rule_without_field_rejects_structured_secret(self):
        """规则命中结构化凭据但没声明字段 → 不注入整段 JSON。

        整份 JSON 注进 Authorization 只会让对方回 401 或 400，而界面上看不出
        是「少配了一个字段」。
        """
        async with AsyncSessionLocal() as session:
            secret = await self._structured_secret(session, "mail.pick.f", name=USER_ID)
            await center.bind_rule(
                session,
                consumer_type="mcp_server",
                consumer_id="srv-pick-f",
                slot="header:Authorization",
                rule={"key_source": "user.user_id", "target": "name", "match": "exact"},
                bound_by_user_id=USER_ID,
            )

        server = SimpleNamespace(
            id="srv-pick-f", server_name="pick-f", sse_url="https://mcp.example.com/sse"
        )
        denials: dict[str, str] = {}
        headers = await apply_mcp_bindings(
            server,
            {"Authorization": "${CREDENTIAL:mail.pick.f}"},
            user_info=USER,
            denials=denials,
        )

        assert headers["Authorization"] == "${CREDENTIAL:mail.pick.f}"
        assert denials["header:Authorization"] == "structured_without_path"


class TestPrefixedCredentialPlaceholder:
    """`Bearer ${CREDENTIAL:key}` 这种「前缀 + 占位符」是 header 编辑器的默认产物。

    它既不能被当成「用户手填的真值」发出去，也不能因为不匹配整段前缀就在
    未绑定时被放过——两边的判断都得认片段形态。
    """

    def test_looks_unfilled_sees_a_fragment(self):
        from app.services.secrets.injection import _looks_unfilled

        assert _looks_unfilled("${CREDENTIAL:mail.gov}") is True
        assert _looks_unfilled("Bearer ${CREDENTIAL:mail.gov}") is True
        # 真实值不能被误判成待填——否则用户手填的 header 会被清掉
        assert _looks_unfilled("Bearer sk-live-abcdef") is False

    def test_unresolved_headers_catches_the_fragment(self):
        from app.services.ai.tools.tool_credentials import (
            unbound_reason,
            unresolved_credential_headers,
        )

        headers = {"Authorization": "Bearer ${CREDENTIAL:platform.mail}", "X-Trace": "abc"}
        assert unresolved_credential_headers(headers) == ["Authorization"]
        assert unbound_reason("Bearer ${CREDENTIAL:platform.mail}") == "credential_unbound"

    async def test_prefixed_value_still_injects_with_prefix(self):
        """有绑定时整段渲染：认证方案保留，只替换占位符那一截。"""
        from app.services.secrets.injection import _render_reference

        rendered = _render_reference(
            "Bearer ${CREDENTIAL:platform.mail}",
            "MyOwnPwd!2026",
            secret_key="platform.mail",
            slot="header:Authorization",
            denials={},
            value_path="",
        )
        assert rendered == "Bearer MyOwnPwd!2026"


class TestDenialReasonWithoutCollector:
    """不传 denials 时，流水里也要能看出真实拒因。

    五个注入入口的 `denials` 都是可选参数。传 None 时 `_record` 什么都不写，
    最后 `mark_usage_failed` 只能退回通用码 `reference_unresolved`——
    事后翻流水只知道「失败了」，分不清是「结构化凭据没选字段」还是「字段不存在」。
    """

    def test_every_entry_prepares_a_collector(self):
        """契约：每个对外注入入口进来先补一个局部字典。

        行为测试很难覆盖——这些入口都要查库，而「不传 denials」恰恰是生产上
        不会发生的调用方式（六个调用点都传了）。但接口既然允许省略，就不能
        让流水记错原因，所以用契约钉住。
        """
        from pathlib import Path

        source = Path("app/services/secrets/injection.py").read_text(encoding="utf-8")
        entries = [
            "apply_api_tool_bindings",
            "apply_api_tool_param_bindings",
            "apply_mcp_bindings",
            "apply_mcp_arg_bindings",
            "apply_script_env_bindings",
        ]
        for name in entries:
            body = source.split(f"async def {name}(")[1].split("\nasync def ")[0]
            assert "if denials is None:" in body, f"{name} 没有给 denials 兜底"

    def test_missing_field_records_a_specific_reason(self):
        """真实拒因是具体的，不是通用码。"""
        from app.services.secrets.injection import _apply_value_path

        denials: dict[str, str] = {}
        picked = _apply_value_path(
            '{"host": "h"}',
            "nope",
            slot="arg:tool:proxy",
            denials=denials,
            value_format="json",
        )
        from app.services.secrets.injection import _MISSING

        assert picked is _MISSING
        assert denials["arg:tool:proxy"] == "reference_field_missing"


class TestMcpAuthorizationKeepsItsScheme:
    """MCP 服务的 Authorization 改走凭据中心后，不能把 ``Bearer `` 丢掉。

    「固定 Token」入口只让用户填 token，前缀由后端拼；凭据这条路的原 headers 里
    往往根本没有 Authorization，渲染结果是裸凭据。两个入口对同一个字段给出不同
    形态，症状是「验证用旧 Token 能连上、实际调用 401」——而两边看代码都没错。
    """

    def test_bare_token_gets_bearer(self):
        from app.services.secrets.injection import _keep_auth_scheme

        assert _keep_auth_scheme("abc123", None) == "Bearer abc123"
        assert _keep_auth_scheme("abc123", "") == "Bearer abc123"

    def test_existing_scheme_is_respected(self):
        from app.services.secrets.injection import _keep_auth_scheme

        assert _keep_auth_scheme("abc123", "Token old") == "Token abc123"

    def test_explicit_placeholder_is_left_alone(self):
        """用户写了裸占位符就是想要裸值，替他补 Bearer 等于改配置。"""
        from app.services.secrets.injection import _keep_auth_scheme

        assert _keep_auth_scheme("abc123", "${CREDENTIAL:mail.smtp}") == "abc123"
        assert _keep_auth_scheme("abc123", "${secret:mail.smtp.password}") == "abc123"

    def test_value_carrying_its_own_scheme_is_untouched(self):
        """凭据本身是完整头（Basic / 已带 Bearer）时不要再套一层。"""
        from app.services.secrets.injection import _keep_auth_scheme

        assert _keep_auth_scheme("Basic dXNlcjpwYXNz", "") == "Basic dXNlcjpwYXNz"
        assert _keep_auth_scheme("Bearer abc123", None) == "Bearer abc123"

    def test_only_authorization_is_touched(self):
        """其它 header 原样注入：给 X-Api-Key 补 Bearer 就是凭空造了个错值。"""
        from pathlib import Path

        source = (Path(__file__).parents[2] / "app/services/secrets/injection.py").read_text(
            encoding="utf-8"
        )
        assert 'if name.lower() == "authorization":' in source


class TestDeclaredTypeCoercion:
    """凭据喂给数字/布尔参数：注入层按远端 schema 转类型。

    背景：配置期原来只允许 string 参数绑凭据，理由是「凭据值永远是字符串」。
    但端口、超时、重试次数这些连接参数恰好都是数字，它们既不该让模型每次猜，
    也不在 Header 里——「只允许 string」实际等于告诉用户「平台管不了这个参数」。
    放开之后转换责任落在注入层，转不了要能在流水里看出是这个原因。
    """

    def test_integer_accepts_digit_string(self):
        from app.services.secrets.injection import coerce_to_declared_type

        assert coerce_to_declared_type("465", "integer") == (465, None)

    def test_integer_rejects_decimal_string(self):
        """整数字段给 "465.0" 应当拒绝，而不是悄悄取整——差值可能正是被忽略的那一位。"""
        from app.services.secrets.injection import coerce_to_declared_type

        value, error = coerce_to_declared_type("465.0", "integer")

        assert error == "type_mismatch"
        assert value == "465.0"

    def test_number_keeps_integer_form(self):
        from app.services.secrets.injection import coerce_to_declared_type

        # 整数写法不要变成 465.0：远端 schema 是 number 时也照样接受 int
        assert coerce_to_declared_type("465", "number") == (465, None)
        assert coerce_to_declared_type("1.5", "number") == (1.5, None)

    def test_boolean_accepts_common_spellings(self):
        from app.services.secrets.injection import coerce_to_declared_type

        for text in ("true", "TRUE", "1", "yes", "on"):
            assert coerce_to_declared_type(text, "boolean") == (True, None), text
        for text in ("false", "0", "no", "off"):
            assert coerce_to_declared_type(text, "boolean") == (False, None), text

    def test_boolean_rejects_garbage(self):
        from app.services.secrets.injection import coerce_to_declared_type

        assert coerce_to_declared_type("maybe", "boolean")[1] == "type_mismatch"

    def test_string_param_stringifies_numbers(self):
        from app.services.secrets.injection import coerce_to_declared_type

        # 远端要字符串时，凭据里的数字 465 得给成 "465"
        assert coerce_to_declared_type(465, "string") == ("465", None)

    def test_undeclared_type_passes_through(self):
        """没声明类型时维持旧行为：原样给，平台不发明转换规则。"""
        from app.services.secrets.injection import coerce_to_declared_type

        assert coerce_to_declared_type(465, None) == (465, None)
        assert coerce_to_declared_type("anything", None) == ("anything", None)

    def test_structured_types_are_left_alone(self):
        """对象/数组不转换：配置期已经拦掉，运行时也不在这里造规则。"""
        from app.services.secrets.injection import coerce_to_declared_type

        assert coerce_to_declared_type({"a": 1}, "object") == ({"a": 1}, None)
        assert coerce_to_declared_type([1, 2], "array") == ([1, 2], None)

    def test_union_type_picks_the_scalar(self):
        """nullable 的 integer 参数在远端仍按 integer 校验。"""
        from app.services.secrets.injection import declared_param_type

        assert declared_param_type({"type": ["integer", "null"]}) == "integer"
        assert declared_param_type({"type": ["string", "null"]}) == "string"
        assert declared_param_type({"type": ["object", "null"]}) is None
        assert declared_param_type({}) is None
        assert declared_param_type(None) is None

    async def test_string_field_is_coerced_for_integer_param(self):
        """凭据里存成 "465"（字符串），参数声明 integer，注入时必须是数字。"""
        async with AsyncSessionLocal() as session:
            rec = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mail.port.text",
                name="邮件端口（写成了字符串）",
                value='{"port": "465"}',
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=[],
                value_format="json",
            )
            await center.bind_secret(
                session,
                secret_id=rec.id,
                consumer_type="mcp_server",
                consumer_id="srv-coerce-1",
                slot="arg:list-mail:port",
                bound_by_user_id=USER_ID,
                value_path="port",
            )

        tool = SimpleNamespace(
            id="tool-coerce-1",
            tool_name="server:list-mail",
            server_id="srv-coerce-1",
            credential_params='["port"]',
            parameter_schema='{"properties": {"port": {"type": "integer"}}}',
        )

        args = await apply_mcp_arg_bindings(tool, {}, user_info=USER)

        assert args["port"] == 465
        assert not isinstance(args["port"], str)

    async def test_uncoercible_value_is_recorded_as_type_mismatch(self):
        """转不了就拒绝注入，并在 denials 里留下具体原因——别把原值硬塞出去。"""
        async with AsyncSessionLocal() as session:
            rec = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mail.port.bad",
                name="邮件端口（填错了）",
                value='{"port": "四百六十五"}',
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=[],
                value_format="json",
            )
            await center.bind_secret(
                session,
                secret_id=rec.id,
                consumer_type="mcp_server",
                consumer_id="srv-coerce-2",
                slot="arg:list-mail:port",
                bound_by_user_id=USER_ID,
                value_path="port",
            )

        tool = SimpleNamespace(
            id="tool-coerce-2",
            tool_name="server:list-mail",
            server_id="srv-coerce-2",
            credential_params='["port"]',
            parameter_schema='{"properties": {"port": {"type": "integer"}}}',
        )
        denials: dict[str, str] = {}

        args = await apply_mcp_arg_bindings(tool, {}, user_info=USER, denials=denials)

        assert "port" not in args
        assert denials["arg:list-mail:port"] == "type_mismatch"

    async def test_declaration_without_schema_behaves_as_before(self):
        """工具没带 parameter_schema 时按「没声明」处理，老工具不受影响。"""
        async with AsyncSessionLocal() as session:
            rec = await center.create_secret(
                session,
                owner_id=USER_ID,
                secret_key="mail.port.legacy",
                name="邮件端口（老工具）",
                value='{"port": "465"}',
                allowed_scenarios=[SCENE_MCP],
                allowed_hosts=[],
                value_format="json",
            )
            await center.bind_secret(
                session,
                secret_id=rec.id,
                consumer_type="mcp_server",
                consumer_id="srv-coerce-3",
                slot="arg:list-mail:port",
                bound_by_user_id=USER_ID,
                value_path="port",
            )

        tool = SimpleNamespace(
            id="tool-coerce-3",
            tool_name="server:list-mail",
            server_id="srv-coerce-3",
            credential_params='["port"]',
        )

        args = await apply_mcp_arg_bindings(tool, {}, user_info=USER)

        # 没有 schema 就原样给：改动前是什么样，现在还什么样
        assert args["port"] == "465"
