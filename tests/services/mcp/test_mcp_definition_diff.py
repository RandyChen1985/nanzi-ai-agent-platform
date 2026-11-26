"""工具定义指纹与差异计算的回归。

这一层是整套「自动探测」的判断依据，出错的方式很具体：

- 指纹太敏感 → 每天都报假变更，几天后没人再看这个待办；
- 指纹太粗 → 远端真改了参数也没人知道，正是这个功能要解决的问题；
- 差异分级错 → 把「加了可选参数」和「可选改必填」当成同一件事，处置优先级就废了。

所以这里断言的都是「哪种变化算哪种级别」，而不是实现细节。
"""
from __future__ import annotations

import json

import pytest

from app.services.mcp.definition_diff import (
    definition_fingerprint,
    diff_parameter_schemas,
    diff_tool_definition,
    strip_platform_schema_extensions,
    summarize_change,
    tool_annotations,
    tool_input_schema,
)

BASE_SCHEMA = {
    "type": "object",
    "properties": {
        "customer_id": {"type": "string", "description": "客户 ID"},
        "page": {"type": "integer", "default": 1},
    },
    "required": ["customer_id"],
}


# --------------------------------------------------------------------------- #
# 1. 指纹：稳定，但不迟钝
# --------------------------------------------------------------------------- #
def test_fingerprint_ignores_key_order_and_whitespace():
    """键顺序与空白不同必须是同一个指纹，否则每次远端重启都会报一遍变更。"""
    reordered = {
        "required": ["customer_id"],
        "properties": {
            "page": {"default": 1, "type": "integer"},
            "customer_id": {"description": "客户 ID", "type": "string"},
        },
        "type": "object",
    }
    assert definition_fingerprint("查询客户", BASE_SCHEMA) == definition_fingerprint(
        "查询客户", reordered
    )
    # 描述尾部空白同样不算变化
    assert definition_fingerprint("查询客户", BASE_SCHEMA) == definition_fingerprint(
        "查询客户   ", BASE_SCHEMA
    )


def test_fingerprint_ignores_platform_extensions():
    """平台自己写的 x-nanzi-* 不能参与比对：那是本地策略，不是远端定义。"""
    with_extension = json.loads(json.dumps(BASE_SCHEMA))
    with_extension["properties"]["customer_id"]["x-nanzi-evidence"] = "order_id"
    assert definition_fingerprint("查询客户", BASE_SCHEMA) == definition_fingerprint(
        "查询客户", with_extension
    )


def test_fingerprint_detects_description_schema_and_annotations():
    base = definition_fingerprint("查询客户", BASE_SCHEMA)
    assert base != definition_fingerprint("查询客户列表", BASE_SCHEMA)
    changed_schema = json.loads(json.dumps(BASE_SCHEMA))
    changed_schema["properties"]["page"]["default"] = 20
    assert base != definition_fingerprint("查询客户", changed_schema)
    assert base != definition_fingerprint("查询客户", BASE_SCHEMA, {"readOnlyHint": True})


def test_fingerprint_accepts_serialized_schema():
    """库里存的是 JSON 文本，远端给的是对象，两边必须算出同一个值。"""
    assert definition_fingerprint("查询客户", BASE_SCHEMA) == definition_fingerprint(
        "查询客户", json.dumps(BASE_SCHEMA)
    )


def test_empty_annotations_equals_missing_annotations():
    """远端有时返回 ``{}`` 有时返回 ``None``，这是同一个事实。"""
    assert definition_fingerprint("x", BASE_SCHEMA, None) == definition_fingerprint(
        "x", BASE_SCHEMA, {}
    )


# --------------------------------------------------------------------------- #
# 2. 参数级差异分级
# --------------------------------------------------------------------------- #
def _kinds(changes):
    return [(c["kind"], c["severity"]) for c in changes]


def test_added_optional_param_is_safe():
    after = json.loads(json.dumps(BASE_SCHEMA))
    after["properties"]["status"] = {"type": "string"}
    changes = diff_parameter_schemas(BASE_SCHEMA, after)
    assert _kinds(changes) == [("param_added_optional", "safe")]
    assert changes[0]["summary"] == "新增可选参数 status"


def test_added_required_param_is_breaking():
    """新增必填参数会让所有已绑定的智能体下次调用直接失败，绝不能报成 safe。"""
    after = json.loads(json.dumps(BASE_SCHEMA))
    after["properties"]["contract_no"] = {"type": "string"}
    after["required"].append("contract_no")
    changes = diff_parameter_schemas(BASE_SCHEMA, after)
    assert _kinds(changes) == [("param_required", "breaking")]
    assert changes[0]["summary"] == "新增必填参数 contract_no"


