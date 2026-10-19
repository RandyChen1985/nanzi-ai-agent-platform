"""API 工具参数「用户变量占位符」契约（源码级）。

工具参数的默认值常需要按调用者取值，例如 ``{user.user_name}``。这条链路跨了
参数编辑器、保存接口、运行时请求内核与试跑端点，任何一处漏掉都会让界面看起来
能配、运行时却取不到值。这里锁定四个不变量：

1. 参数编辑器的默认值旁有用户变量菜单，且必填参数上不可用（默认值只在非必填时生效）；
2. 变量候选与元数据行级权限共用 ``utils/userVariables.ts``，不再各写一份；
3. 运行时在路径替换**之前**完成占位符替换，Headers 也替换；
4. 试跑端点把 ``UserPlaceholderError`` 映射成 ``missing_user_variable``，不被当成连接失败。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARAM_EDITOR = ROOT / "frontend/src/components/system/ToolParamEditor.vue"
METADATA_PAGE = ROOT / "frontend/src/views/MetadataDatasets.vue"
USER_VARIABLES = ROOT / "frontend/src/utils/userVariables.ts"
API_TOOL = ROOT / "frontend/src/api/tool.ts"
API_TOOL_TESTER = ROOT / "frontend/src/components/system/ApiToolTester.vue"
GENERIC_API = ROOT / "app/services/ai/tools/generic_api.py"
TOOLS_ENDPOINT = ROOT / "app/api/portal/endpoints/tools.py"


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


def test_param_editor_exposes_user_variable_menu():
    source = _read(PARAM_EDITOR)

    assert "PermissionVariableMenu" in source
    assert "BUILTIN_USER_VARIABLES" in source
    assert "extraDataVariables" in source
    assert "toggleVariableMenu" in source
    assert "applyUserVariable" in source
    assert "{user.user_name}" in source


def test_param_editor_default_value_menu_disabled_for_required_params():
    source = _read(PARAM_EDITOR)

    # 默认值输入框与变量按钮都要跟着「必填」禁用
    assert ':disabled="p.required"' in source
    assert "默认值仅在非必填时生效" in source
    assert ':aria-expanded="openVariableMenuKey === p.key"' in source


def test_user_variable_catalog_covers_core_fields():
    source = _read(USER_VARIABLES)

    for value in (
        "{user.id}",
        "{user.user_name}",
        "{user.real_name}",
        "{user.dept_code}",
        "{user.org_path}",
        "{user.role}",
    ):
        assert value in source
    assert "collectExtraDataVariables" in source
    assert "localStorage.getItem('user_info')" in source


def test_tool_variable_menu_hides_sensitive_extra_data_keys():
    source = _read(USER_VARIABLES)

    assert "isSensitiveUserVariableKey" in source
    assert "excludeSensitive" in source
    # 工具参数菜单必须显式开启过滤：运行时不会把敏感键暴露成 {user.xxx}
    assert "excludeSensitive: true" in _read(PARAM_EDITOR)


def test_param_editor_explains_required_vs_default():
    source = _read(PARAM_EDITOR)

    # 用户反馈「必填与默认值的逻辑不容易理解」：标题旁提供 ? 说明入口
    assert "必填与默认值说明" in source
    assert "必填与默认值怎么配合" in source
    assert "不是运行时的硬校验" in source


def test_metadata_and_tool_share_one_variable_catalog():
    metadata = _read(METADATA_PAGE)

    assert "BUILTIN_USER_VARIABLES" in metadata
    assert "collectExtraDataVariables" in metadata
    # 页面里不再自己维护第二份内置变量表
    assert "const BUILTIN_USER_VARIABLES = [" not in metadata


def test_runtime_replaces_user_placeholders_before_path_and_headers():
    source = _read(GENERIC_API)

    assert "_apply_user_placeholders" in source
    assert "current_user_variables" in source
    assert "replace_headers_user_placeholders" in source

    replace_at = source.index("_apply_user_placeholders(config, params, variables)")
    path_at = source.index("# 1. Path Substitution")
    assert replace_at < path_at, "占位符必须在路径替换之前处理，否则 {user.id} 会被拼进 URL"


def test_preview_maps_missing_user_variable_to_its_own_error_kind():
    endpoint = _read(TOOLS_ENDPOINT)
    assert "UserPlaceholderError" in endpoint
    assert 'error_kind="missing_user_variable"' in endpoint

    assert "missing_user_variable" in _read(API_TOOL)
    assert "missing_user_variable" in _read(API_TOOL_TESTER)
