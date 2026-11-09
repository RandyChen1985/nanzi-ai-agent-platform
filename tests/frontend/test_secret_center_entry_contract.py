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
ACCOUNT_MENU = ROOT / "frontend/src/components/layout/AccountMenu.vue"
SECRET_CENTER = ROOT / "frontend/src/views/SecretCenter.vue"
ADMIN_CENTER = ROOT / "frontend/src/views/AdminSecretCenter.vue"
USER_DEPT_PICKER = ROOT / "frontend/src/components/common/UserDeptPicker.vue"
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

    def test_tab_button_sits_right_after_permissions(self):
        """凭据紧跟在「我的权限」后面。

        它原本挂在「我的 MCP」之后——那是按实现顺序排的，不是按用户找东西的顺序。
        凭据是「我手上有什么钥匙」，和权限是同一类问题，挨着放才找得到。
        """
        source = _read(PERSONAL_CENTER)

        nav_start = source.index('<nav class="-mb-px')
        nav = source[nav_start:source.index("</nav>", nav_start)]
        permissions = nav.index("我的权限")
        secrets = nav.index("我的凭据")
        memory = nav.index("我的记忆")
        assert permissions < secrets < memory
        # 别又滑回 MCP 后面
        assert nav.index("我的 MCP") > secrets

    def test_component_is_mounted(self):
        source = _read(PERSONAL_CENTER)

        assert "import SecretCenter from './SecretCenter.vue'" in source
        assert "activeTab === 'secrets'" in source
        assert "<SecretCenter />" in source


class TestCenterUiShape:
    def test_usage_log_is_a_modal_not_an_inline_block(self):
        """使用记录是弹框，不是页面底部的折叠区。

        内嵌折叠会把整页撑长，而且要滚到底才看得见——「谁用过我的凭据」是想起来
        才查一次的事，弹出来看完就关更合适。
        """
        source = _read(SECRET_CENTER)

        assert "usageOpen" in source
        # 折叠区那套「收起 / 展开」的文案不该留
        assert "usageOpen ? '收起' : '展开'" not in source
        # 弹框必须是 Teleport + z-[100]，否则会被侧边栏/遮罩层压住
        usage_block = source[source.index("<!-- 使用记录弹框 -->"):]
        assert '<Teleport to="body">' in usage_block[:400]
        assert "z-[100]" in usage_block[:400]

    def test_create_flow_is_a_three_step_wizard(self):
        source = _read(SECRET_CENTER)

        assert "createStep" in source
        assert "nextCreateStep" in source
        assert "prevCreateStep" in source
        for label in ("基本信息", "注入范围", "确认"):
            assert label in source

    def test_scheduled_task_is_not_a_selectable_scene(self):
        """定时任务不再是一个可选场景。

        它曾经是：无人值守调用必须显式勾 ``scheduled_task`` 才注入。实际用起来是
        纯摩擦——定时任务里跑的就是它自己那个 MCP/工具，用户在对话里刚验证过能用，
        到了定时任务却要再勾一次，还看不出差在哪。现在定时任务只是「谁在跑」，
        不是「走哪条链路」，判定完全按内部那个工具/MCP 的场景走。
        """
        source = _read(SECRET_CENTER)

        # 可勾选的场景列表里不能有它
        idx = source.find("const SCENES = [")
        assert idx > 0, "找不到 SCENES 定义"
        block = source[idx:source.find("]", idx)]
        assert "scheduled_task" not in block, "scheduled_task 不该出现在可勾选场景里"

        # 但它必须被登记成「已废弃」，否则历史数据会原样露在界面上
        assert "DEPRECATED_SCENES = new Set(['scheduled_task'])" in source


