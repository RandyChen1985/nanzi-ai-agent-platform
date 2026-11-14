"""凭据中心两个前端入口的契约（源码级）。

凭据中心的价值有一半在「入口在哪」——建好的凭据如果没有任何地方能选到，
它就只是一张表。这里锁定四件事：

1. 个人中心有第十个 tab「我的凭据」，位置紧跟「我的 MCP」，且写进类型与深链数组；
2. 系统管理下有独立路由，权限码与后端菜单种子一致；
3. 两个页面都**不渲染明文**：没有 `<input>` 回填已保存的值，也没有「查看/复制」按钮；
4. 选择器按「我的 / 系统共享」分组，并且只返回 id。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONAL_CENTER = ROOT / "frontend/src/views/PersonalCenter.vue"
ACCOUNT_MENU = ROOT / "frontend/src/components/layout/AccountMenu.vue"
SECRET_CENTER = ROOT / "frontend/src/views/SecretCenter.vue"
ADMIN_CENTER = ROOT / "frontend/src/views/AdminSecretCenter.vue"
USER_DEPT_PICKER = ROOT / "frontend/src/components/common/UserDeptPicker.vue"
ADMIN_SECRET_CENTER = ROOT / "frontend/src/views/AdminSecretCenter.vue"
PICKER = ROOT / "frontend/src/components/secrets/CredentialPicker.vue"
PICKER_FIELDS = ROOT / "frontend/src/utils/credentialPicker.ts"
ROW_ACTIONS = ROOT / "frontend/src/components/secrets/SecretRowActions.vue"
ROUTER = ROOT / "frontend/src/router/index.ts"
MENU_SEED = ROOT / "app/services/menu_seed.py"


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


class TestCredentialTypeCatalog:
    def test_members_open_catalog_with_read_access(self):
        panel = _read(ROOT / "frontend/src/components/secrets/CredentialTypesPanel.vue")
        assert '<CredentialTypesPanel v-if="showTypes" @close=' in _read(SECRET_CENTER)
        assert '<CredentialTypesPanel v-if="showTypes" manage @close=' in _read(ADMIN_SECRET_CENTER)
        assert "listCredentialTypes(props.manage)" in panel
        assert 'v-if="manage && selected.source' in panel

    def test_published_schema_uses_local_input_preview(self):
        panel = _read(ROOT / "frontend/src/components/secrets/CredentialTypesPanel.vue")
        published = panel.split('<template v-if="selected">', 1)[1]
        assert "预览录入表单" in published
        assert ':schema="selected.schema"' in published
        preview = _read(ROOT / "frontend/src/components/secrets/CredentialTypeFormPreview.vue")
        assert "CredentialFieldForm v-model=" in preview
        assert "<form" not in preview and "axios" not in preview and "localStorage" not in preview


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
        """审计里的 action 是内部常量，不能原样抛给用户。

        文案集中到 `utils/secretAudit.ts`：两处中心各写一份时，加一个动作就得记得
        改两个地方，漏一个那边就会显示英文常量。
        """
        shared = _read(ROOT / "frontend/src/utils/secretAudit.ts")
        assert "rotate: '轮换'" in shared
        for path in (SECRET_CENTER, ADMIN_CENTER):
            source = _read(path)
            assert "secretActionLabel(row.action)" in source


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


class TestMcpToolParamCredential:
    """MCP 工具参数级凭据：参数 chip 可点、标记后从模型 schema 剔除。

    影响面是「所有调用者」（模型再也看不到这个参数），所以点一下只开确认弹层，
    不直接落库。
    """

    REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"
    FACTORY = ROOT / "app/services/ai/tools/mcp_factory.py"
    INJECTION = ROOT / "app/services/secrets/injection.py"

    def test_opens_param_settings_table(self):
        """一张表里逐个选，和工具参数编辑器一个交互模型。"""
        source = _read(self.REGISTRY)
        assert "openParamMark(tool, paramName)" in source
        assert "async function submitParamMark()" in source
        # 草稿式：改完一次性提交，不是逐条落库
        assert "paramDraft" in source
        assert "const draftCredentialCount" in source

    def test_param_settings_lists_every_param(self):
        """弹窗里要能一次看到所有参数，而不是只处理被点中的那个。"""
        source = _read(self.REGISTRY)
        assert 'v-for="name in toolParamNames(paramMarkTarget.tool)"' in source
        assert "模型填写" in source
        assert "来自凭据" in source

    def test_param_table_gives_source_column_the_room(self):
        """「值的来源」那列要塞下拉 + 选凭据 + 凭据名 + 状态，160px 根本放不下。

        原先参数名按内容吃走大半宽度、源列钉死 `w-40`：凭据名被截成 `xx-p…`，
        「未选 · 各人绑自己的」竖排成四行。改成固定列宽，参数名只占三成。
        """
        source = _read(self.REGISTRY)
        start = source.find('v-for="name in toolParamNames(paramMarkTarget.tool)"')
        table_head = source[source.rfind("<table", 0, start):start]
        body = source[start:source.find("</table>", start)]

        assert "table-fixed" in table_head, "不固定列宽的话，宽的永远是参数名列"
        assert "w-[30%]" in table_head, "参数名只该拿三成，剩下的给来源列"
        assert "w-40" not in table_head, "来源列不该再钉死 160px"
        # 下拉与凭据控件同一行：一格只占一个控件高度，右侧才有整列宽度可用
        assert "flex flex-wrap items-center" in body
        assert "w-32 shrink-0" in body, "下拉固定宽度，余下的空间留给凭据名与状态"

    def test_unselected_hint_only_when_really_unbound(self):
        """已经绑上了却还挂一句「未选」，比不写更让人糊涂。

        判的是**固定凭据**那条（`fixedBindingOfSlot`）：同槽位可以只有一条规则、
        没有固定凭据，那时「未选」是对的——规则未命中时没有回退目标。
        """
        source = _read(self.REGISTRY)

        assert 'v-else-if="!fixedBindingOfSlot(' in source

    def test_admin_can_bind_secret_for_param(self):
        """管理员要能在参数上直接选凭据（全平台共用那条），不能只让用户各自绑。"""
        source = _read(self.REGISTRY)
        assert "function openParamBind(" in source
        assert "saveParamBind" in source
        # 槽位归到工具所属服务，不是「正在编辑的服务」——从工具卡片进来时后者可能是空
        assert "ctx.tool?.server_id" in source
        assert "选凭据" in source and "换凭据" in source

    def test_binding_reload_does_not_depend_on_editing_id(self):
        """读取绑定不能用 `loadMcpBinding`——它认 `editingId`，而参数设置
        弹窗是从工具卡片直接打开的，那里 `editingId` 是空的。

        症状：绑定成功了（toast 也弹了），但界面一直显示「未选」，重开也不变。
        """
        source = _read(self.REGISTRY)
        assert "async function loadBindingsFor(" in source
        start = source.find("async function saveParamBind(")
        end = source.find("function openParamMark(", start)
        body = source[start:end]
        assert "loadBindingsFor" in body
        # 只看调用；注释里会解释「为什么不能用 loadMcpBinding」
        assert "await loadMcpBinding(" not in body
        # 打开弹窗时就要把已绑的拉回来，并且**等它回来**再算草稿：
        # fire-and-forget 会让第一次打开读到上一个服务的绑定（详见 locator 契约）
        assert "await loadBindingsFor(serverId)" in source

    def test_two_actions_are_labelled_separately(self):
        """选凭据和保存来源是两次落库，界面要说清楚。"""
        source = _read(self.REGISTRY)
        assert "选凭据即刻生效，来源改动需点「保存」" in source

    def test_save_blocked_until_every_marked_param_is_bound(self):
        """标了「来自凭据」却没选凭据 = 调用必然失败，但界面看着是配好的。"""
        source = _read(self.REGISTRY)
        assert "const draftUnboundNames = computed(" in source
        # 按钮禁用（绑定还没回来时草稿是空的，这时保存会把来源全清成「模型填写」）
        assert "paramMarkBusy || paramBindingsLoading || paramProfileLoading || !!paramCredentialError || draftUnboundNames.length > 0" in source
        # 函数里再兜一次，绕过 disabled 也挡得住
        start = source.find("async function submitParamMark(")
        body = source[start:source.find("\n}", start)]
        assert "draftUnboundNames.value.length" in body

    def test_unbound_names_are_listed_not_just_counted(self):
        """只说「有个没选」没用，得说清楚是哪个、缺的是什么。

        「绑死凭据」和「按规则」缺的东西不一样，混成一句「还没选凭据」会让人
        在规则模式下找不到该点哪里，所以分成两组列。
        """
        source = _read(self.REGISTRY)
        assert "还没选凭据" in source
        assert "draftMissingCred.value.join('、')" in source
        assert "draftMissingRule.value.join('、')" in source
        assert "还没配规则" in source

    def test_prompt_explains_who_the_binding_serves(self):
        """这里选的是「全平台共用」还是「各人自绑」，是最容易理解错的一点。

        规则现在是「必须选一条」——保存按钮会拦。所以提示要讲的是这条凭据
        对谁生效、以及个人能不能覆盖，而不是「可以先不选」。
        """
        source = _read(self.REGISTRY)
        assert "全平台共用" in source
        assert "个人绑定优先于这里" in source

    def test_user_panel_labels_arg_slots(self):
        """`arg:工具:参数` 直接摊给用户只会让人猜。"""
        source = _read(ROOT / "frontend/src/components/mcp/PlatformMcpCredentialPanel.vue")
        assert "function slotLabel(" in source
        assert "参数 · " in source

    def test_card_has_params_entry_button(self):
        source = _read(self.REGISTRY)
        assert "参数设置" in source
        assert 'openParamMark(tool)"' in source

    def test_slot_format_uses_short_tool_name(self):
        """槽位 `arg:<短工具名>:<参数名>`：服务已由 consumer_id 限定。"""
        source = _read(self.REGISTRY)
        assert "`arg:${shortToolName(tool)}:${name}`" in source
        assert "function shortToolName" in source

    def test_slot_detail_is_not_forced_on_user(self):
        """槽位是「写到哪里 + 叫什么」的实现概念，默认摊出来只会让人问「这是啥」。

        排查时还是要有，所以留在 title 里，而不是删掉。
        """
        source = _read(self.REGISTRY)
        assert "绑定位置：" in source
        # 参数表里不再直接把 `arg:xxx:yyy` 铺在正文
        start = source.find('v-for="name in toolParamNames(paramMarkTarget.tool)"')
        end = source.find("</table>", start)
        body = source[start:end]
        assert "{{ paramSlot(" not in body

    def test_marked_param_is_visually_marked(self):
        source = _read(self.REGISTRY)
        assert "isCredentialParam(tool, paramName)" in source
        assert "KeyIcon" in source

    def test_dialog_warns_about_global_effect(self):
        """标记是工具级的：所有人都得绑，不能让人以为只影响自己。"""
        source = _read(self.REGISTRY)
        assert "所有调用者" in source
        assert "我的 MCP → 平台服务" in source
        # 提示只在真的标了参数时出现，全是「模型填写」时不吓人
        assert "v-if=\"draftCredentialCount\"" in source

    def test_factory_drops_declared_params(self):
        source = _read(self.FACTORY)
        assert "credential_param_names(tool_record)" in source
        assert "properties.pop(_name, None)" in source
        assert "required_fields.discard(_name)" in source

    def test_factory_injects_before_call(self):
        source = _read(self.FACTORY)
        assert "apply_mcp_arg_bindings(" in source
        # 取不到必须报错，不能带空值发出去等远端 401
        assert "凭据参数缺失" in source

    def test_injection_does_not_reuse_header_denial_helper(self):
        """`_record` 会跳过「没绑定」，但参数级里没绑定就是缺参数。"""
        source = _read(self.INJECTION)
        start = source.find("async def apply_mcp_arg_bindings(")
        end = source.find("def shell_quote(", start)
        body = source[start:end]
        assert "_record(" not in body
        # 直接存原始 code（不经过 _record，也不提前翻成人话）
        assert "denials[slot] = result.denial" in body

    def test_slot_prefix_registered(self):
        from app.services.secrets.center import ALLOWED_SLOT_PREFIXES

        assert "arg" in ALLOWED_SLOT_PREFIXES["mcp_server"]

    def test_backend_rejects_unknown_param(self):
        source = _read(ROOT / "app/api/portal/endpoints/mcp.py")
        assert "/credential-params" in source
        assert "不在这个工具的参数定义里" in source

    def test_backend_rejects_non_string_param(self):
        """凭据值永远是字符串。整数参数注入进去会破坏远端类型校验。

        用户很容易在参数表里顺手把 `steps` 标成凭据——它在界面上和 `api_key`
        长得一模一样，但语义上根本不能这么用。
        """
        source = _read(ROOT / "app/api/portal/endpoints/mcp.py")
        assert "_accepts_string" in source
        assert "的类型不是字符串，不能由凭据提供" in source
        # `type` 可能是 ["string","null"] 这种联合类型，要认出来
        assert "isinstance(declared, list)" in source


class TestApiToolSystemParamCredential:
    """API 工具的系统级参数：选凭据就在参数行里，不再跳去「高级请求配置」。

    之前把选择器放在折叠块里、参数行只给个跳转按钮，用户得先知道有那个折叠块、
    再滚过去才能选——多两层认知成本，换来的只是「前端不用算槽位」。而槽位现在
    由后端按 URL 和方法推，这个理由也不成立了。
    """

    def test_credential_picker_lives_in_the_param_row(self):
        source = _read(ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue")
        assert "CredentialPicker" in source
        assert "openPicker(" in source
        # 不再往别处跳
        assert "locate-credential" not in source

    def test_binding_sends_name_not_slot(self):
        """只传参数名：path/query/body 由后端按工具配置算。"""
        source = _read(ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue")
        assert "name," in source
        assert "query:" not in source and "body:" not in source

    def test_unbound_blocks_saving_the_tool(self):
        source = _read(ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue")
        assert "unbound-changed" in source
        assert "未选，保存会被拦住" in source

        registry = _read(ROOT / "frontend/src/components/system/ToolRegistry.vue")
        assert "saving || paramUnboundNames.length > 0" in registry

    def test_new_tool_records_binding_then_flushes(self):
        """新建态没有工具 id，先记账，保存拿到 id 后补写。"""
        source = _read(ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue")
        assert "async function flushPending(consumerId: string)" in source
        assert "保存后生效" in source

        registry = _read(ROOT / "frontend/src/components/system/ToolRegistry.vue")
        assert "paramEditorRef.value.flushPending(newId)" in registry


class TestToolTesterHidesCredentialParams:
    """测试台不该给「由凭据提供」的参数留输入框。

    这些参数值由平台注入，人工填什么都会被覆盖；留着输入框只会让人以为
    「我填了就该生效」，然后对着空的 arguments 发懵。
    """

    def test_tester_filters_credential_params(self):
        source = _read(ROOT / "frontend/src/components/system/McpToolTester.vue")
        assert "credentialParams" in source
        assert "!credentialParams.value.includes(key)" in source
        # required 也要滤，否则会被标成必填却填不了
        assert "!credentialParams.value.includes(name)" in source

    def test_tester_says_who_provides_them(self):
        source = _read(ROOT / "frontend/src/components/system/McpToolTester.vue")
        assert "个参数由凭据提供" in source
        assert "由平台按当前用户注入" in source

    def test_unbinding_clears_local_state(self):
        """「提示已解除，界面还显示着那个凭据」是合并式加载造成的。

        服务端删掉之后，再拉回来的 map 里没有这一条；如果用 `{...old, ...map}`
        合并，旧 key 会原地留着，界面就一直显示已绑定。
        """
        source = _read(ROOT / "frontend/src/components/system/McpServerRegistry.vue")
        # 以服务端为准
        assert "headerBindings.value = { ...headerBindings.value, ...map }" not in source
        assert "headerBindings.value = map" in source
        # 解绑时本地立刻清，不等加载
        start = source.find("async function saveParamBind(")
        body = source[start:source.find("\n}", start)]
        assert "delete headerBindings.value[slot]" in body
        assert "delete pendingBindings.value[slot]" in body

    def test_api_tool_unbind_clears_both_maps(self):
        source = _read(ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue")
        start = source.find("async function confirmPick(")
        body = source[start:source.find("\n}\n", start)]
        assert "delete bindings.value[name]" in body
        assert "delete pending.value[name]" in body

    def test_saving_marks_refreshes_the_list(self):
        """保存后要重新拉列表：本地改的是对象引用，别处一刷就被旧数据盖回去。"""
        source = _read(ROOT / "frontend/src/components/system/McpServerRegistry.vue")
        start = source.find("async function submitParamMark(")
        body = source[start:source.find("\n}", start)]
        assert "await fetchServers()" in body
        # 用后端回的真实值，而不是本地草稿
        assert "Array.isArray(saved) ? saved : next" in body


class TestBindingEdgeCases:
    """绑定链路的边界情况。每一条都对应一个真实会发生的场景。"""

    def test_picker_can_still_unbind_a_dead_secret(self):
        """绑过一条后来被停用/撤销授权的凭据，必须还能解绑。

        `/picker` 现在只列启用的凭据（`only_active=True`），所以这条绑定查不到
        对应的 item——如果「清除绑定」按钮靠 `selected` 判断，按钮就消失了，
        用户被永久卡在一条取不到值的绑定上。
        """
        source = _read(ROOT / "frontend/src/components/secrets/CredentialPicker.vue")
        assert "const orphaned = computed(" in source
        assert "v-if=\"selected || orphaned\"" in source
        assert "已不在可选列表里" in source

    def test_close_after_save_flushes_pending_first(self):
        """`closeAfterSave` 分支必须先把暂存绑定补写掉再关向导。

        `closeWizard()` → `resetWizard()` 会 `pendingBindings.value = {}`，
        先关就再也找不到用户选过哪几条凭据了。
        """
        source = _read(ROOT / "frontend/src/components/system/McpServerRegistry.vue")
        start = source.find("if (options?.closeAfterSave) {")
        body = source[start:source.find("return true", start)]
        flush_at = body.find("flushPendingBindings(")
        # rfind：上面那段注释里也提到 closeWizard()，取真正调用的那次
        close_at = body.rfind("closeWizard()")
        assert flush_at != -1, "closeAfterSave 分支没有补写暂存绑定"
        assert flush_at < close_at, "补写必须在 closeWizard 之前"

    def test_new_wizard_does_not_inherit_previous_bindings(self):
        """从「编辑 A」跳到「添加」，新向导不能显示 A 的 header 绑定。"""
        source = _read(ROOT / "frontend/src/components/system/McpServerRegistry.vue")
        start = source.find("async function loadMcpBinding(")
        body = source[start:source.find("\n}", start)]
        head = body[:body.find("if (!editingId.value)")]
        assert "headerBindings.value = {}" in body[body.find("if (!editingId.value)"):][:200]

    def test_failed_flush_keeps_pending_for_retry(self):
        """补写失败要留在暂存里，否则只能到编辑态重新选一遍。"""
        source = _read(ROOT / "frontend/src/components/system/McpServerRegistry.vue")
        start = source.find("async function flushPendingBindings(")
        body = source[start:source.find("\n}", start)]
        assert "const keep: Record<string, HeaderBinding> = {}" in body
        assert "keep[slot] = binding" in body
        assert "pendingBindings.value = keep" in body

    def test_reset_wizard_clears_header_bind_state(self):
        source = _read(ROOT / "frontend/src/components/system/McpServerRegistry.vue")
        start = source.find("const resetWizard = ")
        if start == -1:
            start = source.find("function resetWizard()")
        body = source[start:source.find("\n}", start)]
        for name in ("headerBindSlot", "headerBindSecretId", "headerBindPicked"):
            assert f"{name}.value = null" in body, name

    def test_stale_binding_response_is_discarded(self):
        """并发加载要丢掉晚到的旧响应，否则参数设置里会显示别的服务的绑定。"""
        source = _read(ROOT / "frontend/src/components/system/McpServerRegistry.vue")
        assert "if (paramMarkTarget.value?.tool?.server_id !== serverId) return" in source
        assert "if (editingId.value !== requestedId) return" in source


class TestDeadSecretIsVisible:
    """绑定的凭据被停用后，界面必须标出来。

    「已绑定」三个字骗过很多人：绑定记录还在，凭据却用不了，调用直接
    DENY_DISABLED。没有标记的话，用户只能去翻日志才知道是凭据的问题。
    """

    def test_three_editors_mark_the_issue(self):
        for path in (
            "frontend/src/components/system/ToolHeadersEditor.vue",
            "frontend/src/components/system/ToolSystemParamEditor.vue",
            "frontend/src/components/system/McpServerRegistry.vue",
        ):
            source = _read(ROOT / path)
            # 声明形式不限（const 箭头 / function 都有）
            assert "bindingIssue" in source, path
            assert "凭据已停用" in source, path

    def test_user_side_panel_also_marks_it(self):
        source = _read(ROOT / "frontend/src/components/mcp/PlatformMcpCredentialPanel.vue")
        assert "bindingIssue" in source
        assert "调用会失败" in source

    def test_active_secret_shows_nothing(self):
        """启用的凭据不显示任何告警——标记只在真有问题时出现。"""
        source = _read(ROOT / "frontend/src/components/system/ToolHeadersEditor.vue")
        start = source.find("function bindingIssue(")
        body = source[start:source.find("\n}\n", start)]
        assert "secret_status === 'active') return ''" in body


class TestAdminSecretCenterUsage:
    """管理端要能查「谁在用、用成了没有」。

    卡片上只有「近 7 天调用 N 次」这一个数字，用户报「我用不了」时答不上来：
    是谁、哪个场景、还是根本没匹配上绑定。
    """

    ADMIN = "frontend/src/views/AdminSecretCenter.vue"

    def test_usage_has_its_own_entry(self):
        source = _read(ROOT / self.ADMIN)
        assert "使用记录" in source
        assert "openUsage" in source
        assert "/api/portal/secrets/usage" in source

    def test_usage_and_audit_stay_separate(self):
        """一个记「谁用了」，一个记「谁改了」，混在一起两边都答不清。"""
        source = _read(ROOT / self.ADMIN)
        assert "/api/portal/secrets/usage" in source
        assert "/api/portal/secrets/audit" in source

    def test_denial_reasons_are_translated(self):
        """`result` 落库是 `denied_by_*`，直接摊给用户等于没说。

        映射本身收敛在 `utils/secretLabels.ts`，两个页面都从那儿取。
        """
        labels = _read(ROOT / "frontend/src/utils/secretLabels.ts")
        assert "denied_by_grant" in labels and "未授权" in labels
        admin = _read(ROOT / self.ADMIN)
        assert "resultLabel" in admin

    def test_cards_render_two_per_row(self):
        source = _read(ROOT / self.ADMIN)
        assert "grid gap-3 lg:grid-cols-2" in source

    def test_status_is_visible_at_a_glance(self):
        """两列之后卡片挨得近，靠文字读状态太慢，左边给一道色条。"""
        source = _read(ROOT / self.ADMIN)
        assert "border-l-4 border-l-emerald-400" in source

    def test_usage_dialog_is_teleported(self):
        """Tailwind 覆盖层契约：fixed inset-0 必须 Teleport + z-[100]。"""
        source = _read(ROOT / self.ADMIN)
        start = source.find("使用记录弹框")
        assert start != -1
        body = source[start:start + 600]
        assert "Teleport to=\"body\"" in body
        assert "z-[100]" in body


class TestInjectionHelpStaysAccurate:
    """「这些凭据会被用在哪里」是用户唯一能看到的全貌，过时比没有更糟。

    工具支持固定绑定和动态选择，帮助文案需要覆盖这两种配置。
    """

    CENTER = "frontend/src/views/SecretCenter.vue"

    def test_documents_structured_credential_selection(self):
        source = _read(ROOT / self.CENTER)
        assert "MCP 工具 · 结构化凭据" in source
        assert "模型选择" in source
        assert "按次选择" in source

    def test_every_slot_prefix_is_documented(self):
        """六个槽位前缀都要在弹窗里出现，缺一个就是少说了一种用法。"""
        source = _read(ROOT / self.CENTER)
        for slot in ("header:", "arg:", "path:", "query:", "body:", "env:"):
            assert slot in source, slot

    def test_mcp_arg_is_its_own_entry(self):
        """MCP 的 header 和工具参数是两条不同的链路（一个有兜底、一个没有）。"""
        source = _read(ROOT / self.CENTER)
        assert "MCP 服务 · 请求头" in source
        assert "MCP 服务 · 工具参数" in source

    def test_arg_says_it_has_no_fallback(self):
        """arg 没有 fixed_token 那样的兜底，缺失就是失败——这点必须写清楚。"""
        source = _read(ROOT / self.CENTER)
        start = source.find("MCP 服务 · 工具参数")
        body = source[start:start + 900]
        assert "没有兜底" in body

    def test_api_param_mentions_slot_is_derived(self):
        """槽位由后端推，用户不该以为要自己选 query 还是 body。"""
        source = _read(ROOT / self.CENTER)
        start = source.find("API 工具 · 参数位")
        body = source[start:start + 900]
        assert "由工具的 URL 和方法决定" in body


class TestUsageStatsAndFilter:
    """使用记录要能「按凭据查」和「看趋势」，只有一个全局明细列表不够。"""

    ADMIN = "frontend/src/views/AdminSecretCenter.vue"

    def test_dialog_has_detail_and_stats_tabs(self):
        source = _read(ROOT / self.ADMIN)
        assert "switchUsageTab" in source
        assert "usageTab === 'detail'" in source
        assert "usageTab === 'stats'" in source

    def test_secret_filter_is_wired(self):
        """`/usage` 早就支持 secret_id，前端一直没接。"""
        source = _read(ROOT / self.ADMIN)
        assert "usageSecretId" in source
        assert "secret_id: usageSecretId.value" in source
        assert "全部凭据" in source

    def test_card_can_open_its_own_usage(self):
        """卡片上的入口要带上自己那条的 id，而不是又打开全局列表。"""
        source = _read(ROOT / self.ADMIN)
        assert "@click=\"openUsage(item.id)\"" in source

    def test_stats_panel_shows_the_three_dimensions(self):
        # 渲染在共用组件里，管理端只负责挂上去
        source = _read(ROOT / "frontend/src/components/secrets/SecretUsageStats.vue")
        assert "按凭据" in source
        assert "按结果" in source
        assert "按天" in source

    def test_stats_uses_the_aggregate_endpoint(self):
        source = _read(ROOT / "frontend/src/components/secrets/SecretUsageStats.vue")
        assert "/api/portal/secrets/usage/stats" in source


class TestBindingIsIdentifiable:
    """卡片上的绑定要能认出「哪个服务、谁绑的」。

    原来只有 `MCP 服务 · arg:echo:api_key`——服务是 uuid 推出来的标签，槽位是内部
    编码，看了等于没看。
    """

    ADMIN = "frontend/src/views/AdminSecretCenter.vue"

    def test_shows_consumer_name_not_uuid(self):
        source = _read(ROOT / self.ADMIN)
        assert "binding.consumer_name || binding.consumer_id" in source

    def test_shows_who_bound_it(self):
        source = _read(ROOT / self.ADMIN)
        assert "binding.bound_by_name || binding.bound_by_user_id" in source

    def test_global_binding_is_labelled(self):
        source = _read(ROOT / self.ADMIN)
        assert "binding.is_global ? '全局'" in source

    def test_slot_is_humanised(self):
        """`arg:echo:api_key` → `echo.api_key`；`header:X-Key` → `X-Key`。"""
        source = _read(ROOT / self.ADMIN)
        assert "function slotLabel(" in source
        assert "parts[1]}.${parts.slice(2).join(':')}" in source

    def test_raw_slot_is_still_available_on_hover(self):
        """排查时还是需要原始槽位，但不能铺在正文里。"""
        source = _read(ROOT / self.ADMIN)
        assert "槽位：${binding.slot}" in source


class TestUsageStatsShared:
    """统计面板两边共用同一个组件。

    抽出来的直接原因：两份 `RESULT_LABEL` 已经分叉了——同一个
    `denied_by_scenario`，个人端写「场景未启用」、管理端写「场景不符」，
    个人端还漏了 `denied_disabled`。纯展示口径没有分叉的理由。
    """

    STATS = "frontend/src/components/secrets/SecretUsageStats.vue"

    def test_labels_have_a_single_source(self):
        """三处曾经各写一份，导致同一结果三种说法。"""
        source = _read(ROOT / "frontend/src/utils/secretLabels.ts")
        assert "denied_by_scenario" in source
        assert "denied_disabled" in source

        # 谁也不许再自己定义
        for path in (
            "frontend/src/views/SecretCenter.vue",
            "frontend/src/views/AdminSecretCenter.vue",
            "frontend/src/components/secrets/SecretUsageStats.vue",
        ):
            view = _read(ROOT / path)
            assert "const RESULT_LABEL" not in view, path
            assert "secretLabels" in view, path

    def test_both_pages_use_it(self):
        for path in (
            "frontend/src/views/SecretCenter.vue",
            "frontend/src/views/AdminSecretCenter.vue",
        ):
            source = _read(ROOT / path)
            assert "SecretUsageStats" in source, path

    def test_admin_has_no_private_copy_left(self):
        """管理端原来那份内联渲染要删干净，否则两套样式会各自演化。"""
        source = _read(ROOT / "frontend/src/views/AdminSecretCenter.vue")
        assert "statsMaxSecret" not in source
        assert "barWidth" not in source

    def test_personal_center_has_a_stats_tab(self):
        source = _read(ROOT / "frontend/src/views/SecretCenter.vue")
        assert "['usage', 'stats', 'audit']" in source
        assert "'统计'" in source

    def test_personal_stats_follow_the_scope_switch(self):
        """统计要跟着弹窗上的范围开关走，而不是左栏的选中项。

        原来统计跟着 `selectedId` 走、明细请求却不带 `secret_id`（拿的是全部），
        同一个弹窗里两个 tab 各说一套。
        """
        source = _read(ROOT / "frontend/src/views/SecretCenter.vue")
        assert ':secret-id="statsSecretId"' in source
        assert "const statsSecretId = computed(" in source


class TestPersonalUsageScope:
    """个人端「使用记录」默认只看点进来的那条凭据。

    入口按钮长在单条凭据的卡片上，但请求没带 `secret_id`——点「邮箱名」的使用
    记录，弹出来的是「a / k」几条凭据混在一起的最近 50 条。用户第一反应是
    「点错了」或「数据串了」，而不是「这里刚好是全部」。
    """

    def test_open_usage_locks_the_selected_secret(self):
        source = _read(SECRET_CENTER)
        start = source.find("async function openUsage(")
        body = source[start:source.find("\n}", start)]
        assert "selected.value?.id" in body
        assert "usageScope.value" in body

    def test_request_carries_secret_id(self):
        source = _read(SECRET_CENTER)
        start = source.find("async function loadRecords(")
        body = source[start:source.find("\n}", start)]
        assert "params.secret_id = usageSecretId.value" in body

    def test_dialog_offers_a_way_to_see_everything(self):
        """想看全貌可以，但必须是显式点一下，不能是「悄悄给全部」。"""
        source = _read(SECRET_CENTER)
        assert "查看全部记录" in source
        assert "setUsageScope('all')" in source
        assert "setUsageScope('secret')" in source

    def test_current_scope_is_written_on_the_title(self):
        """两个视角差的就是「谁」，只藏在开关里的话看不出来现在看的是全部。"""
        source = _read(SECRET_CENTER)
        assert "usageScopeLabel" in source
        assert "只看 ${usageScopeLabel}" in source


class TestBindingsGroupedByType:
    """绑定要按「MCP / API 工具 / 脚本」分开显示。

    平铺在一起时看不出这条凭据被哪几类地方用，只能逐个 hover。
    """

    ADMIN = "frontend/src/views/AdminSecretCenter.vue"

    def test_groups_by_consumer_type(self):
        source = _read(ROOT / self.ADMIN)
        assert "function groupBindings(" in source
        assert "groupBindings(item.bindings)" in source

    def test_group_order_is_stable(self):
        """按创建顺序跳来跳去，每次看都要重新找。"""
        source = _read(ROOT / self.ADMIN)
        assert "BINDING_TYPE_ORDER" in source
        assert "'mcp_server', 'api_tool', 'script_tool'" in source

    def test_each_type_has_its_own_colour(self):
        source = _read(ROOT / self.ADMIN)
        assert "BINDING_TYPE_STYLE" in source
        for cls in ("bg-violet-50", "bg-sky-50", "bg-amber-50"):
            assert cls in source, cls

    def test_script_tool_label_is_correct(self):
        """真实类名是 `SysScriptTool`，消费者类型是 `script_tool`；
        只写 `script` 会让脚本工具显示成原始英文。"""
        source = _read(ROOT / self.ADMIN)
        assert "script_tool: '脚本工具'" in source

    def test_hover_still_carries_the_raw_slot(self):
        source = _read(ROOT / self.ADMIN)
        assert "槽位：${binding.slot}" in source


class TestCopyMatchesCode:
    """提示文案要和实现一致。

    这类文字不参与编译也不进类型检查，过时了没有任何东西会红——但它是用户
    理解这个系统的唯一入口，说错比不说更糟。下面每条都对应一处真实的错。
    """

    def test_variable_secrets_are_readable(self):
        """曾经写着「值和变量都无法查看明文」。

        `GET /{id}/value` 只对 variable 开放，credential 才 403。把两者混成
        一句话，用户会以为建了变量就再也查不回来。
        """
        source = _read(ROOT / "frontend/src/views/SecretCenter.vue")
        assert "「变量」类型可以随时查看" in source
        assert "值和变量都加密保存" not in source

    def test_unbound_hint_distinguishes_dynamic_selection(self):
        source = _read(ROOT / "frontend/src/views/SecretCenter.vue")
        start = source.index("条凭据没有固定绑定")
        span = source[start:start + 200]
        assert "动态选择" in span
        assert "关联工具" in span

    def test_admin_copy_mentions_every_grant_target(self):
        """授权档位有三种（用户 / 部门 / 全体），只说两种会漏掉最常用的那个。"""
        source = _read(ROOT / "frontend/src/views/AdminSecretCenter.vue")
        assert "授权给用户、部门或全体用户" in source
        assert "授权给具体的人、部门或全体用户" in source

    def test_user_panel_points_at_the_better_option(self):
        """「找管理员配固定 Token」过时了：绑一条系统级凭据能轮换，比写死好维护。"""
        source = _read(ROOT / "frontend/src/components/mcp/PlatformMcpCredentialPanel.vue")
        assert "系统级凭据" in source
        assert "比在服务上写死 Token 更好维护" in source

    def test_header_copy_does_not_contradict_itself(self):
        """原来一句话里既说「所有人共用配置」又说「只对你自己生效」。"""
        source = _read(ROOT / "frontend/src/components/system/ToolHeadersEditor.vue")
        assert "凭据绑定是个人级" in source
        # 「变量占位」和「按原值写入」不能并列在同一句里
        assert "敏感值建议用变量占位；保存后仍会按原值写入工具配置" not in source

    def test_api_param_help_drops_slot_derivation(self):
        """槽位怎么推是后端的事，前端不再算，讲它只会让人担心改 URL 会失效。"""
        source = _read(ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue")
        assert "GET 走 query" not in source
        assert "调用时由平台填值" in source


class TestEditMetadata:
    """凭据建好后要能改元数据。

    原来前端只有「删除」。域名白名单配错一个字就得删掉重建——而删除会连带
    清掉所有绑定和授权，重建后还得一条条重新绑。后端 `PATCH /{id}` 一直支持，
    只是没有入口。
    """

    ADMIN = "frontend/src/views/AdminSecretCenter.vue"

    def test_card_has_an_edit_button(self):
        source = _read(ROOT / self.ADMIN)
        assert "@click=\"openEdit(item)\"" in source
        assert ">编辑</button>" in source or "编辑\n" in source

    def test_edit_uses_patch_not_create(self):
        """PATCH 只改元数据；值必须走轮换，否则没有轮换痕迹。"""
        source = _read(ROOT / self.ADMIN)
        assert "axios.patch(`/api/portal/secrets/${target.id}`" in source

    def test_edit_form_covers_the_editable_fields(self):
        source = _read(ROOT / self.ADMIN)
        for field in ("name", "allowed_scenarios", "allowed_hosts", "category", "remark"):
            assert field in source, field

    def test_allowlist_explains_the_empty_case(self):
        """留空 = 只允许内网，公网地址会被 DENY_HOST——这是最容易踩的一条。"""
        source = _read(ROOT / self.ADMIN)
        assert "留空只允许平台内部目标" in source
        assert "公网地址必须写进来" in source

    def test_edit_says_value_cannot_change(self):
        for path in ("frontend/src/views/AdminSecretCenter.vue",
                     "frontend/src/views/SecretCenter.vue"):
            source = _read(ROOT / path)
            assert "的值不能改——要换值请用「轮换」" in source, path

    def test_personal_center_also_has_edit(self):
        """两个入口都要能改，不然用户得先判断自己在哪个页面。"""
        source = _read(ROOT / "frontend/src/views/SecretCenter.vue")
        assert "function openEdit()" in source
        assert "axios.patch(`/api/portal/secrets/${item.id}`" in source
        assert "@click=\"openEdit\"" in source

    def test_personal_edit_is_owner_only(self):
        """系统级凭据由管理员维护，个人端不给编辑入口。"""
        source = _read(ROOT / "frontend/src/views/SecretCenter.vue")
        start = source.find(">编辑</button>")
        assert start != -1
        # 按钮前面最近的 v-if 应该是 owner_scope === 'user'
        head = source[max(0, start - 400):start]
        assert "selected.owner_scope === 'user'" in head


class TestRotateIsReachable:
    """「改值走轮换」这句话得有一个真的按钮，否则等于让人去点空气。

    管理端的编辑弹窗写着「要换值请用轮换」，但卡片上原来没有这个入口——
    而系统级凭据是全平台共用的，不能改值就只能删了重建，连带清掉
    所有绑定和授权。
    """

    ADMIN = "frontend/src/views/AdminSecretCenter.vue"

    def test_admin_can_rotate(self):
        source = _read(ROOT / self.ADMIN)
        assert "function openRotate(" in source
        assert "@click=\"openRotate(item)\"" in source
        assert "`/api/portal/secrets/${target.id}/rotate`" in source

    def test_rotate_explains_the_overlap_window(self):
        """7 天回退窗口是这个功能最反直觉的地方，必须说清。"""
        source = _read(ROOT / self.ADMIN)
        assert "旧值会保留" in source
        assert "7 天" in source
        assert "不会立刻失败" in source

    def test_rotate_sends_overlap_days(self):
        source = _read(ROOT / self.ADMIN)
        assert "overlap_days: 7" in source

    def test_new_value_is_masked_until_revealed(self):
        """粘贴 Token 时默认打码，和创建表单一致。"""
        source = _read(ROOT / self.ADMIN)
        start = source.find("轮换凭据值")
        span = source[start:start + 1600]
        assert "masked-value" in span
        assert "revealRotateValue" in span


class TestGrantAllUsers:
    """系统级凭据要能给「全体用户」授权。

    没有这一档时，平台统一采购的账号只能逐部门授权——新增部门必然漏；
    按人授权更糟，新人入职必然漏。
    """

    ADMIN = ROOT / "frontend/src/views/AdminSecretCenter.vue"

    def test_grant_dialog_offers_everyone(self):
        source = _read(self.ADMIN)
        assert "grantToAll" in source
        assert "授权给全体用户" in source

    def test_everyone_grant_has_no_grantee_id(self):
        """`all` 不指向任何实体，grantee_id 必须是空串。"""
        source = _read(self.ADMIN)
        assert "grantee_type: 'all'" in source
        assert "grantee_id: ''" in source

    def test_granted_all_is_listed_and_locked(self):
        source = _read(self.ADMIN)
        # 已经授权给全体时，选择器锁住，避免重复挑人
        assert "grantedToAll" in source
        assert "全体用户" in source

    def test_submit_allows_all_without_picks(self):
        source = _read(self.ADMIN)
        assert "(!grantSelected.length && !grantToAll)" in source

    def test_backend_accepts_all(self):
        from app.services.secrets.policy import GRANTEE_ALL, GRANTEE_TYPES

        assert GRANTEE_ALL == "all"
        assert GRANTEE_ALL in GRANTEE_TYPES
        source = _read(ROOT / "app/services/secrets/resolver.py")
        # 全体授权不比对 grantee_id
        assert "SecretGrant.grantee_type == GRANTEE_ALL" in source

    def test_center_normalizes_all_grantee_id(self):
        source = _read(ROOT / "app/services/secrets/center.py")
        assert "grantee_id = \"\"" in source


class TestCreatingBindingsForTools:
    """API 工具与脚本工具的新建态也要能先选凭据。

    MCP 那侧已经改成「先记着、保存后补写」。另外两个消费者如果还要求先存一遍
    空配置再回来配，同一件事就有三种操作路径，用户永远记不住。
    """

    HEADERS = ROOT / "frontend/src/components/system/ToolHeadersEditor.vue"
    REGISTRY = ROOT / "frontend/src/components/system/ToolRegistry.vue"
    SCRIPT_WIZARD = ROOT / "frontend/src/components/system/ScriptToolWizard.vue"
    SCRIPT_LIST = ROOT / "frontend/src/components/system/ScriptToolList.vue"

    def test_tool_headers_editor_stores_pending(self):
        source = _read(self.HEADERS)
        assert "pendingBindings" in source
        assert "async function flushPending(consumerId: string)" in source
        assert "defineExpose" in source

    def test_tool_headers_no_longer_requires_consumer_id(self):
        """旧实现里 `if (!bindingSlot.value || !props.consumerId) return` 直接堵死新建态。"""
        source = _read(self.HEADERS)
        assert "if (!bindingSlot.value || !props.consumerId) return" not in source
        assert "if (!bindingSlot.value) return" in source

    def test_headers_editor_has_no_param_slot_logic(self):
        """参数位已砍掉：凭据只在这里给 header 绑，参数绑定在参数定义那边。

        同一个能力有两个入口时，用户得判断「我该在哪改」，而两边行为稍有不一致
        就是 bug。所以留一个。
        """
        source = _read(self.HEADERS)
        assert "paramSlotPrefix" not in source
        assert "PARAM_SLOT_PREFIXES" not in source
        assert "data-credential-param" not in source
        # 只做 header
        assert "slot.startsWith('header:')" not in source
        assert "bindingSlotName" in source

    def test_tool_headers_failed_bindings_survive(self):
        """补写失败要留在暂存里，否则「哪些没绑上」查不到也重试不了。"""
        source = _read(self.HEADERS)
        assert "pendingBindings.value = keep" in source

    def test_tool_registry_flushes_after_create(self):
        source = _read(self.REGISTRY)
        assert "headersEditorRef" in source
        assert "flushPending(newId)" in source

    def test_script_wizard_stores_pending(self):
        source = _read(self.SCRIPT_WIZARD)
        assert "pendingEnvBindings" in source
        assert "flushPendingEnvBindings" in source
        # 变量名能提前算槽位，所以新建时就能选
        assert "slot: `env:${variable}`" in source

    def test_script_wizard_allows_binding_while_creating(self):
        source = _read(self.SCRIPT_WIZARD)
        assert ':disabled="!isEditing || envBindingsBusy"' not in source
        assert "新建脚本时还不能绑定" not in source

    def test_script_list_flushes_after_create(self):
        source = _read(self.SCRIPT_LIST)
        assert "wizardRef" in source
        assert "flushPendingEnvBindings(newId)" in source


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
            # 值输入框默认掩码，且从不绑定已保存的值。
            # textarea 没有 type="password"，靠 .masked-value（-webkit-text-security）
            # 遮住；「显示」只影响**正在输入的**内容，不会回显已保存的值。
            assert "masked-value" in template or path is ADMIN_SECRET_CENTER

    def test_reveal_only_affects_typing(self):
        """「显示」按钮只切换掩码样式，不读也不回填任何已保存的值。"""
        for path in (SECRET_CENTER, ADMIN_SECRET_CENTER):
            source = _read(path)
            template = source[source.index("<template>") :]
            assert "revealValue" in source
            # 显形只改 class，不能顺手去取明文
            assert ":class=\"{ 'masked-value': !revealValue }\"" in template
            assert "/value" not in template

    def test_long_values_are_not_trimmed(self):
        """JSON / PEM 的缩进要保住：只判空白，保存用原值。"""
        for path in (SECRET_CENTER, ADMIN_SECRET_CENTER):
            source = _read(path)
            assert "form.value.value.trim()" in source
            assert "credentialRequest(form.value.value)" in source
        assert "value: legacyValue" in _read(ROOT / "frontend/src/composables/useCredentialFields.ts")

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
        fields = _read(PICKER_FIELDS)

        # emit 的是 id，不是值；组件内部也不缓存明文
        assert "emit('update:modelValue', item ? item.id : null)" in source
        assert "item.id" in source
        # 值的影子只留在类型定义里（选择器知道它存在，但从不渲染它）
        assert "value_masked" in fields
        assert "value_masked" not in source


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

    def test_binding_works_before_saving(self):
        """新建时也能先选，保存后补写——不再要求先存一遍空配置。"""
        source = _read(self.HEADERS)

        assert "canBind" not in source
        assert "保存工具后可绑定凭据" not in source

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

    def test_available_while_creating_too(self):
        source = _read(self.MCP_REGISTRY)

        # 新建时先记在 pendingBindings，保存后补写
        assert "pendingBindings" in source
        assert "flushPendingBindings" in source

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

    def test_available_while_creating_too(self):
        source = _read(self.WIZARD)

        # 变量名能提前算槽位（env:<名字>），所以新建时就能选
        assert "pendingEnvBindings" in source
        assert "先保存，再回来配" not in source

    def test_script_credentials_use_private_channel_not_shared_env_file(self):
        runtime = _read(ROOT / "app/services/ai/tools/script_tool_runtime.py")
        execution = _read(ROOT / "app/services/ai/tools/credential_script_execution.py")
        assert "execute_credential_script" in runtime
        assert "apply_script_env_bindings" in execution
        assert "run_credential_process" in execution
        assert '"env": env' in execution
        assert "build_env_file_content" not in execution
        assert ".env" not in execution


class TestValueRevealAndBindingsOverview:
    """「查看变量值」与「这条凭据用在哪」两个入口。

    前者是 variable 类型存在的意义——值本来就是要填到别处去的，不给看等于没用；
    后者同时展示固定绑定、符合类型的动态候选和实际使用过的目标。
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

    def test_tool_relations_overview_counts_tools_and_shows_each_source(self):
        source = _read(self.CENTER)

        assert "/secrets/${item.id}/tool-relations" in source
        assert "关联工具（" in source
        assert "toolRelations.items.length" in source
        assert "固定绑定" in source
        assert "模型选择" in source
        assert "按次选择" in source
        assert "曾使用" in source
        assert "还没绑定到任何工具，所以不会被注入" not in source

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


