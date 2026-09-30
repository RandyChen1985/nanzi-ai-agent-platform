"""前端 Langfuse 配置 Tab 契约测试（纯源码断言，不跑浏览器）。

保护三件事：
1. Langfuse 配置是**独立 Tab**，没有被塞进「参数配置」的键值表格；
2. Tab 内容委托给独立组件 ``components/system/LangfuseConfig.vue``；
3. 保存按钮绑定独立权限 ``element:system:langfuse_save``，且该权限已在前端权限目录登记。
"""

import pathlib

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend"
SYSTEM_CONFIG = FRONTEND / "src" / "views" / "SystemConfig.vue"
LANGFUSE_COMPONENT = FRONTEND / "src" / "components" / "system" / "LangfuseConfig.vue"
PERMISSIONS = FRONTEND / "src" / "constants" / "permissions.ts"

PERMISSION_ID = "element:system:langfuse_save"


@pytest.fixture(scope="module")
def system_config_source() -> str:
    return SYSTEM_CONFIG.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def component_source() -> str:
    assert LANGFUSE_COMPONENT.exists(), f"缺少组件: {LANGFUSE_COMPONENT}"
    return LANGFUSE_COMPONENT.read_text(encoding="utf-8")


def test_tab_is_registered_in_active_tab_union(system_config_source):
    assert "'langfuse'" in system_config_source
    assert "ref<'diagnostics'" in system_config_source


def test_tab_button_label_is_chain_tracing_only(system_config_source):
    """Tab 文案就是「链路追踪」四个字，不挂 Langfuse 前缀（用户要求）。"""
    assert 'activeTab = \'langfuse\'' in system_config_source
    assert "ChartBarSquareIcon" in system_config_source

    start = system_config_source.index('@click="activeTab = \'langfuse\'"')
    end = system_config_source.index("</button>", start)
    button_block = system_config_source[start:end]
    assert "链路追踪" in button_block
    assert "Langfuse" not in button_block, "Tab 文案不应带 Langfuse 前缀"


def test_tab_panel_renders_dedicated_component(system_config_source):
    assert 'activeTab === \'langfuse\'' in system_config_source
    assert "import LangfuseConfig from '../components/system/LangfuseConfig.vue'" in system_config_source
    assert "<LangfuseConfig" in system_config_source


def test_tab_is_not_added_to_key_value_config_category(system_config_source):
    """不得把 Langfuse 配置混进「参数配置」的分类与键白名单。"""
    assert "observability" not in system_config_source
    assert "langfuse_host" not in system_config_source
    assert "langfuse_secret_key" not in system_config_source


def test_component_uses_dedicated_endpoint(component_source):
    assert "/api/portal/system/langfuse" in component_source
    # 连通性探测走同一前缀下的 /test（模板拼接，故断言拼接形式）
    assert "${ENDPOINT}/test" in component_source


def test_component_binds_dedicated_permission(component_source):
    assert PERMISSION_ID in component_source


def test_component_covers_all_config_fields(component_source):
    for field in (
        "enabled",
        "host",
        "public_key",
        "secret_key",
        "sample_rate",
        "capture_content",
        "environment",
        "release",
        "timeout_seconds",
        "trace_url_template",
    ):
        assert field in component_source, f"组件缺少字段 {field}"


def test_component_never_echoes_secret_back(component_source):
    """密钥只提交新输入的值；未修改时不回传，避免把掩码写成真密钥。"""
    assert "has_secret_key" in component_source
    assert "secret_key" in component_source


def test_component_exposes_status_and_test_connection(component_source):
    assert "status" in component_source
    assert "client_active" in component_source
    assert "测试连接" in component_source


def test_component_handles_api_failure(component_source):
    assert "catch" in component_source
    assert "showToast" in component_source


def test_permission_is_registered_in_catalog():
    source = PERMISSIONS.read_text(encoding="utf-8")
    assert PERMISSION_ID in source


def test_config_fields_are_hidden_when_switch_is_off(component_source):
    """总开关关闭时必须隐藏全部配置项（用户要求）。

    关闭态若仍铺满连接参数/采样率，会与「已关闭」自相矛盾；但必须留一句说明，
    否则用户会以为配置丢了。
    """
    switch_idx = component_source.index('v-if="!form.enabled"')
    config_idx = component_source.index("Langfuse 地址")
    between = component_source[switch_idx:config_idx]
    assert "<template v-else>" in between, "配置项必须位于「开关开启」分支内"
    assert "开启总开关后" in component_source, "关闭态缺少说明文案"


def test_switch_off_save_only_submits_the_switch(component_source):
    """关闭态保存只提交开关本身，不把隐藏字段的旧值（甚至空值）写回。"""
    start = component_source.index("const buildPayload")
    body = component_source[start:component_source.index("const save =")]
    assert "if (!form.value.enabled)" in body
    assert "return { enabled: false }" in body


def test_test_connection_button_is_hidden_when_disabled(component_source):
    """未启用时没有可探测的连接，隐藏按钮避免必然失败的点击。"""
    idx = component_source.index("testConnection\"")
    assert 'v-if="form.enabled"' in component_source[:idx]


def test_test_connection_submits_draft_form_values(component_source):
    """「测试连接」必须带当前表单值，否则用户被逼成"先保存再测"。

    更要紧的是：不带值时后端测的是**已保存的旧值**，改了地址点测试可能显示
    「连接成功」却其实是旧地址通的——比不能测更误导。
    """
    start = component_source.index("const testConnection")
    body = component_source[start:component_source.index("const clearSecret")]
    assert "buildProbePayload" in body, "探测请求必须携带草稿值"
    assert "axios.post(`${ENDPOINT}/test`, buildProbePayload())" in body


def test_probe_payload_includes_connection_fields(component_source):
    start = component_source.index("const buildProbePayload")
    body = component_source[start:component_source.index("const save =")]
    for field in ("enabled", "host", "public_key", "timeout_seconds"):
        assert field in body, f"探测载荷缺少 {field}"
    # 密钥只在真的输入了新值时才提交（留空＝复用已保存密钥）
    assert "if (typedSecret)" in body
    assert "payload.secret_key = typedSecret" in body


def test_ui_tells_user_probe_uses_draft_values(component_source):
    """界面要说明「未保存的修改也生效」，否则用户仍会先去点保存。"""
    assert "未保存的修改也会生效" in component_source