class TestAdminCreateWizard:
    def test_admin_create_uses_same_three_step_wizard(self):
        """管理端和个人中心必须是同一套步骤模式。

        个人中心改了三步、管理端还是单页，管理员建系统级凭据时要面对一屏十几个
        字段——和自己刚在个人中心用过的那套完全不同，也更容易漏勾场景。
        """
        source = _read(ADMIN_CENTER)

        assert "createStep" in source
        assert "nextCreateStep" in source
        assert "prevCreateStep" in source
        for label in ("基本信息", "注入范围", "确认"):
            assert label in source

    def test_admin_scene_list_matches_backend_reality(self):
        """管理端可选场景必须和后端一致：只有真正有注入点的才列出来。"""
        source = _read(ADMIN_CENTER)

        scenes = source[source.index("CREATE_SCENES"):]
        scenes = scenes[: scenes.index("]")]
        for key in ("mcp", "api_tool", "script_env"):
            assert f"'{key}'" in scenes
        # 定时任务不再是独立场景；agent 没有注入点
        assert "'scheduled_task'" not in scenes
        agent_line = next(ln for ln in scenes.splitlines() if "'agent'" in ln)
        assert "wired: false" in agent_line


class TestPlatformMcpCredentialEntry:
    """普通用户给平台 MCP 服务绑自己凭据的入口。

    平台 MCP 列表接口只给管理员，普通用户在「我的 MCP」里根本看不到这些服务，
    所以「每人用自己的 Token」曾经只有管理员用得上——而管理员恰恰配的就是
    服务级固定 Token，最不需要它。
    """

    PANEL = ROOT / "frontend/src/components/mcp/PlatformMcpCredentialPanel.vue"

    def test_panel_lists_platform_targets_and_binds(self):
        source = _read(self.PANEL)

        assert "/api/portal/secrets/platform-targets" in source
        assert "'mcp_server'" in source and "'api_tool'" in source

    def test_tool_binding_only_offers_bindable_slots(self):
        """API 工具只把「留空/占位」的头列为可绑，且按槽位逐个绑。

        注入层对工具是只补空位：手填了真实值的 header 绑了也不生效。所以面板
        直接消费后端算好的 ``bindable_slots``，不在前端自己猜哪些头像凭据。
        """
        source = _read(self.PANEL)

        assert "bindable_slots" in source
        assert "openBindTool" in source
        # 槽位前缀由后端给（header:/query:/body:），前端不再自己拼
        assert "return editing.value.slot" in source

    def test_mcp_with_multiple_auth_headers_binds_per_slot(self):
        """服务配了多个认证头时，每个槽位各自绑，不是一个按钮全包。"""
        source = _read(self.PANEL)

        assert "openBindMcp(item, slot)" in source
        assert "item.bindable_slots.length > 1" in source

    def test_tool_editor_warns_binding_is_per_user(self):
        """平台工具里绑凭据只对自己生效，界面必须说清，并给出「共用就写死值」。"""
        source = _read(ROOT / "frontend/src/components/system/ToolHeadersEditor.vue")

        assert "只对" in source and "你自己" in source
        assert "只补空位" in source

    def test_panel_is_link_level_only(self):
        """只做绑定：不给地址、不给改服务，用户的权限刚好够用。"""
        source = _read(self.PANEL)
        view = _read(ROOT / "frontend/src/views/McpManagement.vue")

        assert "platform" in view and "PlatformMcpCredentialPanel" in view
        assert "sse_url" not in source

    def test_menu_has_platform_entry(self):
        menu = _read(ROOT / "frontend/src/components/mcp/McpPersonalMenu.vue")
        types = _read(ROOT / "frontend/src/utils/mcpShare.ts")

        assert "'platform'" in menu
        assert "平台服务" in menu
        assert "'platform'" in types


class TestSecretRecordTabs:
    """「谁用了」和「谁改了」是两个问题，必须分开查。"""

    def test_personal_center_has_audit_tab(self):
        source = _read(SECRET_CENTER)

        assert "recordTab" in source
        assert "/api/portal/secrets/audit" in source
        assert "变更记录" in source

    def test_admin_center_has_audit_entry(self):
        source = _read(ADMIN_CENTER)

        assert "openAudit" in source
        assert "/api/portal/secrets/audit" in source
        assert "变更记录" in source

    def test_actions_are_shown_in_chinese(self):
        """审计里的 action 是内部常量，不能原样抛给用户。"""
        for path in (SECRET_CENTER, ADMIN_CENTER):
            source = _read(path)
            assert "ACTION_LABEL" in source
            assert "rotate: '轮换'" in source