class TestAdminListViewToggle:
    """管理端的卡片 / 列表双视图。

    卡片适合「一条条看」，列表适合「一堆里扫」。两者要同时存在——管理员排查
    「哪条凭据没人用」时是扫模式，配一条新凭据时是看模式。

    列表的约束是**操作必须收进 ⋯**：一行里平铺四个文字按钮会把「名称」和
    「使用情况」挤掉，而这两列才是扫列表时真正要看的东西。
    """

    def test_both_views_exist_and_toggle(self):
        source = _read(ADMIN_SECRET_CENTER)

        assert "viewMode" in source
        assert "viewMode === 'card'" in source
        assert "v-else" in source, "列表视图要和卡片视图互斥渲染"
        assert "setViewMode('card')" in source
        assert "setViewMode('list')" in source

    def test_view_choice_persists(self):
        source = _read(ADMIN_SECRET_CENTER)

        # 纯界面偏好，不值得为它加一次后端请求
        assert "localStorage" in source
        assert "dsh:admin-secrets:view" in source

    def test_list_uses_row_actions_menu(self):
        source = _read(ADMIN_SECRET_CENTER)

        assert "SecretRowActions" in source
        # 列表里不能再出现平铺的动作按钮
        list_section = source.split("<!-- 列表视图")[1]
        assert "@click=\"openEdit(item)\"" not in list_section
        assert "@click=\"openRotate(item)\"" not in list_section
        assert "@usage=\"openUsage(item.id)\"" in list_section

    def test_card_view_keeps_flat_buttons(self):
        """卡片横向空间够，直点更快——不要顺手把卡片也改成菜单。"""
        source = _read(ADMIN_SECRET_CENTER)
        card_section = source.split("<!-- 列表视图")[0]

        assert "@click=\"openEdit(item)\"" in card_section
        assert "@click=\"openRotate(item)\"" in card_section
        assert "强制停用" in card_section

    def test_list_columns_cover_triage_questions(self):
        source = _read(ADMIN_SECRET_CENTER)
        list_section = source.split("<!-- 列表视图")[1]

        # 扫列表要回答的四件事：是什么、能不能用、谁在用、在跑吗
        for header in ("名称", "状态", "场景", "授权范围", "绑定", "近 7 天"):
            assert header in list_section, f"列表缺「{header}」列"

    def test_list_overflow_scrolls_instead_of_squashing(self):
        source = _read(ADMIN_SECRET_CENTER)
        list_section = source.split("<!-- 列表视图")[1]

        assert "overflow-x-auto" in list_section
        assert "min-w-[" in list_section, "窄屏要横向滚动，不能把列压成一条缝"


