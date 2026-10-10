"""工具 JSON Schema 预检与语义等价净化契约。

背景：平台把 MCP 远端 ``inputSchema`` 原样透传给模型（`mcp_factory.py` 有意保留远端
原始 Schema，`args_schema` 只管本地校验），而上游一旦启用 constrained decoding
（vLLM / xgrammar / outlines 一类）就会对 tools schema 做严格编译，遇到非法约束即整单
400——43 个工具里 1 个坏 schema 会废掉整轮请求，且上游返回的路径自带遮蔽
（``***.***.***.parameters/required``），点不出工具名。

约定：
1. **预检必须点名**（工具名 + 问题 + 路径），否则等于没查；
2. **只做语义等价的修复**，绝不摘工具、绝不改其它关键字；
3. 静态拿不准的结构（``anyOf`` / ``oneOf`` / ``allOf`` 分支上的 ``required``）宁可不改，
   避免误伤本来能用的工具。
"""

import logging
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.no_infrastructure

LOGGER_NAME = "app.services.ai.runtime.agentscope.tool_schema_sanitize"
ISSUE_MARKER = "Tool schema issue"


def _tool(name, parameters):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "",
            "parameters": parameters,
        },
    }


def _params(tools, index=0):
    return tools[index]["function"]["parameters"]


def _issues(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER_NAME and ISSUE_MARKER in record.getMessage()
    ]


# ── 预检必须点名 ────────────────────────────────────────────────────────────────

def test_required_referencing_undefined_property_is_dropped_and_the_tool_is_named(
    caplog,
):
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "mcp_lookup_abnormal_id",
            {
                "type": "object",
                "properties": {"abnormal_id": {"type": "string"}},
                "required": ["abnormal_id", "not_defined_anywhere"],
            },
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    assert _params(out)["required"] == ["abnormal_id"], "未定义属性应从 required 剔除"

    logs = _issues(caplog)
    assert len(logs) == 1
    assert "mcp_lookup_abnormal_id" in logs[0], "必须点名到具体工具"
    assert "not_defined_anywhere" in logs[0]
    assert "$.required" in logs[0], "必须给出可定位的路径"


def test_empty_required_is_preserved(caplog):
    """``required: []`` 是合法写法（等价于无必填项），必须原样保留。

    删掉它等于让根层丢掉 ``required`` 这个关键字，反而会触发上游 400——这与
    「根层缺 ``required`` 就补」是同一件事的两面，方向必须一致。
    """
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "empty_required_tool",
            {"type": "object", "properties": {}, "required": []},
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    assert _params(out)["required"] == [], "required: [] 必须原样保留"
    assert out is tools, "无需修复时不得改动对象"
    assert _issues(caplog) == []


# ── 根层缺 required：上游要求该关键字存在，缺了整单 400 ──────────────────────────

@pytest.mark.parametrize(
    "parameters",
    [
        {"type": "object", "properties": {"a": {"type": "string"}}},
        {"type": "object", "properties": {}},
        {"type": "object"},
        {},
    ],
    ids=["with-property", "empty-properties", "type-only", "empty-schema"],
)
def test_missing_root_required_is_filled_and_named(parameters, caplog, monkeypatch):
    from app.services.ai.runtime.agentscope import tool_schema_sanitize as mod

    monkeypatch.setattr(mod, "_REPORTED_ISSUES", set())

    tools = [_tool("no_required_tool", dict(parameters))]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = mod.sanitize_tool_schemas(tools)

    assert _params(out)["required"] == [], "根层必须补上空 required"

    logs = _issues(caplog)
    assert len(logs) == 1
    assert "no_required_tool" in logs[0], "必须点名到具体工具"
    assert "missing_required" in logs[0]
    assert "$.required" in logs[0]


