"""探测编排里「算差异」那一段的回归（纯逻辑，不碰 DB 与网络）。

DB 写入与远端调用由集成测试覆盖；这里盯的是几条一旦搞错就会安静出事的规则：

- 首次探测必须只建立基线，不能把「库里的指纹为空」当成「变了」；
- 远端 0 工具不能推导出「工具全被删了」（保护在 ``probe_server`` 里，这里验
  ``_compute_changes`` 在空远端输入下的行为不额外产生删除之外的副作用）；
- 已经标记下线的工具不重复报；
- 落库的 before/after 必须把远端指纹一起带上，否则「忽略」之后同一差异会天天重报。
"""
from __future__ import annotations

import json

from app.services.mcp.definition_diff import definition_fingerprint
from app.services.mcp.definition_probe import (
    HASH_KEY,
    _change_payload,
    _compute_changes,
    _row_definition_hash,
    _schema_annotations,
)

SCHEMA = {
    "type": "object",
    "properties": {"customer_id": {"type": "string"}},
    "required": ["customer_id"],
}
DESC = "查询客户信息"


def _remote(description=DESC, schema=None, annotations=None, name="crm:query_customer"):
    schema = schema if schema is not None else SCHEMA
    return {
        name: {
            "description": description,
            "schema": schema,
            "annotations": annotations,
            "definition_hash": definition_fingerprint(description, schema, annotations),
        }
    }


def _cached(description=DESC, schema=None, hash_value="present", available=True):
    schema = schema if schema is not None else SCHEMA
    return [
        {
            "tool_name": "crm:query_customer",
            "tool_description": description,
            "parameter_schema": json.dumps(schema),
            "is_available": available,
            "definition_hash": (
                # 与 sync_tools 同一口径：annotations 存在 schema 的扩展键里，算指纹时
                # 要单独取出来传进去（指纹会剥掉扩展键，不能指望它顺便带上 annotations）。
                definition_fingerprint(description, schema, _schema_annotations(schema))
                if hash_value == "present"
                else hash_value
            ),
        }
    ]


def test_unchanged_definition_yields_nothing():
    changes, baseline = _compute_changes("crm", _cached(), _remote())
    assert changes == []
    assert baseline == 0


def test_first_probe_only_establishes_baseline():
    """历史数据没有指纹：第一次探测必须当成「建立基线」，否则上线当天全量误报。"""
    changes, baseline = _compute_changes("crm", _cached(hash_value=None), _remote())
    assert changes == []
    assert baseline == 1


def test_description_change_is_reported_with_hash():
    changes, _ = _compute_changes("crm", _cached(), _remote(description="查询客户，支持手机号"))
    assert [c["kind"] for c in changes] == ["description_changed"]
    assert changes[0]["severity"] == "safe"
    assert len(changes[0]["definition_hash"]) == 64


def test_new_remote_tool_is_notice():
    remote = _remote()
    remote.update(_remote(name="crm:create_followup", description="创建跟进"))
    changes, _ = _compute_changes("crm", _cached(), remote)
    added = [c for c in changes if c["kind"] == "tool_added"]
    assert len(added) == 1
    assert added[0]["severity"] == "notice"
    assert added[0]["tool_name"] == "crm:create_followup"


def test_removed_remote_tool_is_breaking():
    changes, _ = _compute_changes("crm", _cached(), {})
    assert [(c["kind"], c["severity"]) for c in changes] == [("tool_removed", "breaking")]


def test_already_unavailable_tool_is_not_reported_again():
    """上次已经报过下线，这次别再报——不然每天一条同样的「工具已删除」。"""
    changes, _ = _compute_changes("crm", _cached(available=False), {})
    assert changes == []


def test_fingerprint_mismatch_without_granular_diff_reports_nothing():
    """指纹不同但拆不出具体差异时，不制造无法解读的待办。"""
    schema_with_noise = json.loads(json.dumps(SCHEMA))
    schema_with_noise["title"] = "CustomerQuery"
    changes, _ = _compute_changes("crm", _cached(), _remote(schema=schema_with_noise))
    assert changes == []


def test_annotations_change_from_cached_schema_is_detected():
    """库里的 annotations 藏在 schema 的 x-nanzi-mcp-annotations 里，要能取出来比对。"""
    schema_with_ann = json.loads(json.dumps(SCHEMA))
    schema_with_ann["x-nanzi-mcp-annotations"] = {"readOnlyHint": True}
    cached = _cached(schema=schema_with_ann)
    changes, _ = _compute_changes("crm", cached, _remote())
    assert [c["kind"] for c in changes] == ["annotations_changed"]


# --------------------------------------------------------------------------- #
# 落库载荷
# --------------------------------------------------------------------------- #
def test_payload_keeps_remote_hash_for_description_change():
    before_text, after_text = _change_payload(
        {
            "kind": "description_changed",
            "before": {"description": "a"},
            "after": {"description": "b"},
            "definition_hash": "h" * 64,
        }
    )
    assert json.loads(after_text)[HASH_KEY] == "h" * 64
    assert json.loads(before_text) == {"description": "a"}


def test_payload_for_removed_tool_puts_hash_after():
    before_text, after_text = _change_payload(
        {
            "kind": "tool_removed",
            "before": {"description": "a", HASH_KEY: "old"},
            "after": None,
            "definition_hash": "n" * 64,
        }
    )
    assert json.loads(before_text)["description"] == "a"
    assert json.loads(after_text)[HASH_KEY] == "n" * 64


