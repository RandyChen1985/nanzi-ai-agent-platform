import pytest
from app.services.ai.tools.registry import ToolRegistry
from app.schemas.agent import ToolConfigItem


@pytest.mark.asyncio
async def test_get_tools_does_not_inject_runtime_config():
    """历史 `_runtime_config` 注入链路已删除：没有任何消费者，只造成「配了却没生效」的错觉。"""
    tools = await ToolRegistry.get_tools(
        [ToolConfigItem(name="execute_sql_query", model_name="gpt-4o", temperature=0.1)]
    )

    assert len(tools) == 1
    assert not hasattr(tools[0], "_runtime_config")


@pytest.mark.asyncio
async def test_runtime_tool_spec_applies_description_override(monkeypatch):
    """运行时唯一真正生效的高级项：描述覆盖写进模型可见的 schema。"""

    async def fake_http_tool(**kwargs):
        return "ok"

    fake_http_tool.name = "system_http_request"
    fake_http_tool.description = "原始描述"
    monkeypatch.setitem(ToolRegistry._registry, "system_http_request", fake_http_tool)

    specs = await ToolRegistry.get_runtime_tools(
        [{"name": "system_http_request", "description_override": "覆盖后的描述"}]
    )

    assert specs[0].description == "覆盖后的描述"


@pytest.mark.asyncio
async def test_runtime_tool_spec_marks_internal_llm_capability(monkeypatch):
    """能力标记不能随「有没有配置」漂移：没配高级参数的工具也要带上标记。"""

    async def fake_read_image(**kwargs):
        return "ok"

    fake_read_image.name = "read_image"
    fake_read_image.description = "读取图片"
    monkeypatch.setitem(ToolRegistry._registry, "read_image", fake_read_image)

    plain_specs = await ToolRegistry.get_runtime_tools(["read_image"])
    assert plain_specs[0].supports_internal_llm is True

    configured_specs = await ToolRegistry.get_runtime_tools(
        [{"name": "read_image", "temperature": 0.4}]
    )
    assert configured_specs[0].supports_internal_llm is True


@pytest.mark.asyncio
async def test_runtime_tool_wraps_temperature_only_for_internal_llm_tool(monkeypatch):
    """工具级温度通过调用上下文传给工具内部，纯函数工具不读也不配。"""
    from app.core.context import get_current_tool_temperature

    seen = {}

    async def fake_read_image(**kwargs):
        seen["temperature"] = get_current_tool_temperature()
        return "ok"

    fake_read_image.name = "read_image"
    fake_read_image.description = "读取图片"

    async def fake_sql_tool(**kwargs):
        seen["sql_temperature"] = get_current_tool_temperature()
        return "ok"

    fake_sql_tool.name = "execute_sql_query"
    fake_sql_tool.description = "执行 SQL"

    monkeypatch.setitem(ToolRegistry._registry, "read_image", fake_read_image)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql_tool)

    specs = await ToolRegistry.get_runtime_tools(
        [
            {"name": "read_image", "temperature": 0.4},
            {"name": "execute_sql_query", "temperature": 0.9},
        ]
    )
    by_name = {spec.name: spec for spec in specs}

    await by_name["read_image"].callable(path="a.png")
    assert seen["temperature"] == 0.4

    # 纯函数工具不包装：即使配置里写了温度，调用上下文也没有覆盖
    await by_name["execute_sql_query"].callable(sql="select 1")
    assert seen["sql_temperature"] is None

    # 调用结束后上下文必须复位，不能泄漏到下一条工具调用
    assert get_current_tool_temperature() is None


@pytest.mark.asyncio
async def test_runtime_tool_config_preserves_schema_dataset_scope(monkeypatch):
    """AgentScope runtime 工具也要保留 get_dataset_schema 的数据集配置。"""
    calls = []

    async def fake_schema_tool(**kwargs):
        calls.append(kwargs)
        return "schema ok"

    fake_schema_tool.name = "get_dataset_schema"
    fake_schema_tool.description = "fake schema"
    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema_tool)

    specs = await ToolRegistry.get_runtime_tools([
        {
            "name": "get_dataset_schema",
            "metadata_dataset_ids": ["1", "3"],
        }
    ])

    assert len(specs) == 1
    assert specs[0].name == "get_dataset_schema"

    result = await specs[0].callable(keywords="销售")

    assert result == "schema ok"
    assert calls == [{"keywords": "销售", "metadata_dataset_ids": ["1", "3"]}]