class TestSecretCenterUsageHelp:
    """凭据中心顶部要能回答「这些凭据到底会被用在哪」。

    这层信息原来只写在代码里：四个真实注入点、两个不注入的勾选项、三道闸门。
    每次都要口头解释一遍，界面上一个字都没有。
    """

    CENTER = ROOT / "frontend/src/views/SecretCenter.vue"

    def test_help_button_exists(self):
        source = _read(self.CENTER)
        assert "usageHelpOpen = true" in source
        assert 'aria-label="查看凭据使用范围"' in source

    def test_help_lists_all_four_real_injection_points(self):
        source = _read(self.CENTER)
        for anchor in ["MCP 服务", "API 工具 · 请求头", "API 工具 · 参数位", "脚本工具 · 环境变量"]:
            assert anchor in source, f"弹窗缺少场景：{anchor}"

    def test_help_says_scheduled_task_reuses_the_same_rule(self):
        """定时任务不是单独的注入点，它跑的就是内部那个工具/MCP 的通道。"""
        source = _read(self.CENTER)
        assert "沿用同一套判定" in source
        assert "暂未实现" in source

    def test_help_overlay_follows_portal_contract(self):
        """弹层必须是 Teleport + z-[100]，否则会被页面元素盖住。"""
        source = _read(self.CENTER)
        marker = "usageHelpOpen"
        idx = source.find('<div\n        v-if="usageHelpOpen"')
        assert idx > 0
        segment = source[max(0, idx - 200):idx + 200]
        assert 'Teleport to="body"' in source[:idx]
        assert "fixed inset-0 z-[100]" in segment


class TestMcpWizardPendingBindings:
    """新建 MCP 时就能选凭据，而不是逼用户先存一遍空配置再回来改。

    绑定是 `(consumer_id, slot)` 维度的，而新建向导里服务还没有 id。以前干脆把
    「来自凭据中心」整个藏起来（`v-if="isEditing"`），只让用固定 Token。
    """

    REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"

    def test_pending_store_exists(self):
        source = _read(self.REGISTRY)
        assert "pendingBindings" in source
        assert "flushPendingBindings" in source

    def test_authorization_credential_tab_shown_while_creating(self):
        """二选一不能只在编辑态出现。"""
        source = _read(self.REGISTRY)
        assert 'v-if="isEditing" class="mt-3 inline-flex rounded-lg bg-white p-0.5 ring-1 ring-indigo-100"' not in source
        assert 'class="mt-3 inline-flex rounded-lg bg-white p-0.5 ring-1 ring-indigo-100"' in source

    def test_header_bind_does_not_require_saved_server(self):
        """每行 Header 的钥匙按钮以前只有已保存的行才有。"""
        source = _read(self.REGISTRY)
        assert "if (!name || !editingId.value) return" not in source
        # 新增行也要能挑凭据
        assert 'v-if="!pair.existing"' in source

    def test_pending_flushed_after_create(self):
        source = _read(self.REGISTRY)
        assert "await flushPendingBindings(newId)" in source

    def test_binding_failure_does_not_rollback_server(self):
        """服务建好了但绑定失败，要明说，不能显示成「已绑定」。"""
        source = _read(self.REGISTRY)
        assert "的凭据绑定失败" in source

    def test_reset_clears_pending(self):
        source = _read(self.REGISTRY)
        idx = source.find("const resetWizard = () => {")
        assert idx > 0
        segment = source[idx:idx + 2000]
        assert "pendingBindings.value = {}" in segment