def test_param_becoming_required_is_breaking_and_reverse_is_notice():
    after = json.loads(json.dumps(BASE_SCHEMA))
    after["required"].append("page")
    became_required = diff_parameter_schemas(BASE_SCHEMA, after)
    assert _kinds(became_required) == [("param_required", "breaking")]
    assert became_required[0]["summary"] == "参数 page 从可选变为必填"

    relaxed = diff_parameter_schemas(after, BASE_SCHEMA)
    assert _kinds(relaxed) == [("param_required", "notice")]
    assert relaxed[0]["summary"] == "参数 page 从必填变为可选"


def test_removed_param_is_breaking():
    changes = diff_parameter_schemas(BASE_SCHEMA, {"type": "object", "properties": {"page": {"type": "integer", "default": 1}}})
    assert ("param_removed", "breaking") in _kinds(changes)


def test_type_change_is_breaking():
    after = json.loads(json.dumps(BASE_SCHEMA))
    after["properties"]["page"]["type"] = "string"
    changes = diff_parameter_schemas(BASE_SCHEMA, after)
    assert _kinds(changes) == [("param_type", "breaking")]
    assert "integer → string" in changes[0]["summary"]


def test_enum_value_added_is_safe_but_removed_is_breaking():
    before = json.loads(json.dumps(BASE_SCHEMA))
    before["properties"]["status"] = {"type": "string", "enum": ["draft", "signed"]}
    added = json.loads(json.dumps(before))
    added["properties"]["status"]["enum"] = ["draft", "signed", "void"]
    assert _kinds(diff_parameter_schemas(before, added)) == [("param_enum", "safe")]

    removed = json.loads(json.dumps(before))
    removed["properties"]["status"]["enum"] = ["draft"]
    severity = diff_parameter_schemas(before, removed)[0]
    assert (severity["kind"], severity["severity"]) == ("param_enum", "breaking")
    assert "移除 signed" in severity["summary"]


def test_one_param_reports_only_the_most_severe_change():
    """同一参数同时改类型和必填，只报一条——否则列表里看着像三个问题。"""
    after = json.loads(json.dumps(BASE_SCHEMA))
    after["properties"]["page"]["type"] = "string"
    after["required"].append("page")
    changes = diff_parameter_schemas(BASE_SCHEMA, after)
    assert len(changes) == 1
    assert changes[0]["kind"] == "param_type"
    assert changes[0]["severity"] == "breaking"


def test_param_description_change_does_not_outrank_type_change():
    after = json.loads(json.dumps(BASE_SCHEMA))
    after["properties"]["page"]["type"] = "string"
    after["properties"]["page"]["description"] = "页码"
    changes = diff_parameter_schemas(BASE_SCHEMA, after)
    assert _kinds(changes) == [("param_type", "breaking")]


def test_platform_extension_change_is_not_a_param_change():
    after = json.loads(json.dumps(BASE_SCHEMA))
    after["properties"]["customer_id"]["x-nanzi-evidence"] = "order_id"
    assert diff_parameter_schemas(BASE_SCHEMA, after) == []


# --------------------------------------------------------------------------- #
# 3. 工具级差异
# --------------------------------------------------------------------------- #
def test_description_change_is_safe_and_carries_remote_hash():
    changes = diff_tool_definition(
        tool_name="crm:query_customer",
        before_description="查询客户",
        before_schema=BASE_SCHEMA,
        after_description="查询客户，支持手机号",
        after_schema=BASE_SCHEMA,
    )
    assert [(c["kind"], c["severity"]) for c in changes] == [("description_changed", "safe")]
    # 每条变更都带远端指纹，「忽略」时靠它把已确认版本推进，否则同一差异天天报。
    assert len(changes[0]["definition_hash"]) == 64
    assert changes[0]["tool_name"] == "crm:query_customer"


def test_annotations_change_is_notice():
    changes = diff_tool_definition(
        tool_name="crm:query_customer",
        before_description="查询客户",
        before_schema=BASE_SCHEMA,
        after_description="查询客户",
        after_schema=BASE_SCHEMA,
        after_annotations={"destructiveHint": True},
    )
    assert [(c["kind"], c["severity"]) for c in changes] == [("annotations_changed", "notice")]


def test_identical_definition_produces_no_change():
    assert (
        diff_tool_definition(
            tool_name="crm:query_customer",
            before_description="查询客户",
            before_schema=BASE_SCHEMA,
            after_description="查询客户",
            after_schema=BASE_SCHEMA,
        )
        == []
    )


# --------------------------------------------------------------------------- #
# 4. 远端对象解析的兼容性
# --------------------------------------------------------------------------- #
def test_tool_input_schema_supports_both_field_names():
    assert tool_input_schema({"input_schema": {"type": "object"}}) == {"type": "object"}
    assert tool_input_schema({"inputSchema": {"type": "object"}}) == {"type": "object"}
    assert tool_input_schema({"parameter_schema": {"type": "object"}}) == {"type": "object"}


