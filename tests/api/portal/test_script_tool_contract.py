"""脚本工具契约：schema 校验、内置/自定义合并、注册表解析。

不连库：DB 行用 ``SimpleNamespace`` 替身，registry 的会话也用替身 session。
真正落库的行为由迁移与集成测试覆盖，这里盯的是「纯逻辑层面的约定」。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.script_tool import (
    SCRIPT_TOOL_MAX_CODE_BYTES,
    SysScriptToolCreate,
    SysScriptToolPreviewRequest,
    SysScriptToolResponse,
    SysScriptToolUpdate,
)
from app.services.ai.tools.script_tool_builtins import BUILTIN_SCRIPT_TOOLS
from app.services.ai.tools.script_tool_factory import (
    build_args_schema,
    config_from_builtin,
    config_from_row,
    merge_effective_script_tools,
    requirements_of,
)
from app.services.ai.tools.script_tool_runtime import ScriptToolConfig


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def test_name_pattern_is_enforced():
    SysScriptToolCreate(name="file_reader", code="print(1)")
    for bad in ["File_reader", "1abc", "ab", "has-dash", "含中文"]:
        with pytest.raises(ValidationError):
            SysScriptToolCreate(name=bad, code="print(1)")


def test_language_only_python():
    assert SysScriptToolCreate(name="a_b_c", code="x", language="PYTHON").language == "python"
    with pytest.raises(ValidationError):
        SysScriptToolCreate(name="a_b_c", code="x", language="node")


async def test_custom_name_cannot_shadow_builtin_when_seed_row_missing():
    """迁移漏跑时库里没有内置行，也不能让自定义工具占用内置名。"""
    from fastapi import HTTPException

    from app.api.portal.endpoints.script_tools import _assert_name_available

    with pytest.raises(HTTPException) as exc:
        await _assert_name_available(SimpleNamespace(), "file_digest")
    assert exc.value.status_code == 400
    assert "内置脚本工具" in exc.value.detail


def test_code_size_limit():
    SysScriptToolCreate(name="a_b_c", code="x" * SCRIPT_TOOL_MAX_CODE_BYTES)
    with pytest.raises(ValidationError):
        SysScriptToolCreate(name="a_b_c", code="x" * (SCRIPT_TOOL_MAX_CODE_BYTES + 1))


def test_code_is_required_on_create():
    with pytest.raises(ValidationError):
        SysScriptToolCreate(name="a_b_c", code="")


def test_requirements_normalized_from_json_and_text():
    assert SysScriptToolCreate(
        name="a_b_c", code="x", requirements='["pypdf>=4.0", " pypdf>=4.0 ", ""]'
    ).requirements == ["pypdf>=4.0"]
    assert SysScriptToolCreate(
        name="a_b_c", code="x", requirements="pypdf>=4.0\nopenpyxl，python-docx"
    ).requirements == ["pypdf>=4.0", "openpyxl", "python-docx"]


def test_timeout_bounds():
    with pytest.raises(ValidationError):
        SysScriptToolCreate(name="a_b_c", code="x", timeout_seconds=0)
    with pytest.raises(ValidationError):
        SysScriptToolCreate(name="a_b_c", code="x", timeout_seconds=3601)


def test_update_allows_partial_and_keeps_none():
    """更新模型必须能区分「没传」和「传了 null」：否则改一个字段会清空其它字段。"""
    payload = SysScriptToolUpdate(is_active=False)
    assert payload.is_active is False
    assert payload.code is None
    assert payload.name is None


def test_response_schema_parses_stored_json_strings():
    response = SysScriptToolResponse(
        id="x",
        name="a_b_c",
        parameter_schema='{"type":"object","properties":{}}',
        requirements='["pypdf>=4.0"]',
        created_at=None,
        updated_at=None,
    )
    assert response.parameter_schema == {"type": "object", "properties": {}}
    assert response.requirements == ["pypdf>=4.0"]


def test_preview_request_defaults():
    payload = SysScriptToolPreviewRequest()
    assert payload.params == {}
    assert payload.tool_id is None


def test_requirements_of_handles_garbage():
    assert requirements_of(None) == ()
    assert requirements_of("[]") == ()
    assert requirements_of("not json") == ("not json",)
    assert requirements_of([1, "", "x"]) == ("1", "x")


# --------------------------------------------------------------------------- #
# DB 行 → 执行配置
# --------------------------------------------------------------------------- #
def _row(**overrides) -> SimpleNamespace:
    base = {
        "id": "row-1",
        "name": "my_tool",
        "display_name": "我的工具",
        "description": "读文件",
        "source": "custom",
        "language": "python",
        "code": "print(1)",
        "parameter_schema": {"type": "object", "properties": {}},
        "requirements": '["pypdf>=4.0"]',
        "timeout_seconds": 30,
        "group_name": "文档",
        "is_active": True,
        "created_at": None,
        "updated_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_config_from_custom_row_uses_db_code():
    config = config_from_row(_row())
    assert config.code == "print(1)"
    assert config.requirements == ("pypdf>=4.0",)
    assert config.timeout_seconds == 30


def test_config_from_builtin_row_prefers_code_definition():
    """内置行的 code 恒为 NULL：源码唯一来源是代码库，DB 里的旧代码不得生效。"""
    builtin = BUILTIN_SCRIPT_TOOLS[0]
    config = config_from_row(_row(name=builtin.name, source="builtin", code=None), builtin=builtin)
    assert config.code == builtin.code
    assert builtin.code.strip()


def test_args_schema_honours_empty_required_list():
    """``required: []`` 是合法的「全部可选」，不能退回默认必填。"""
    config = ScriptToolConfig(
        name="all_optional",
        code="print(1)",
        parameter_schema={
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": [],
        },
    )
    args_schema = build_args_schema(config)
    assert args_schema.model_fields["note"].is_required() is False


def test_config_rejects_row_without_code():
    from app.services.ai.tools.script_tool_runtime import ScriptToolError

    with pytest.raises(ScriptToolError):
        config_from_row(_row(code=None))


def test_config_from_builtin_matches_definition():
    builtin = BUILTIN_SCRIPT_TOOLS[0]
    config = config_from_builtin(builtin)
    assert config.name == builtin.name
    assert config.parameter_schema == builtin.parameter_schema


def test_unoverridden_builtin_uses_code_metadata_not_stale_seed():
    """迁移 seed 是快照；未覆盖的内置行必须以代码定义为准，否则老环境看不到修复。"""
    builtin = next(item for item in BUILTIN_SCRIPT_TOOLS if item.name == "file_to_base64")
    row = _builtin_row(
        name=builtin.name,
        parameter_schema='{"type":"object","properties":{"stale":{"type":"string"}}}',
        timeout_seconds=7,
    )
    config = config_from_row(row, builtin=builtin)
    assert config.parameter_schema == builtin.parameter_schema
    assert config.timeout_seconds == builtin.timeout_seconds

    effective = next(
        item for item in merge_effective_script_tools([row]) if item.name == builtin.name
    )
    assert effective.parameter_schema == builtin.parameter_schema
    assert effective.timeout_seconds == builtin.timeout_seconds


# --------------------------------------------------------------------------- #
# 生效列表合并
# --------------------------------------------------------------------------- #
def test_merge_adds_builtins_missing_from_db():
    """迁移漏跑时内置工具仍要出现，否则代码上线了页面却看不到。"""
    merged = merge_effective_script_tools([])
    assert {item.name for item in merged} == {tool.name for tool in BUILTIN_SCRIPT_TOOLS}
    assert all(item.id.startswith("builtin-script-") for item in merged)


def test_db_row_wins_over_definition():
    builtin = BUILTIN_SCRIPT_TOOLS[0]
    merged = merge_effective_script_tools(
        [_row(id=builtin.tool_id, name=builtin.name, source="builtin", code=None,
              group_name="财务", is_active=False)]
    )
    found = {item.name: item for item in merged}[builtin.name]
    assert found.group_name == "财务"
    assert found.is_active is False
    assert found.code == builtin.code


def test_merge_keeps_custom_tools_and_sorts_builtin_first():
    merged = merge_effective_script_tools([_row(name="my_tool")])
    sources = [item.source for item in merged]
    assert sources == sorted(sources, key=lambda value: value != "builtin")
    assert "my_tool" in {item.name for item in merged}


def test_merge_parses_string_schema():
    merged = merge_effective_script_tools(
        [_row(parameter_schema=json.dumps({"type": "object", "properties": {"a": {"type": "string"}}}))]
    )
    found = {item.name: item for item in merged}["my_tool"]
    assert found.parameter_schema["properties"]["a"]["type"] == "string"


# --------------------------------------------------------------------------- #
# 注册表解析
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _FakeResult(self._rows.pop(0))


async def test_registry_serves_builtin_without_db_row():
    from app.services.ai.tools.registry import ToolRegistry

    tool = await ToolRegistry._load_script_tool(_FakeSession([None]), "file_digest")
    assert tool is not None
    assert tool.name == "file_digest"
    assert tool.runtime_metadata["script_tool_config"].code


async def test_registry_hides_disabled_row():
    from app.services.ai.tools.registry import ToolRegistry

    tool = await ToolRegistry._load_script_tool(
        _FakeSession([_row(name="file_digest", source="builtin", code=None, is_active=False)]),
        "file_digest",
    )
    assert tool is None


async def test_registry_returns_none_for_unknown_name():
    from app.services.ai.tools.registry import ToolRegistry

    assert await ToolRegistry._load_script_tool(_FakeSession([None]), "not_exists_tool") is None


async def test_unbound_script_tool_refuses_to_run():
    """没有工作区绑定时必须明确报错，而不是悄悄在宿主机上执行。"""
    from app.services.ai.tools.registry import ToolRegistry
    from app.services.ai.tools.script_tool_runtime import ScriptToolError

    tool = await ToolRegistry._load_script_tool(_FakeSession([None]), "file_digest")
    with pytest.raises(ScriptToolError):
        await tool.ainvoke({})


def test_script_source_type_is_registered():
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    spec = RuntimeToolSpec(
        name="x",
        description="d",
        parameters_schema={},
        callable=lambda **_kwargs: None,
        source_type="script_tool",
    )
    assert spec.metadata == {}


# --------------------------------------------------------------------------- #
# 可编辑内置：代码/参数能改，名字不能改，改坏了能恢复默认
# --------------------------------------------------------------------------- #
def _builtin_row(name: str = "file_digest", **overrides):
    row = SimpleNamespace(
        id="builtin-script-file-digest",
        name=name,
        display_name="文件摘要",
        description="算摘要",
        source="builtin",
        language="python",
        code=None,
        parameter_schema='{"type":"object","properties":{}}',
        requirements="[]",
        timeout_seconds=None,
        group_name=None,
        is_active=True,
        created_at=None,
        updated_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_update_builtin_allows_code_override():
    """用户选择「可编辑内置」：代码可以改，改完就是覆盖。"""
    from app.api.portal.endpoints.script_tools import ensure_editable

    row = _builtin_row()
    ensure_editable(row, SysScriptToolUpdate(code="print('patched')"))
    ensure_editable(row, SysScriptToolUpdate(description="改描述", requirements=["pypdf"]))


def test_update_builtin_rejects_rename():
    """改名会让「恢复默认」和内置兜底匹配失效，必须拒绝。"""
    from fastapi import HTTPException

    from app.api.portal.endpoints.script_tools import ensure_editable

    row = _builtin_row()
    with pytest.raises(HTTPException) as exc:
        ensure_editable(row, SysScriptToolUpdate(name="file_digest_v2"))
    assert exc.value.status_code == 403
    assert "不能改名" in exc.value.detail
    # 原样传回同名不算改名
    ensure_editable(row, SysScriptToolUpdate(name="file_digest"))


def test_custom_tool_has_no_name_restriction():
    from app.api.portal.endpoints.script_tools import ensure_editable

    ensure_editable(_builtin_row(source="custom"), SysScriptToolUpdate(name="whatever"))


def test_reset_clears_override_and_restores_builtin_defaults():
    from app.api.portal.endpoints.script_tools import apply_builtin_defaults

    builtin = next(item for item in BUILTIN_SCRIPT_TOOLS if item.name == "file_digest")
    row = _builtin_row(
        code="print('用户改坏的版本')",
        parameter_schema='{"type":"object","properties":{"x":{"type":"string"}}}',
        requirements='["pypdf"]',
        timeout_seconds=7,
        description="被改过的描述",
    )
    apply_builtin_defaults(row, builtin)

    assert row.code is None, "必须置空：运行时才会回落到代码库版本"
    assert json.loads(row.parameter_schema) == builtin.parameter_schema
    assert json.loads(row.requirements) == list(builtin.requirements)
    assert row.timeout_seconds == builtin.timeout_seconds
    assert row.description == builtin.description


def test_overridden_builtin_uses_db_code_at_runtime():
    """覆盖后运行时必须以库里的代码为准，否则「可编辑」就是假的。"""
    row = _builtin_row(code="print('mine')")
    config = config_from_row(row, builtin=next(i for i in BUILTIN_SCRIPT_TOOLS if i.name == "file_digest"))
    assert config.code == "print('mine')"


def test_unoverridden_builtin_falls_back_to_codebase_source():
    row = _builtin_row(code=None)
    builtin = next(i for i in BUILTIN_SCRIPT_TOOLS if i.name == "file_digest")
    config = config_from_row(row, builtin=builtin)
    assert config.code == builtin.code
    assert "hashlib" in config.code


def test_is_overridden_only_for_builtin_rows():
    def pick(rows, name="file_digest"):
        # 合并会把代码库里其他内置一并补齐，必须按名字取，不能按顺序取
        return next(item for item in merge_effective_script_tools(rows) if item.name == name)

    assert pick([_builtin_row(code="print('x')")]).is_overridden is True
    assert pick([_builtin_row(code=None)]).is_overridden is False

    custom = pick([_builtin_row(source="custom", name="my_tool", code="print('x')")], "my_tool")
    assert custom.is_overridden is False, "自定义工具没有「覆盖」概念"


# --------------------------------------------------------------------------- #
# 更新：显式 null 必须能清空（分组 / 超时 / 描述）
# --------------------------------------------------------------------------- #
async def test_update_can_clear_group_timeout_and_description():
    """前端清空时提交 null；后端若只看 `is not None`，用户会以为保存成功。"""
    from app.api.portal.endpoints.script_tools import apply_update_fields

    row = _builtin_row(source="custom", name="my_tool")
    row.group_name = "旧分组"
    row.timeout_seconds = 30
    row.description = "旧描述"

    await apply_update_fields(
        SimpleNamespace(),  # apply_update_fields 只在改名时才用 db
        row,
        SysScriptToolUpdate(group_name=None, timeout_seconds=None, description=None),
    )

    assert row.group_name is None
    assert row.timeout_seconds is None, "None = 跟随平台超时"
    assert row.description is None


async def test_update_ignores_omitted_fields():
    """没传的字段不能被 null 顺手清掉。"""
    from app.api.portal.endpoints.script_tools import apply_update_fields

    row = _builtin_row(source="custom", name="my_tool")
    row.group_name = "保留分组"
    row.timeout_seconds = 30

    await apply_update_fields(SimpleNamespace(), row, SysScriptToolUpdate(display_name="只改这个"))

    assert row.group_name == "保留分组"
    assert row.timeout_seconds == 30


async def test_update_blank_group_becomes_none():
    from app.api.portal.endpoints.script_tools import apply_update_fields

    row = _builtin_row(source="custom", name="my_tool")
    row.group_name = "旧分组"
    await apply_update_fields(SimpleNamespace(), row, SysScriptToolUpdate(group_name="   "))
    assert row.group_name is None


async def test_builtin_group_only_update_does_not_mark_overridden():
    """编辑内置的分组/描述时前端会带上完整 code；内容没变就不能落库。"""
    from app.api.portal.endpoints.script_tools import apply_update_fields

    builtin = next(item for item in BUILTIN_SCRIPT_TOOLS if item.name == "file_digest")
    row = _builtin_row()
    assert row.code is None

    await apply_update_fields(
        SimpleNamespace(),
        row,
        SysScriptToolUpdate(group_name="新分组", description="改描述", code=builtin.code),
    )

    assert row.group_name == "新分组"
    assert row.code is None, "code 与代码库一致时写入会误标「已覆盖」"


async def test_builtin_changed_code_is_stored():
    from app.api.portal.endpoints.script_tools import apply_update_fields

    row = _builtin_row()
    await apply_update_fields(
        SimpleNamespace(),
        row,
        SysScriptToolUpdate(code="print('patched')"),
    )
    assert row.code == "print('patched')"


async def test_builtin_changed_schema_marks_overridden():
    """只改参数（不改代码）也要标记覆盖，否则 UI 说「已覆盖」但徽标不显示。"""
    from app.api.portal.endpoints.script_tools import apply_update_fields

    builtin = next(item for item in BUILTIN_SCRIPT_TOOLS if item.name == "file_digest")
    row = _builtin_row(parameter_schema=json.dumps(builtin.parameter_schema))
    await apply_update_fields(
        SimpleNamespace(),
        row,
        SysScriptToolUpdate(
            parameter_schema={"type": "object", "properties": {"custom": {"type": "string"}}}
        ),
    )
    assert row.code == builtin.code
    assert json.loads(row.parameter_schema)["properties"]["custom"]["type"] == "string"


async def test_builtin_same_schema_does_not_mark_overridden():
    from app.api.portal.endpoints.script_tools import apply_update_fields

    builtin = next(item for item in BUILTIN_SCRIPT_TOOLS if item.name == "file_digest")
    row = _builtin_row(parameter_schema=json.dumps(builtin.parameter_schema))
    await apply_update_fields(
        SimpleNamespace(),
        row,
        SysScriptToolUpdate(parameter_schema=builtin.parameter_schema, code=builtin.code),
    )
    assert row.code is None


async def test_builtin_stale_seed_edit_description_does_not_mark_overridden():
    """老环境 seed schema 与代码库不同；只改描述时前端会提交新 schema，不能误标覆盖。"""
    from app.api.portal.endpoints.script_tools import apply_update_fields

    builtin = next(item for item in BUILTIN_SCRIPT_TOOLS if item.name == "file_to_base64")
    row = _builtin_row(
        name=builtin.name,
        parameter_schema='{"type":"object","properties":{"stale":{"type":"string"}}}',
        timeout_seconds=7,
    )
    await apply_update_fields(
        SimpleNamespace(),
        row,
        SysScriptToolUpdate(
            description="新描述",
            parameter_schema=builtin.parameter_schema,
            requirements=list(builtin.requirements),
            timeout_seconds=builtin.timeout_seconds,
        ),
    )
    assert row.description == "新描述"
    assert row.code is None


async def test_builtin_changed_requirements_or_timeout_marks_overridden():
    from app.api.portal.endpoints.script_tools import apply_update_fields

    builtin = next(item for item in BUILTIN_SCRIPT_TOOLS if item.name == "file_digest")
    row = _builtin_row(requirements="[]")
    await apply_update_fields(
        SimpleNamespace(),
        row,
        SysScriptToolUpdate(requirements=["pypdf>=4.0"]),
    )
    assert row.code == builtin.code

    row = _builtin_row(timeout_seconds=None)
    await apply_update_fields(
        SimpleNamespace(),
        row,
        SysScriptToolUpdate(timeout_seconds=15),
    )
    assert row.code == builtin.code


def test_update_code_rejects_blank_and_oversize():
    with pytest.raises(ValidationError):
        SysScriptToolUpdate(code="   ")
    with pytest.raises(ValidationError):
        SysScriptToolUpdate(code="x" * (SCRIPT_TOOL_MAX_CODE_BYTES + 1))
    assert SysScriptToolUpdate(code=None).code is None


# --------------------------------------------------------------------------- #
# 失效引用判定：脚本工具不能被误判（否则用户会「移除失效引用」把配置删掉）
# --------------------------------------------------------------------------- #
async def test_script_tool_is_not_reported_as_unresolved():
    from app.services.mcp.tool_ref_health import resolve_unresolved_tool_names

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakeDB:
        """只回答 sys_script_tools 的查询，其它查询返回空。"""

        async def execute(self, stmt):
            return _FakeResult([])

    # file_digest 是内置脚本：库里没有行也必须算「可解析」
    unresolved = await resolve_unresolved_tool_names(
        _FakeDB(), {"file_digest"}, user_id=1
    )
    assert unresolved == set(), "内置脚本被误判成失效会让用户删掉有效配置"


async def test_disabled_script_tool_is_reported_as_unresolved():
    from app.services.mcp.tool_ref_health import _active_script_tool_names

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakeDB:
        async def execute(self, stmt):
            return _FakeResult([("file_digest", False)])

    assert await _active_script_tool_names(_FakeDB(), {"file_digest"}) == set()


async def test_missing_script_table_rolls_back_transaction():
    """PostgreSQL 下查询失败后事务会 aborted，必须 rollback 才能继续后续查询。"""
    from app.services.mcp.tool_ref_health import _active_script_tool_names

    class _FakeDB:
        rolled_back = False

        async def execute(self, stmt):
            raise RuntimeError('relation "sys_script_tools" does not exist')

        async def rollback(self):
            self.rolled_back = True

    db = _FakeDB()
    assert await _active_script_tool_names(db, {"file_digest"}) == {"file_digest"}
    assert db.rolled_back is True


# --------------------------------------------------------------------------- #
# 沙箱不可用时的降级：绝不能在平台后端进程里跑用户脚本
# --------------------------------------------------------------------------- #
def test_script_binding_refuses_when_sandbox_degraded():
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
    from app.services.ai.runtime.agentscope.workspace import _bind_script_tool_spec
    from app.services.ai.tools.script_tool_runtime import ScriptToolConfig

    config = ScriptToolConfig(name="demo_tool", code="print('hi')", parameter_schema={})
    async def _noop(**kwargs):
        return "unused"

    spec = RuntimeToolSpec(
        name="demo_tool",
        description="demo",
        parameters_schema={"type": "object", "properties": {}},
        source_type="script_tool",
        callable=_noop,
        metadata={"script_tool_config": config},
    )
    local_ws = SimpleNamespace(workdir="/tmp/whatever", conversation_id="c1")

    with pytest.raises(PermissionError) as exc:
        _bind_script_tool_spec(
            spec,
            sandbox_ws=None,
            local_ws=local_ws,
            user_info=None,
            sandbox_degraded=True,
        )
    assert "不会退回平台后端进程" in str(exc.value)


async def test_binding_drops_script_tools_when_degraded():
    """runner 在降级分支里把脚本 spec 一并摘掉，模型连调用的机会都没有。"""
    from pathlib import Path as _Path

    source = _Path("app/services/ai/runners/assistant_agent_runner.py").read_text(encoding="utf-8")
    degrade_branch = source[source.index("移除沙箱 Bash 工具"):]
    degrade_branch = degrade_branch[: degrade_branch.index("bind_configured_tools_to_workspace")]
    assert '!= "script_tool"' in degrade_branch, "降级分支必须同时移除脚本工具"


# --------------------------------------------------------------------------- #
# 兜底标记：库里有的内置行绝不能被标成「迁移未执行」
# --------------------------------------------------------------------------- #
def test_existing_builtin_row_is_not_marked_fallback():
    """内置行落在库里时 id 就叫 builtin-script-*，按 id 前缀猜会把正常行判成兜底。

    后果不只是提示错：前端会据此禁用「添加脚本工具」和启停开关。
    """
    from app.api.portal.endpoints.script_tools import _response_from_effective

    row = _builtin_row(name="file_digest", id="builtin-script-file-digest")
    item = next(
        i for i in merge_effective_script_tools([row]) if i.name == "file_digest"
    )
    response = _response_from_effective(item, {row.name})
    assert response.is_fallback is False
    assert response.id.startswith("builtin-script-"), "id 前缀本来就长这样，不能当判据"


def test_missing_row_is_marked_fallback():
    from app.api.portal.endpoints.script_tools import _response_from_effective

    item = next(
        i for i in merge_effective_script_tools([]) if i.name == "file_digest"
    )
    response = _response_from_effective(item, set())
    assert response.is_fallback is True