def test_missing_root_required_is_aggregated_into_one_line(monkeypatch, caplog):
    """多个工具都缺 ``required`` 时只汇总一条，而不是每个工具各刷一条。

    缺 ``required`` 是常态（多数 MCP 工具都不写这个关键字），逐个工具点名会让首轮
    刷出几十条日志。
    """
    from app.services.ai.runtime.agentscope import tool_schema_sanitize as mod

    monkeypatch.setattr(mod, "_REPORTED_ISSUES", set())
    monkeypatch.setattr(mod, "_PENDING_MISSING_REQUIRED", [])

    tools = [
        _tool(
            f"agg_tool_{i}",
            {"type": "object", "properties": {"a": {"type": "string"}}},
        )
        for i in range(5)
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = mod.sanitize_tool_schemas(tools)

    assert [_params(out, i)["required"] for i in range(5)] == [[]] * 5

    logs = _issues(caplog)
    assert len(logs) == 1, f"应聚合为一条，实际 {len(logs)} 条"
    assert "missing_required" in logs[0]
    assert "count=5" in logs[0]
    for i in range(5):
        assert f"agg_tool_{i}" in logs[0], "聚合日志必须列出被修复的工具名"


def test_missing_required_summary_is_reported_only_once_per_process(
    monkeypatch, caplog
):
    from app.services.ai.runtime.agentscope import tool_schema_sanitize as mod

    monkeypatch.setattr(mod, "_REPORTED_ISSUES", set())
    monkeypatch.setattr(mod, "_PENDING_MISSING_REQUIRED", [])

    tools = [
        _tool("once_tool", {"type": "object", "properties": {"a": {"type": "string"}}})
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        mod.sanitize_tool_schemas(tools)
        first_round = _issues(caplog)
        caplog.clear()
        mod.sanitize_tool_schemas(tools)
        second_round = _issues(caplog)

    assert len(first_round) == 1
    assert second_round == [], "同一问题不应每轮对话都报"


def test_aggregated_summary_coexists_with_per_tool_issues(monkeypatch, caplog):
    """聚合的 missing_required 与逐条点名的其它问题互不干扰。"""
    from app.services.ai.runtime.agentscope import tool_schema_sanitize as mod

    monkeypatch.setattr(mod, "_REPORTED_ISSUES", set())
    monkeypatch.setattr(mod, "_PENDING_MISSING_REQUIRED", [])

    tools = [
        _tool(
            "agg_first",
            {"type": "object", "properties": {"a": {"type": "string"}}},
        ),
        _tool(
            "undefined_second",
            {
                "type": "object",
                "properties": {"b": {"type": "string"}},
                "required": ["b", "ghost"],
            },
        ),
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        mod.sanitize_tool_schemas(tools)

    logs = _issues(caplog)
    assert len(logs) == 2, f"应为「一条聚合 + 一条逐条」，实际 {len(logs)} 条"

    summary = [line for line in logs if "count=" in line]
    per_tool = [
        line for line in logs if "count=" not in line and "undefined_second" in line
    ]
    assert len(summary) == 1
    assert "agg_first" in summary[0]
    assert len(per_tool) == 1


def test_nested_missing_required_is_not_filled(caplog):
    """嵌套属性本来就不写 ``required``，补了只是噪音：只在根层补。"""
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "nested_no_required_tool",
            {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    }
                },
                "required": ["filter"],
            },
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    nested = _params(out)["properties"]["filter"]
    assert "required" not in nested, "嵌套层不得被补 required"
    assert _issues(caplog) == []


def test_filling_required_keeps_everything_else_intact(caplog):
    """补 ``[]`` 只新增一个关键字，其它内容一个字都不能动（语义中立）。"""
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    parameters = {
        "type": "object",
        "title": "NeutralArgs",
        "description": "keep me",
        "additionalProperties": False,
        "properties": {
            "a": {"type": "string", "enum": ["x", "y"]},
            "b": {"type": "integer"},
        },
    }
    tools = [_tool("neutral_tool", parameters)]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    fixed = _params(out)
    assert fixed["required"] == []
    assert {key: value for key, value in fixed.items() if key != "required"} == parameters
    assert out[0]["function"]["name"] == "neutral_tool"
    assert out[0]["type"] == "function"


# ── 健康 schema 必须一动不动 ────────────────────────────────────────────────────

def test_healthy_schema_is_returned_untouched(caplog):
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "healthy_tool",
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["a"],
            },
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    assert out is tools, "无问题时必须原样返回（保持对象身份，不做无谓拷贝）"
    assert out[0] is tools[0]
    assert _issues(caplog) == [], "健康 schema 不得产生任何噪音"