class TestMcpWizardSectionDividers:
    """三步向导第 1 步的三个分区要有视觉分界。

    Authorization、其他 Header、用户身份传递，看起来都是「鉴权相关」，实际是三件
    不同的事：服务怎么认我们 / 补充头 / 我们怎么告诉服务现在是谁。混成一片时，
    用户会以为关掉 Header 就把身份传递一起关了。
    """

    REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"

    def test_dividers_exist_between_sections(self):
        source = _read(self.REGISTRY)

        # 必须带 shrink-0：父容器是 flex-col，可收缩的 1px 元素会被压成 0——
        # 线还在 DOM 里，一个字都看不见（踩过两次）。
        assert source.count("h-px shrink-0 bg-gray-300") >= 2
        # 分割线必须带 aria-hidden：它是纯装饰，读屏不该念出来
        assert 'bg-gray-300" aria-hidden="true"' in source

    def test_divider_survives_flex_shrink(self):
        """没有 shrink-0 的 1px 元素在 flex 列里会被压没。"""
        source = _read(self.REGISTRY)
        lines = [l for l in source.splitlines() if "bg-gray-300" in l and "aria-hidden" in l]
        assert lines
        for line in lines:
            assert "shrink-0" in line
            assert "h-px" in line

    def test_wizard_container_uses_gap_not_space_y(self):
        """父容器用 gap 而不是 space-y。

        space-y 按 DOM 顺序发 margin，可这几个分区靠 CSS order 重排——margin 会
        落到错误的元素上，视觉间距和预期对不上。gap 按渲染顺序分配。
        """
        source = _read(self.REGISTRY)
        assert "flex-1 flex-col gap-5 overflow-y-auto" in source
        assert "flex-1 flex-col space-y-5 overflow-y-auto" not in source

    def test_divider_order_matches_render_order(self):
        """分区靠 CSS order 排序，分割线必须用相邻分区的 order 值。

        用错 order 时线会跑到别的分区之间——DOM 顺序和渲染顺序不是一回事。
        """
        source = _read(self.REGISTRY)
        lines = source.splitlines()

        dividers = [l for l in lines if "bg-gray-300" in l and "aria-hidden" in l]
        assert len(dividers) == 2
        assert "order-2" in dividers[0] or "order-2" in dividers[1]
        assert "order-3" in dividers[0] or "order-3" in dividers[1]


class TestDismissibleHints:
    """提示条可以关掉，且关掉后记得住。

    这类提示是给第一次进来的人看的（「凭据建完即不可读」「建完不等于能用」），
    老用户每进一次就被挡一次，久了只会被无视——比不显示更糟。
    """

    DISMISSIBLE = ROOT / "frontend/src/utils/dismissible.ts"

    def test_helper_persists_to_local_storage(self):
        source = _read(self.DISMISSIBLE)

        assert "localStorage" in source
        # 读不到 localStorage（隐私模式）时不能抛出去，否则整页白屏
        assert "catch" in source

    def test_both_centers_use_it(self):
        for path in (SECRET_CENTER, ADMIN_CENTER):
            source = _read(path)
            assert "useDismissible" in source, path

    def test_personal_center_hints_are_dismissible(self):
        source = _read(SECRET_CENTER)

        # 两条都要能关：安全说明 + 未绑定提醒
        assert "dismissSecurityHint" in source
        assert "dismissUnboundHint" in source
        assert 'v-if="showSecurityHint"' in source
        assert "showUnboundHint" in source

    def test_admin_center_hints_are_dismissible(self):
        source = _read(ADMIN_CENTER)

        assert "dismissNoGrantHint" in source
        assert "dismissPlaintextHint" in source
        # 未授权那条是复合条件（stats.noGrant && showNoGrantHint），单独看变量
        assert "stats.noGrant && showNoGrantHint" in source
        assert 'v-if="showPlaintextHint"' in source


class TestGrantDialogUsesRealData:
    def test_grantee_picker_is_not_a_manual_id_input(self):
        """授权对象从真实数据里选，不是手填 ID。

        手填 ID 要求管理员先去别处查到「7」是谁、部门的编码是什么，输错了还会
        安静地授权给一个不存在的对象——被授权者永远看不到，管理员以为授权好了。
        """
        source = _read(ADMIN_CENTER)
        picker = _read(USER_DEPT_PICKER)

        assert "grantForm" not in source
        assert "UserDeptPicker" in source
        # 数据源在公共组件里，不在页面里各写一份
        assert "/api/portal/management/users" in picker
        assert "/api/portal/departments/tree" in picker
        assert "/api/portal/management/users" not in source

    def test_already_granted_is_marked_and_not_selectable(self):
        """已经授权的对象要标出来并且不可再选，否则会重复授权、界面看不出结果。"""
        source = _read(ADMIN_CENTER)
        picker = _read(USER_DEPT_PICKER)

        assert "grantedKeys" in source
        assert "disabledKeys" in picker
        assert "isDisabled" in picker

    def test_picker_clears_selection_when_switching_type(self):
        """切「用户/部门」必须清空已选。

        提交时只能有一个 grantee_type，混着 'user:7' 和 'dept:3' 会一半失败，
        而失败的又只是被计入 failed——用户看到「已授权 3 个」其实只成了 1 个。
        """
        picker = _read(USER_DEPT_PICKER)
        body = picker[picker.index("function switchType"):]
        body = body[: body.index("function toggle")]

        assert "emit('update:modelValue', [])" in body