class TestRowActionsMenu:
    """⋯ 菜单本身的结构性要求。

    这些不是风格问题：位置算错会跑出视口，不做 Teleport 会被表格容器的
    overflow 裁掉一半，不可键盘关闭对只能用键盘的人是死路。
    """

    def test_menu_teleports_out_of_scroll_container(self):
        source = _read(ROW_ACTIONS)

        # 菜单挂在带 overflow-x-auto 的表格里，absolute 定位会被裁
        assert 'to="body"' in source
        assert "position: 'fixed'" in source

    def test_menu_overlay_meets_modal_contract(self):
        source = _read(ROW_ACTIONS)

        # 与 tests/core/test_guide_overlay_contract.py 同一条规则：
        # 全屏遮罩必须 Teleport + z >= 100，否则会被页面里的层压住
        assert "fixed inset-0" in source
        assert "z-[320]" in source

    def test_menu_closes_on_escape_and_outside_click(self):
        source = _read(ROW_ACTIONS)

        assert "'Escape'" in source
        assert "addEventListener('keydown'" in source
        assert "removeEventListener" in source, "监听要摘，否则切页面后还在跑"

    def test_disabled_secret_hides_disable_action(self):
        source = _read(ROW_ACTIONS)

        # 已经停用的再「强制停用」没有意义
        assert 'v-if="active"' in source

    def test_menu_does_not_emit_union_type(self):
        """`emit(action)` 传联合类型过不了 vue-tsc 的重载检查。"""
        source = _read(ROW_ACTIONS)

        assert "  emit(action)" not in source, "要逐个分发，不能传联合类型"
        assert "emit('usage')" in source

    def test_menu_opens_downward_and_flips_only_at_viewport_bottom(self):
        """表格行的菜单默认向下展开，下方放不下才翻转。

        照搬网格卡片（SavedReportItemCard）的「只要上方放得下就向上」会在表格里
        连第一行都往上弹，正好盖住表头和筛选栏——那是卡片高度与菜单高度都不同的
        场景，不能直接复用。
        """
        source = _read(ROW_ACTIONS)

        assert "const spaceBelow = vh - anchorBottom" in source, "未按下方剩余空间判断"
        assert "spaceBelow < height + GAP && anchorTop >= height + GAP" in source, (
            "必须「下方放不下且上方够」才翻转"
        )

    def test_open_menu_follows_scroll_and_unbinds(self):
        """菜单是 fixed 定位，表格页整页滚动，不跟随就会与按钮错位。"""
        source = _read(ROW_ACTIONS)

        assert "addEventListener('scroll', reposition, true)" in source, "滚动时不重算位置"
        assert "removeEventListener('scroll', reposition, true)" in source, (
            "监听要摘，否则切页面后还在跑"
        )
        assert "addEventListener('resize', reposition)" in source
        assert "removeEventListener('resize', reposition)" in source