def test_tool_annotations_accepts_pydantic_like_object():
    class _Ann:
        def model_dump(self, mode="json"):
            return {"readOnlyHint": True}

    class _Tool:
        annotations = _Ann()

    assert tool_annotations(_Tool()) == {"readOnlyHint": True}


def test_strip_is_recursive():
    raw = {"a": {"x-nanzi-evidence": "x", "b": [{"X-NANZI-k": 1, "keep": 2}]}}
    assert strip_platform_schema_extensions(raw) == {"a": {"b": [{"keep": 2}]}}


# --------------------------------------------------------------------------- #
# 5. 摘要还原（列表与通知复用同一套文案）
# --------------------------------------------------------------------------- #
def test_summarize_change_reads_backend_payloads():
    assert summarize_change("description_changed", '{"description": "a"}', '{"description": "b"}') == "工具描述变更"
    assert summarize_change("tool_removed", '{"description": "a"}', None) == "远端已删除该工具"
    assert (
        summarize_change(
            "param_required",
            json.dumps({"param": "contract_no", "required": False}),
            json.dumps({"param": "contract_no", "required": True}),
        )
        == "参数 contract_no 从可选变为必填"
    )
    assert summarize_change("description_changed", None, None) == "工具描述变更"


@pytest.mark.parametrize(
    "kind",
    [
        "description_changed",
        "param_description",
        "param_added_optional",
        "param_enum",
        "param_required",
        "param_removed",
        "param_type",
        "tool_added",
        "tool_removed",
        "annotations_changed",
    ],
)
def test_every_kind_has_a_summary(kind):
    """新增 kind 却忘了文案时，列表会显示空白摘要——这条断言挡住那种情况。"""
    assert summarize_change(kind, None, None).strip()


def test_enum_introduced_from_nothing_is_breaking():
    """原来不限取值、现在给出白名单：老调用传任何别的值都会被拒，属收紧。

    只看「有没有移除枚举值」的话，before 是空集 → removed 为空 → 被判成 safe，
    而这种改动恰恰是所有老调用都会中招的那一类。
    """
    before = {"type": "object", "properties": {"status": {"type": "string"}}}
    after = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["a", "b"]}},
    }
    changes = diff_parameter_schemas(before, after)
    assert _kinds(changes) == [("param_enum", "breaking")]


def test_new_required_param_summary_survives_round_trip():
    """列表页用 summarize_change 从落库片段重算摘要，不能把「新增必填」说成「变必填」。"""
    after = json.loads(json.dumps(BASE_SCHEMA))
    after["properties"]["contract_no"] = {"type": "string"}
    after["required"].append("contract_no")
    change = diff_parameter_schemas(BASE_SCHEMA, after)[0]
    assert change["summary"] == "新增必填参数 contract_no"

    # 落库再读出来的那条路（before 为 None）
    before_text, after_text = None, json.dumps({**change["after"], "param": "contract_no"})
    assert summarize_change("param_required", before_text, after_text) == "新增必填参数 contract_no"


def test_constraint_change_is_not_reported_as_a_type_change():
    """只改了 default/minimum：不能显示「类型 integer → integer」，也不该算破坏性。"""
    before = {
        "type": "object",
        "properties": {"page": {"type": "integer", "default": 1}},
    }
    after = {
        "type": "object",
        "properties": {"page": {"type": "integer", "default": 20}},
    }
    changes = diff_parameter_schemas(before, after)
    assert _kinds(changes) == [("param_type", "notice")]
    assert "类型 integer → integer" not in changes[0]["summary"]
    assert "默认值" in changes[0]["summary"]


def test_optional_type_change_through_anyof_is_detected():
    """Pydantic v2 的 Optional[str] 走 anyOf、``type`` 是 None：只比 type 会整个漏掉。"""
    before = {
        "type": "object",
        "properties": {"note": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
    }
    after = {
        "type": "object",
        "properties": {"note": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
    }
    changes = diff_parameter_schemas(before, after)
    assert [c["kind"] for c in changes] == ["param_type"]
    assert changes[0]["severity"] == "breaking"


def test_enum_removed_entirely_is_a_relaxation():
    """「有白名单 → 取消白名单」是放宽（任何旧取值都还能用），不能标成 breaking。

    与「无枚举 → 有枚举」互为镜像；只判「有没有移除取值」会把这一半标反。
    """
    before = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["a", "b"]}},
    }
    after = {"type": "object", "properties": {"status": {"type": "string"}}}
    changes = diff_parameter_schemas(before, after)
    assert [c["kind"] for c in changes] == ["param_enum"]
    assert changes[0]["severity"] == "safe"