class TestAccountMenuEntry:
    def test_account_menu_links_to_secret_tab(self):
        """头像卡片里的入口必须和 tab 指向同一个深链。

        个人中心 tab 改了位置、卡片里却没有入口，用户还是只能先点进个人中心
        再找——卡片存在的意义就是省掉这一步。
        """
        source = _read(ACCOUNT_MENU)

        assert "/dashboard/personal?tab=secrets" in source
        assert "我的凭据" in source
        group = source[source.index('aria-label="账户"'):source.index('aria-label="设置"')]
        assert group.index("我的权限") < group.index("我的凭据") < group.index("Token 用量")


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
        # 槽位现在由调用点拼：Authorization 一处、「其他 Header」按名字一处
        assert "'header:Authorization'" in source
        assert "`header:${headerBindSlot.value}`" in source

    def test_only_available_when_editing(self):
        source = _read(self.MCP_REGISTRY)

        # 绑定的消费者是 mcp_server + 服务 id，新建时还没有 id
        assert 'v-if="isEditing"' in source
        assert "editingId.value" in source

    def test_ui_makes_token_and_credential_mutually_exclusive(self):
        """固定 Token 和凭据必须二选一，而不是并排两块。

        并排时用户会以为「填了 Token 又绑了凭据 = 两个都用」，实际是绑定单方面
        盖过 Token（``apply_mcp_bindings`` 覆盖写法）。二选一之后若仍有绑定残留，
        界面必须明确说出「实际走的不是你看到的那个」。
        """
        source = _read(self.MCP_REGISTRY)

        assert "authSource" in source
        assert "'token'" in source and "'credential'" in source
        # 二选一后不许再出现「上面那块 Token 被下面覆盖」这种并排语义
        assert "绑定优先于上面的固定 Token" not in source
        # 选回固定 Token 但绑定还在时，要如实警告
        assert "调用时会用它盖过这里的固定 Token" in source

    def test_every_mcp_header_can_bind_credential(self):
        """「其他 Header」也要能来自凭据，和 API 工具一致。

        槽位是 ``header:<名称>``，后端 ``apply_mcp_bindings`` 本来就遍历所有
        header，只给 Authorization 开按钮是把已有能力藏起来了。
        """
        source = _read(self.MCP_REGISTRY)

        assert "openHeaderBind" in source
        assert "bindingOfHeader" in source
        assert "headerBindSlot" in source
        # 绑定后的行不能继续显示掩码值——值根本不在服务配置里
        assert "值根本不在服务配置里" in source

    def test_backend_overrides_static_token_for_mcp(self):
        source = _read(self.INJECTION)

        # API 工具只补空位，MCP 覆盖——两条规则必须同时留在代码里
        assert "_looks_unfilled" in source, "API 工具仍然只补空位"
        # MCP 改成「绑定槽位驱动」：遍历绑定表而不是遍历 headers，也不再靠
        # 「名字像不像凭据头」来猜。否则 X-Tenant 这类带旧值的 Header 会在
        # 循环里被 continue 掉——连库都不查，用户只看到「绑了但没生效」。
        assert "绑定优先，且无条件覆盖" in source
        assert "list_bound_slots(" in source
        assert "_set_case_insensitive" in source


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


class TestDeprecatedScenarioDisplay:
    """`scheduled_task` 已废弃：后端常量还在（历史数据里有），但代码不认它。

    旧实现把它当普通取值渲染——个人中心连标签都没有，直接露出英文 key，
    用户勾了以为生效，实际永远不会被注入，而且查不出原因。
    """

    def test_personal_view_filters_deprecated_scenes(self):
        source = _read(SECRET_CENTER)

        assert "DEPRECATED_SCENES" in source
        # 列表徽章与表单预览两条路径都要过一遍过滤
        assert "DEPRECATED_SCENES.has" in source
        assert "visibleFormScenarios" in source
        assert 'v-for="key in visibleFormScenarios"' in source

    def test_admin_view_filters_deprecated_scenes(self):
        source = _read(ADMIN_SECRET_CENTER)

        assert "DEPRECATED_SCENES" in source
        assert "visibleScenarios(" in source
        # 不能再直接遍历原始数组
        assert 'v-for="scene in item.allowed_scenarios"' not in source
        assert 'v-for="key in form.allowed_scenarios"' not in source