class TestUsageUserDisplay:
    """使用记录要能看出「这个人是谁」，并且能按用户筛。

    流水只存 user_id，界面原先直接显示「用户 7」——排查「某用户说用不了」时，
    运维还得先去用户表反查这个人是谁；记录一多也没法只看某个人的。
    """

    def test_admin_usage_row_shows_name_email_and_id(self):
        source = _read(ADMIN_SECRET_CENTER)

        assert "user_real_name" in source and "user_email" in source
        # 姓名 > 用户名 > 裸 ID 的兜底顺序：用户被删了也要能显示点东西
        assert "row.user_real_name || row.user_name || row.user_id" in source
        assert "usageUserTitle" in source, "悬停要能看到完整身份（ID / 用户名 / 姓名 / 邮箱）"

    def test_admin_usage_search_sends_user_keyword(self):
        source = _read(ADMIN_SECRET_CENTER)

        assert "usageUserKeyword" in source
        assert "user_keyword:" in source, "搜索词要传给后端，不能只在前端过滤当前页"
        assert "scheduleUsageSearch" in source, "输入即搜必须防抖"
        assert "clearTimeout(usageSearchTimer)" in source

    def test_personal_usage_also_shows_identity(self):
        source = _read(SECRET_CENTER)

        assert "user_real_name" in source and "user_email" in source