def test_payload_marks_param_name():
    before_text, after_text = _change_payload(
        {
            "kind": "param_required",
            "param": "contract_no",
            "before": {"required": False},
            "after": {"required": True},
            "definition_hash": "p" * 64,
        }
    )
    assert json.loads(before_text)["param"] == "contract_no"
    assert json.loads(after_text)["param"] == "contract_no"
    assert json.loads(after_text)[HASH_KEY] == "p" * 64


def test_row_hash_reads_after_then_before():
    class _Row:
        after_value = json.dumps({HASH_KEY: "after-hash"})
        before_value = json.dumps({HASH_KEY: "before-hash"})

    assert _row_definition_hash(_Row()) == "after-hash"

    class _OnlyBefore:
        after_value = None
        before_value = json.dumps({HASH_KEY: "before-hash"})

    assert _row_definition_hash(_OnlyBefore()) == "before-hash"

    class _Broken:
        after_value = "not-json"
        before_value = None

    assert _row_definition_hash(_Broken()) is None


def test_schema_annotations_extraction():
    assert _schema_annotations(json.dumps({"x-nanzi-mcp-annotations": {"a": 1}})) == {"a": 1}
    assert _schema_annotations(json.dumps({"type": "object"})) is None
    assert _schema_annotations("not-json") is None
    assert _schema_annotations(None) is None


async def test_probe_servers_times_out_instead_of_hanging(monkeypatch):
    """单个服务挂起必须被超时掐断，否则夜间巡检永远不结束、之后每天都被跳过。

    夜间 job 是 ``max_instances=1``：只要有一趟永不返回，APScheduler 后续每次触发
    都会因为「上一次还挂着」被直接跳过——探测功能等于停摆到重启，而且日志里只留一句
    max instances 警告，很难和「某个 MCP 服务发疯了」联系起来。
    """
    import asyncio

    import app.services.mcp.definition_probe as probe_module

    async def _never_returns(*args, **kwargs):  # noqa: ANN002, ANN003
        await asyncio.sleep(30)
        return {}

    recorded: list = []

    async def _record(server_id, *, status, error=None, count_failure=False):  # noqa: ANN001
        recorded.append((server_id, status, count_failure))

    monkeypatch.setattr(probe_module, "probe_server", _never_returns)
    monkeypatch.setattr(probe_module, "PROBE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(probe_module, "_record_probe_state", _record)

    results = await probe_module.probe_servers(["slow-server"])

    assert len(results) == 1
    assert results[0]["status"] == probe_module.PROBE_UNREACHABLE
    assert "超时" in (results[0]["error"] or "")
    # 超时要计入连续失败，这样界面上能看出「这个服务一直连不上」
    assert recorded == [("slow-server", probe_module.PROBE_UNREACHABLE, True)]


def test_dismissed_keys_skip_added_and_removed():
    """已忽略的 tool_added / tool_removed 不再进入变更列表（其余类型不受影响）。"""
    cached = [
        {
            "tool_name": "crm:gone",
            "tool_description": "旧工具",
            "parameter_schema": json.dumps({"type": "object"}),
            "is_available": True,
            "definition_hash": definition_fingerprint("旧工具", {"type": "object"}),
        }
    ]
    remote = {
        "crm:new_one": {
            "description": "新工具",
            "schema": {"type": "object"},
            "annotations": None,
            "definition_hash": definition_fingerprint("新工具", {"type": "object"}),
        }
    }

    changes, _ = _compute_changes("crm", cached, remote)
    assert {change["kind"] for change in changes} == {"tool_added", "tool_removed"}

    changes, _ = _compute_changes(
        "crm",
        cached,
        remote,
        {("crm:new_one", "tool_added"), ("crm:gone", "tool_removed")},
    )
    assert changes == []


def test_oversized_payload_is_still_valid_json():
    """超长定义落库前必须裁剪，但**绝不能写出半截 JSON**。

    ``_row_param`` / ``_row_definition_hash`` / ``summarize_change`` 全靠 json.loads
    读这两列：解析失败意味着「忽略」推不动指纹（每天重报）、参数去重键失配、摘要退化。
    而且 TEXT 上限是 65535 **字节**，中文一字 3 字节——按字符数裁剪根本兜不住。
    """
    huge_description = "很长的工具描述" * 20000
    huge_items = {
        "type": "object",
        "properties": {
            f"k{index}": {"type": "string", "description": "x" * 500}
            for index in range(500)
        },
    }
    change = {
        "kind": "param_description",
        "param": "page",
        "definition_hash": "a" * 64,
        "before": {"param": "page", "type": "integer", "description": huge_description, "items": huge_items},
        "after": {"param": "page", "type": "integer", "description": huge_description + "改", "items": huge_items},
    }
    before_text, after_text = _change_payload(change)

    for text in (before_text, after_text):
        assert text is not None
        assert len(text.encode("utf-8")) <= 50_000, "必须按字节受控，否则 MySQL TEXT 会 Data too long"
        payload = json.loads(text)  # 关键断言：必须是合法 JSON
        assert payload.get("param") == "page"

    # 指纹与参数名必须保住：前者是「忽略」推进版本的依据，后者是去重键的一部分
    class _Row:
        after_value = after_text
        before_value = before_text

    assert _row_definition_hash(_Row()) == "a" * 64
    assert json.loads(after_text).get("param") == "page"