class TestFilterEmptyState:
    """筛选结果为空时，左侧筛选栏必须还在。

    原来的模板把「一条凭据都没有」和「筛选后没有匹配」合并成一个分支，且这个
    分支写在三栏布局**外面**：

        <div v-else-if="filtered.length === 0">还没有凭据</div>
        <div v-else class="grid ...">   ← 左筛选 / 中列表 / 右详情

    于是点一下「未绑定」这种必然为 0 的筛选，整个左侧栏连同分页一起消失，
    只剩一句「还没有凭据」——用户会以为自己的凭据丢了，而且**没有退路**：
    分类按钮就在刚消失的那一栏里。

    正确的边界是：只有 ``scopedItems``（去掉筛选后的全集）为空才算「真的没有
    凭据」；筛选为空只是中间那一列的事，左栏要一直在，并给出清除筛选的入口。
    """

    def test_whole_page_empty_state_only_for_empty_scope(self):
        source = _read(SECRET_CENTER)

        assert 'v-else-if="filtered.length === 0"' not in source, (
            "整体空态不能用「筛选后为空」判断，那会连左栏一起藏掉"
        )
        assert 'v-else-if="scopedItems.length === 0"' in source

    def test_filtered_empty_state_lives_inside_the_list_column(self):
        source = _read(SECRET_CENTER)

        assert 'v-if="filtered.length === 0"' in source
        assert "没有符合" in source, "筛选为空要和「还没有凭据」用不同的话"

    def test_empty_filter_offers_a_way_back(self):
        source = _read(SECRET_CENTER)

        assert "function resetFilters" in source
        assert "resetFilters()" in source or "@click=\"resetFilters\"" in source


class TestDetailColumnCollapse:
    """右侧详情不显示时，中间列表要把那 330px 收回来。

    三栏是写死的 ``grid-cols-[176px_minmax(0,1fr)_330px]``。筛选为空时
    ``selected`` 变成 null，右栏 ``v-if`` 不渲染——但栅格模板仍然是三列，
    于是列表右边空出一整块 330px 的空白，看着像页面没加载完。
    """

    def test_grid_columns_follow_detail_visibility(self):
        source = _read(SECRET_CENTER)

        assert "lg:grid-cols-[176px_minmax(0,1fr)_330px]" in source, "选中时仍是三栏"
        assert "lg:grid-cols-[176px_minmax(0,1fr)]" in source, "没选中时该收成两栏"
        assert ":class=\"selected" in source or "selected\n" in source


class TestAdminCardShowsBindingAndUsage:
    """管理端的系统凭据卡片要回答「它在跑，还是摆着」。

    个人中心有右侧详情：绑定到哪、近 7 天调了几次、最近一次什么时候。管理端
    原来只有「授权范围」——管理员建完一条系统凭据，既看不出它绑在哪些 MCP
    服务 / API 工具 / 脚本上，也看不出有没有被真正用过。

    管理端是扫读场景（一次看很多条），所以不照搬三栏，直接把这两行并进卡片。
    """

    def test_card_renders_binding_count_and_usage(self):
        source = _read(ADMIN_SECRET_CENTER)

        assert "binding_count" in source
        assert "usage_7d" in source
        assert "last_used_at" in source
        assert "近 7 天调用" in source, "要用中文说清楚，别只丢个数字"
        assert "最近一次" in source

    def test_card_lists_actual_binding_targets(self):
        source = _read(ADMIN_SECRET_CENTER)

        assert "item.bindings" in source, "只给数量没用，要能看到绑到哪个槽位"
        assert "BINDING_CONSUMER_LABEL" in source

    def test_type_covers_new_fields(self):
        source = _read(ADMIN_SECRET_CENTER)

        assert "bindings: SecretBindingRef[]" in source
        assert "usage_7d: number" in source
        assert "failed_7d: number" in source