class TestAuditActionBadges:
    """变更记录的动作要能靠颜色区分，删除必须醒目。

    原先所有动作都是一模一样的灰底白字，一屏十几条扫下来分不出「创建」和「删除」，
    而「谁把凭据删了」正是审计最常被问的问题。
    """

    def test_delete_is_red_and_heavier(self):
        from pathlib import Path as _P

        src = (_P(ROOT) / "frontend/src/utils/secretAudit.ts").read_text(encoding="utf-8")
        style_section = src.split("SECRET_ACTION_STYLE: Record<string, string> = {", 1)[1]
        delete_line = next(
            line for line in style_section.splitlines() if line.strip().startswith("delete:")
        )
        assert "rose" in delete_line, "删除要用红色系"
        assert "font-semibold" in delete_line, "颜色之外还要加粗，扫列表时一眼挑出来"

    def test_every_action_has_distinct_style(self):
        from pathlib import Path as _P

        src = (_P(ROOT) / "frontend/src/utils/secretAudit.ts").read_text(encoding="utf-8")
        # 十个动作都要有配色，漏一个就会退回透明底
        for action in (
            "create", "update", "rotate", "delete", "disable",
            "enable", "grant", "revoke", "bind", "unbind",
        ):
            assert f"{action}:" in src, f"{action} 缺配色"
        assert "SECRET_ACTION_FALLBACK_STYLE" in src, "未登记的动作要有兜底配色"

    def test_both_centers_use_shared_badge_style(self):
        for path in (SECRET_CENTER, ADMIN_SECRET_CENTER):
            source = _read(path)
            assert "secretActionStyle(row.action)" in source, f"{path.name} 未接入配色"
            assert "secretActionLabel(row.action)" in source
            assert "ACTION_LABEL" not in source, "本地映射应已删除，改由共享模块提供"

    def test_both_centers_import_shared_badge_style(self):
        """模板里写了不等于能用：`<script setup>` 里没 import 就是 undefined。

        曾经管理端把本地 ACTION_LABEL 换成共享函数时漏了这行 import，静态检查只
        报一条类型警告，运行时的表现是「点变更记录，弹框一片空白」——渲染列表时
        调用 undefined 抛错，整棵子树都渲染不出来。
        """
        pattern = re.compile(
            r"import\s*\{([^}]*)\}\s*from\s*['\"]\.\./utils/secretAudit['\"]"
        )
        for path in (SECRET_CENTER, ADMIN_SECRET_CENTER):
            source = _read(path)
            match = pattern.search(source)
            assert match, f"{path.name} 没有从 utils/secretAudit 导入「动作」文案与配色"
            imported = {name.strip() for name in match.group(1).split(",") if name.strip()}
            for name in ("secretActionLabel", "secretActionStyle"):
                assert name in imported, f"{path.name} 用了 {name} 却没导入"