@pytest.mark.asyncio
async def test_runtime_tool_config_forces_schema_dataset_scope(monkeypatch):
    """模型显式传空列表或其他 ID 时，不能覆盖平台绑定的数据集范围。"""
    calls = []

    async def fake_schema_tool(**kwargs):
        calls.append(kwargs)
        return "schema ok"

    fake_schema_tool.name = "get_dataset_schema"
    fake_schema_tool.description = "fake schema"
    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema_tool)

    specs = await ToolRegistry.get_runtime_tools([
        {
            "name": "get_dataset_schema",
            "metadata_dataset_ids": ["1", "3"],
        }
    ])

    await specs[0].callable(keywords="销售", metadata_dataset_ids=[])
    await specs[0].callable(keywords="销售", metadata_dataset_ids=["999"])

    assert calls == [
        {"keywords": "销售", "metadata_dataset_ids": ["1", "3"]},
        {"keywords": "销售", "metadata_dataset_ids": ["1", "3"]},
    ]


@pytest.mark.asyncio
async def test_get_tools_returns_copies_not_registry_instances():
    """`get_tool()` 给的是注册表共享实例：get_tools 必须浅拷贝，否则调用方一改就污染全局。"""

    class _Shared:
        name = "shared_echo_tool"
        description = "原始描述"

    shared = _Shared()
    original_registry = dict(ToolRegistry._registry)
    ToolRegistry._registry["shared_echo_tool"] = shared
    try:
        tools = await ToolRegistry.get_tools(["shared_echo_tool"])
        assert tools and tools[0] is not shared

        # 改副本不影响注册表里的原件
        tools[0].description = "被调用方改过"
        assert shared.description == "原始描述"
    finally:
        ToolRegistry._registry.clear()
        ToolRegistry._registry.update(original_registry)


@pytest.mark.asyncio
async def test_dirty_tool_timeout_does_not_drop_other_config(monkeypatch):
    """历史 JSON 里 timeout_seconds 非法时，描述覆盖等其余配置必须仍然生效。"""

    async def fake_http_tool(**kwargs):
        return "ok"

    fake_http_tool.name = "system_http_request"
    fake_http_tool.description = "原始描述"
    monkeypatch.setitem(ToolRegistry._registry, "system_http_request", fake_http_tool)

    specs = await ToolRegistry.get_runtime_tools(
        [
            {
                "name": "system_http_request",
                "timeout_seconds": 0,
                "description_override": "覆盖后的描述",
            }
        ]
    )

    assert len(specs) == 1
    assert specs[0].description == "覆盖后的描述"


def test_dirty_timeout_is_coerced_before_chat_config_validation():
    """脏超时必须在 schema 层就清掉，不能留到 ChatConfig。

    ChatConfig.tools 直接持有 ToolConfigItem：库里只要有一个 `timeout_seconds: 0`，
    整份会话配置校验就失败 → 智能体直接不可用。丢一个字段远好过整轮挂掉。
    """
    from app.schemas.agent import ChatConfig, ToolConfigItem

    assert ToolConfigItem(name="t", timeout_seconds=0).timeout_seconds is None
    assert ToolConfigItem(name="t", timeout_seconds=-5).timeout_seconds is None
    assert ToolConfigItem(name="t", timeout_seconds="abc").timeout_seconds is None
    assert ToolConfigItem(name="t", timeout_seconds=7200).timeout_seconds is None
    assert ToolConfigItem(name="t", timeout_seconds=10).timeout_seconds == 10
    assert ToolConfigItem(name="t", timeout_seconds="12").timeout_seconds == 12

    config = ChatConfig(
        agent_id="1",
        agent_name="a",
        model_name="m",
        temperature=0.7,
        system_prompt="p",
        tools=[{"name": "Bash", "timeout_seconds": 0, "description_override": "x"}],
    )
    assert config.tools[0].timeout_seconds is None
    # 脏超时不能连坐掉别的配置
    assert config.tools[0].description_override == "x"