def test_only_targeted_keywords_are_touched(caplog):
    """净化不得顺手改动其它关键字或工具元数据。"""
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    parameters = {
        "type": "object",
        "title": "LookupArgs",
        "description": "keep me",
        "additionalProperties": False,
        "properties": {
            "mode": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["mode", "ghost"],
    }
    tools = [_tool("keyword_guard_tool", parameters)]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    fixed = _params(out)
    assert fixed["required"] == ["mode"]
    assert fixed["title"] == "LookupArgs"
    assert fixed["description"] == "keep me"
    assert fixed["additionalProperties"] is False
    assert fixed["properties"] == {"mode": {"type": "string", "enum": ["a", "b"]}}
    assert out[0]["function"]["description"] == ""
    assert out[0]["type"] == "function"


# ── 递归覆盖各种嵌套位置 ────────────────────────────────────────────────────────

def test_nested_object_property_is_repaired(caplog):
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "nested_tool",
            {
                "type": "object",
                # 根层显式声明 required，本用例只聚焦「嵌套层」的修复
                "properties": {
                    "filter": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id", "ghost"],
                    }
                },
                "required": ["filter"],
            },
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    nested = _params(out)["properties"]["filter"]
    assert nested["required"] == ["id"]
    logs = _issues(caplog)
    assert len(logs) == 1
    assert "$.properties.filter.required" in logs[0]


def test_array_items_are_repaired(caplog):
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "items_tool",
            {
                "type": "object",
                # 根层显式声明 required，本用例只聚焦「数组元素」的修复
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name", "ghost"],
                        },
                    }
                },
                "required": ["rows"],
            },
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    items = _params(out)["properties"]["rows"]["items"]
    assert items["required"] == ["name"]
    assert "$.properties.rows.items.required" in _issues(caplog)[0]


def test_anyof_branch_is_repaired(caplog):
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "anyof_tool",
            {
                # 根层显式声明 required，本用例只聚焦「anyOf 分支」的修复
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                        "required": ["a", "ghost"],
                    }
                ],
                "required": ["a"],
            },
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    branch = _params(out)["anyOf"][0]
    assert branch["required"] == ["a"]
    assert "$.anyOf[0].required" in _issues(caplog)[0]


def test_required_beside_anyof_is_left_alone(caplog):
    """``required`` 与 ``anyOf`` 并列时无法静态确定合法字段集：宁可不改。"""
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "ambiguous_tool",
            {
                "anyOf": [
                    {"properties": {"a": {"type": "string"}}},
                    {"properties": {"b": {"type": "string"}}},
                ],
                "required": ["a", "b"],
            },
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    assert _params(out)["required"] == ["a", "b"], "歧义结构不得被误改"
    assert _issues(caplog) == []


def test_required_beside_anyof_with_properties_is_left_alone(caplog):
    """同时有 ``properties`` 与 ``anyOf`` 时，合法字段集可能定义在分支里：不动。"""
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "ambiguous_with_properties_tool",
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "anyOf": [
                    {"properties": {"b": {"type": "string"}}, "required": ["b"]},
                ],
                "required": ["a", "ghost"],
            },
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    assert _params(out)["required"] == ["a", "ghost"], "歧义结构不得被误改"
    assert _issues(caplog) == []


def test_object_without_properties_is_left_alone(caplog):
    """没有 ``properties`` 的 object（自由字典）不应被当成"引用不存在属性"。"""
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "freeform_tool",
            {"type": "object", "required": ["anything"]},
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    assert _params(out)["required"] == ["anything"]
    assert _issues(caplog) == []


# ── 健壮性 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "tools",
    [
        None,
        [],
        "not-a-list",
        [{"type": "function"}],
        [{"type": "function", "function": {"name": "x"}}],
        [{"type": "function", "function": {"name": "y", "parameters": None}}],
        [{"type": "function", "function": {"name": "z", "parameters": "bad"}}],
        [{"type": "function", "function": {"name": "r", "parameters": {"required": "bad"}}}],
        [{"type": "function", "function": {"name": "p", "parameters": {"properties": 3, "required": ["a"]}}}],
    ],
)
def test_malformed_input_never_raises(tools):
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    sanitize_tool_schemas(tools)


def test_one_odd_tool_does_not_stop_the_rest_from_being_repaired(caplog):
    """一个形态古怪的工具不得拖累整批工具。

    预检是逐个工具进行的：若某个工具触发内部异常而被整体兜底，前面已修复的工具会
    一起白修（整批退回未净化状态），这正违背「只修坏的那一个」的初衷。
    """
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool("freeform_first_tool", {"type": "object", "required": ["anything"]}),
        _tool(
            "repairable_second_tool",
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["a", "ghost"],
            },
        ),
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = sanitize_tool_schemas(tools)

    assert _params(out, 0)["required"] == ["anything"], "古怪形态保持原样"
    assert _params(out, 1)["required"] == ["a"], "后续工具仍必须被净化"

    logs = _issues(caplog)
    assert len(logs) == 1
    assert "repairable_second_tool" in logs[0]
    assert not any(
        record.name == LOGGER_NAME and "preflight" in record.getMessage()
        for record in caplog.records
    ), "正常路径不得触发整体兜底"


