"""凭据中心两个前端入口的契约（源码级）。

凭据中心的价值有一半在「入口在哪」——建好的凭据如果没有任何地方能选到，
它就只是一张表。这里锁定四件事：

1. 个人中心有第十个 tab「我的凭据」，位置紧跟「我的 MCP」，且写进类型与深链数组；
2. 系统管理下有独立路由，权限码与后端菜单种子一致；
3. 两个页面都**不渲染明文**：没有 `<input>` 回填已保存的值，也没有「查看/复制」按钮；
4. 选择器按「我的 / 系统共享」分组，并且只返回 id。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONAL_CENTER = ROOT / "frontend/src/views/PersonalCenter.vue"
SECRET_CENTER = ROOT / "frontend/src/views/SecretCenter.vue"
ADMIN_SECRET_CENTER = ROOT / "frontend/src/views/AdminSecretCenter.vue"
PICKER = ROOT / "frontend/src/components/secrets/CredentialPicker.vue"
ROUTER = ROOT / "frontend/src/router/index.ts"
MENU_SEED = ROOT / "app/services/menu_seed.py"


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


class TestPersonalTab:
    def test_tab_is_registered_in_type_and_deeplink_array(self):
        source = _read(PERSONAL_CENTER)

        assert "'mcp' | 'secrets' | 'tasks'" in source
        # 深链 ?tab=secrets 要能直达：数组里没有它就会被回落到 info
        assert "'mcp', 'secrets', 'tasks'" in source

    def test_tab_button_sits_right_after_mcp(self):
        source = _read(PERSONAL_CENTER)

        mcp_button = source.index("我的 MCP")
        secrets_button = source.index("我的凭据")
        tasks_button = source.index("我的任务", secrets_button)
        assert mcp_button < secrets_button < tasks_button

    def test_component_is_mounted(self):
        source = _read(PERSONAL_CENTER)

        assert "import SecretCenter from './SecretCenter.vue'" in source
        assert "activeTab === 'secrets'" in source
        assert "<SecretCenter />" in source


class TestAdminEntry:
    def test_route_is_registered_under_dashboard(self):
        source = _read(ROUTER)

        assert "path: 'secrets'" in source
        assert "AdminSecretCenter" in source
        assert "menu:system:credentials" in source

    def test_permission_code_matches_backend_seed(self):
        router_source = _read(ROUTER)
        seed_source = _read(MENU_SEED)

        assert "menu:system:credentials" in router_source
        assert 'code="menu:system:credentials"' in seed_source
        # 路由名要与种子的 active_route_names 一致，否则菜单高亮不上
        assert "active_route_names=\"AdminSecretCenter\"" in seed_source


class TestNoPlaintextOnScreen:
    def test_credential_never_rendered_from_a_value_field(self):
        for path in (SECRET_CENTER, ADMIN_SECRET_CENTER):
            source = _read(path)
            # 列表只渲染后端给的掩码，不碰任何 *_encrypted / 明文值字段
            assert "value_encrypted" not in source
            assert "value_preview" not in source or "readable" in source

    def test_no_reveal_button(self):
        """按「有没有这条能力」断言，而不是扫文案。

        界面必须能写「无法再查看明文」这句话——它在提示用户约束是什么。
        把它当违规词扫，测出来的是文案写得好不好，不是代码守不守约。
        真正要拦的是三件事：读明文的接口、回显值的输入框、复制值的按钮。
        """
        for path in (SECRET_CENTER, ADMIN_SECRET_CENTER):
            source = _read(path)
            template = source[source.index("<template>") :]

            assert "/value" not in template, f"{path.name} 不该调用读明文的接口"
            assert "clipboard" not in source.lower() or "copyToClipboard" not in source
            # 值输入框必须是 password，且从不绑定已保存的值
            assert 'type="password"' in template or path is ADMIN_SECRET_CENTER

    def test_admin_page_states_that_admin_cannot_read(self):
        source = _read(ADMIN_SECRET_CENTER)
        assert "管理员也读不到凭据明文" in source


class TestPicker:
    def test_groups_by_source(self):
        source = _read(PICKER)

        assert "groups.mine" in source
        assert "groups.shared" in source
        assert "系统共享" in source

    def test_only_emits_ids(self):
        source = _read(PICKER)

        # emit 的是 id，不是值；组件内部也不缓存明文
        assert "item.id" in source
        assert "value_masked" in source


class TestCreateFlow:
    def test_create_sends_scenario_and_host_whitelist(self):
        source = _read(SECRET_CENTER)

        assert "allowed_scenarios" in source
        assert "allowed_hosts" in source
        # 空白名单 = 只允许内部目标，界面必须讲清楚
        assert "只允许平台内部目标" in source

    def test_rotation_mentions_overlap_window(self):
        source = _read(SECRET_CENTER)

        assert "overlap_days" in source
        assert "旧值" in source


class TestHeaderBindingEntry:
    """工具编辑页的绑定入口。

    凭据建完必须有一个**配置时**的入口能用上它。这里锁定的是「入口存在且语义正确」：
    绑定的槽位命名、占位符形状、新建工具时的禁用条件。
    """

    HEADERS = ROOT / "frontend/src/components/system/ToolHeadersEditor.vue"
    REGISTRY = ROOT / "frontend/src/components/system/ToolRegistry.vue"

    def test_editor_has_binding_button(self):
        source = _read(self.HEADERS)

        assert "CredentialPicker" in source
        assert "openBinding" in source
        assert "KeyIcon" in source

    def test_slot_naming_matches_backend(self):
        source = _read(self.HEADERS)

        # 槽位必须是 header:<名称>，与 injection.py 里读的 slot 拼法一致
        assert "`header:${key}`" in source

    def test_placeholder_shape_matches_runtime_detection(self):
        source = _read(self.HEADERS)

        # generic_api 用 CREDENTIAL_PLACEHOLDER_PREFIX 判断「还没填」
        assert "CREDENTIAL:" in source

    def test_binding_requires_saved_tool(self):
        source = _read(self.HEADERS)

        assert "canBind" in source
        assert "保存工具后可绑定凭据" in source

    def test_registry_passes_consumer_id(self):
        source = _read(self.REGISTRY)

        assert ':consumer-id="isEditing && toolForm.id' in source

    def test_unbinding_clears_the_placeholder(self):
        source = _read(self.HEADERS)

        # 解绑同时清空值：留着一个指向已删凭据的占位符，运行时必报「凭据没填」
        assert "row.value = ''" in source


class TestMcpBindingEntry:
    """MCP 服务表单的绑定入口。

    MCP 的规则和 API 工具**相反**：绑定优先。原因是 `fixed_token` 是服务级单值
    （管理员配一次、全平台共用），不是用户手写的值；不覆盖就永远不生效。
    """

    MCP_REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"
    INJECTION = ROOT / "app/services/secrets/injection.py"

    def test_form_offers_credential_picker(self):
        source = _read(self.MCP_REGISTRY)

        assert "CredentialPicker" in source
        assert 'scene="mcp"' in source
        assert "saveMcpBinding" in source

    def test_slot_and_consumer_type_match_backend(self):
        source = _read(self.MCP_REGISTRY)

        assert "consumer_type: 'mcp_server'" in source
        assert "slot: 'header:Authorization'" in source

    def test_only_available_when_editing(self):
        source = _read(self.MCP_REGISTRY)

        # 绑定的消费者是 mcp_server + 服务 id，新建时还没有 id
        assert 'v-if="isEditing"' in source
        assert "editingId.value" in source

    def test_ui_states_binding_wins_over_fixed_token(self):
        source = _read(self.MCP_REGISTRY)

        assert "绑定优先于上面的固定 Token" in source

    def test_backend_overrides_static_token_for_mcp(self):
        source = _read(self.INJECTION)

        # API 工具只补空位，MCP 覆盖——两条规则必须同时留在代码里
        assert "looks_like_credential_header" in source
        assert "绑定优先，不是只补空位" in source


class TestScriptEnvBindingEntry:
    """脚本工具的环境变量绑定。

    这里是唯一**槽位不预定义**的消费者：变量名由用户在脚本里自己定
    （``os.environ["DB_PASSWORD"]``），所以界面上必须能填名字，不能只给个选择器。
    """

    WIZARD = ROOT / "frontend/src/components/system/ScriptToolWizard.vue"

    def test_wizard_offers_env_binding(self):
        source = _read(self.WIZARD)

        assert "CredentialPicker" in source
        assert 'scene="script_env"' in source
        assert "saveEnvBinding" in source

    def test_slot_shape_matches_backend(self):
        source = _read(self.WIZARD)

        assert "consumer_type: 'script_tool'" in source
        # 后端按 prefix="env:" 找槽位，名字必须拼进 slot 里
        assert "slot: `env:${variable}`" in source

    def test_requires_explicit_variable_name(self):
        source = _read(self.WIZARD)

        # 光选凭据不够——没有变量名，后端不知道注入成哪个环境变量
        assert "ENV_NAME_RE" in source
        assert "envVariable" in source

    def test_only_available_when_editing(self):
        source = _read(self.WIZARD)

        assert 'v-if="!isEditing"' in source
        assert "先保存，再回来配" in source

    def test_env_file_is_shell_quoted(self):
        from app.services.secrets.injection import build_env_file_content

        rendered = build_env_file_content({"DB_PASSWORD": "p@ss$(whoami)`id`"})
        # 单引号包裹，$ 与反引号都不展开
        assert rendered == "DB_PASSWORD='p@ss$(whoami)`id`'\n"


class TestValueRevealAndBindingsOverview:
    """「查看变量值」与「这条凭据用在哪」两个入口。

    前者是 variable 类型存在的意义——值本来就是要填到别处去的，不给看等于没用；
    后者回答「我把它绑到哪了」，否则只能去每个工具、每个 MCP 服务里翻。
    """

    CENTER = ROOT / "frontend/src/views/SecretCenter.vue"
    ENDPOINTS = ROOT / "app/api/portal/endpoints/secrets.py"

    def test_reveal_only_for_variable(self):
        source = _read(self.CENTER)

        # 按钮存在，但挂条件 —— credential 连「看一眼」的入口都不该有
        assert "selected.secret_type === 'variable'" in source
        assert "openReveal" in source
        assert "/value" in source

    def test_reveal_mentions_audit(self):
        source = _read(self.CENTER)

        # 读明文会记流水，界面上要讲清楚，避免用户以为这是「悄悄看一眼」
        assert "这次读取会记一条使用记录" in source

    def test_bindings_overview_queries_by_secret(self):
        source = _read(self.CENTER)

        # 右侧详情内嵌展示「已绑定」，不再是弹窗；查询仍按 secret_id 反查
        assert "loadBindingsOf" in source
        assert "secret_id: item.id" in source

    def test_backend_accepts_secret_id_lookup(self):
        source = _read(self.ENDPOINTS)

        assert "secret_id: str | None = Query(None)" in source
        assert "请提供 secret_id" in source

    def test_no_clipboard_leak_in_credential_rows(self):
        source = _read(self.CENTER)

        # 复制能力只允许出现在 reveal 弹窗里（且只对 variable）；列表行不能有
        assert "copyRevealed" in source