def test_unexpected_internal_error_never_breaks_the_request(monkeypatch, caplog):
    """预检自身出错时必须原样放行：它绝不能反过来打断模型调用。

    上游 schema 是第三方数据、形状任意，若预检代码有未预料的缺陷而抛异常，
    异常会从 ``_format_tools`` 冒出，使整轮调用直接失败——那比原本的 400 更糟。
    因此预检必须自己兜住自己。
    """
    from app.services.ai.runtime.agentscope import tool_schema_sanitize as mod

    def _boom(*args, **kwargs):
        raise RuntimeError("preflight exploded")

    monkeypatch.setattr(mod, "_PREFLIGHT_FAILURE_REPORTED", False)
    monkeypatch.setattr(mod, "_sanitize_tool", _boom)

    tools = [
        _tool(
            "exploding_tool",
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        out = mod.sanitize_tool_schemas(tools)

    assert out is tools, "预检出问题时必须原样放行"
    assert any(
        record.name == LOGGER_NAME and "preflight" in record.getMessage()
        for record in caplog.records
    ), "必须留下一条可排查的日志"


def test_internal_error_is_not_logged_on_every_single_call(monkeypatch, caplog):
    """预检持续失败也不能每轮刷屏。"""
    from app.services.ai.runtime.agentscope import tool_schema_sanitize as mod

    def _boom(*args, **kwargs):
        raise RuntimeError("preflight exploded again")

    monkeypatch.setattr(mod, "_PREFLIGHT_FAILURE_REPORTED", False)
    monkeypatch.setattr(mod, "_sanitize_tool", _boom)
    tools = [_tool("exploding_tool_2", {"type": "object", "properties": {}})]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        for _ in range(3):
            mod.sanitize_tool_schemas(tools)

    failures = [
        record
        for record in caplog.records
        if record.name == LOGGER_NAME and "preflight" in record.getMessage()
    ]
    assert len(failures) == 1, "同一进程内只报一次，避免刷屏"


def test_self_referencing_schema_is_reported_and_does_not_hang(caplog):
    """自引用 schema 不得导致无限递归，且必须点名报出来。

    递归定义（树形参数）是受限解码后端的常见难点，也可能是上游拒绝的原因之一，
    因此这里不只是「不挂」，还要让它在日志里可见。
    """
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    params = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    params["properties"]["self"] = params

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        sanitize_tool_schemas([_tool("cyclic_tool", params)])

    logs = _issues(caplog)
    assert len(logs) == 1
    assert "cyclic_tool" in logs[0]
    assert "cyclic_schema" in logs[0]


# ── 日志不得刷屏 ────────────────────────────────────────────────────────────────

def test_repeated_identical_issue_is_reported_once(caplog):
    from app.services.ai.runtime.agentscope.tool_schema_sanitize import (
        sanitize_tool_schemas,
    )

    tools = [
        _tool(
            "dedup_tool",
            {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["a", "ghost"],
            },
        )
    ]

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        sanitize_tool_schemas(tools)
        sanitize_tool_schemas(tools)
        sanitize_tool_schemas(tools)

    assert len(_issues(caplog)) == 1, "同一问题在同一进程内只报一次"


# ── 接进真实发送链路 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_format_tools_sanitizes_before_the_request_goes_out(monkeypatch, caplog):
    """净化必须发生在 AgentScope 格式化之后、真正发请求之前。"""
    import openai

    from app.services.ai.runtime.agentscope.models import (
        AgentScopeModelConfig,
        create_openai_chat_model,
    )

    class FakeCompletions:
        async def create(self, **kwargs):
            return None

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, "AsyncClient", lambda **kwargs: FakeClient())

    model = create_openai_chat_model(
        AgentScopeModelConfig(
            api_key="sk-test",
            base_url="https://llm.example.com/v1",
            model="deepseek-v4.1-flash",
            streaming=False,
        )
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        fmt_tools, _ = model._format_tools(
            [
                _tool(
                    "pipeline_tool",
                    {
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                        "required": ["a", "ghost"],
                    },
                )
            ],
            None,
        )

    assert _params(fmt_tools)["required"] == ["a"]
    assert "pipeline_tool" in _issues(caplog)[0]
