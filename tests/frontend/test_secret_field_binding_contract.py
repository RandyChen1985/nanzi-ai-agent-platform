"""使用侧「字段级绑定」的前端契约。

守的是四件事：

1. 结构化凭据在**每个**绑定入口都能选字段（header / 参数 / MCP / 脚本环境变量）；
2. 选中字段后绑定请求真的带上 ``value_path``——界面选好了却没传，等于白配；
3. 没选字段就保存要被拦住，而不是悄悄整段注入；
4. header 位点写的是 ``${secret:标识.字段}``，并保留用户原有的认证前缀。

这些是纯文件契约检查：跑起来不需要后端和数据库，改动 UI 时立刻能发现漏传。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FIELD_SELECT = ROOT / "frontend/src/components/secrets/SecretFieldSelect.vue"
HEADERS = ROOT / "frontend/src/components/system/ToolHeadersEditor.vue"
PARAMS = ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue"
MCP_PANEL = ROOT / "frontend/src/components/mcp/PlatformMcpCredentialPanel.vue"
SCRIPT = ROOT / "frontend/src/components/system/ScriptToolWizard.vue"
SECRET_CENTER = ROOT / "frontend/src/views/SecretCenter.vue"
MCP_REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"
PICKER = ROOT / "frontend/src/components/secrets/CredentialPicker.vue"
BINDING_LABEL = ROOT / "frontend/src/components/secrets/SecretBindingLabel.vue"
MAPPING_PANEL = ROOT / "frontend/src/components/secrets/SecretFieldMappingPanel.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestFieldSelectComponent:
    def test_reads_fields_from_backend(self):
        source = _read(FIELD_SELECT)
        assert "/fields" in source
        # 只读字段清单，不读值：选择器本来也不需要值
        assert "res.data?.data || {}" in source

    def test_sensitive_field_shows_no_value(self):
        source = _read(FIELD_SELECT)
        assert "（敏感）" in source
        # 敏感字段不带预览，非敏感才展示
        assert "if (field.sensitive) return" in source

    def test_structured_without_choice_is_flagged(self):
        source = _read(FIELD_SELECT)
        assert "不选字段就会把整段 JSON 注进去" in source

    def test_single_value_secret_explains_whole_injection(self):
        source = _read(FIELD_SELECT)
        assert "单值凭据，整条值直接注入" in source

    def test_switching_secret_clears_stale_path(self):
        source = _read(FIELD_SELECT)
        # 换到单值凭据时把上一次的字段路径清掉，否则绑定会带着不存在的字段
        assert "if (format.value !== 'json' && props.modelValue) emit('update:modelValue', '')" in source


class TestEveryBindingEntryOffersFieldChoice:
    def test_all_entries_import_the_component(self):
        # 五个入口：API 工具 Header / API 工具参数 / 我的 MCP 平台服务 /
        # 脚本环境变量 / MCP 工具集（Header + Authorization + 参数设置）
        for path in (HEADERS, PARAMS, MCP_PANEL, SCRIPT, MCP_REGISTRY):
            assert "SecretFieldSelect" in _read(path), path.name

    def test_mcp_registry_covers_its_three_entry_points(self):
        """MCP 工具集里有三处能绑凭据，漏一处用户就会在那里撞上「请选择字段」。"""
        source = _read(MCP_REGISTRY)
        # Authorization 固定绑定 / 其他 Header / 参数设置
        for prop in (
            ":secret-id=\"mcpBindingSecretId\"",
            ":secret-id=\"headerBindSecretId\"",
            ":secret-id=\"paramBindSecretId\"",
        ):
            assert prop in source, prop

    def test_headers_editor_binds_field_select(self):
        source = _read(HEADERS)
        assert ":secret-id=\"bindingSecretId\"" in source

    def test_params_editor_binds_field_select(self):
        source = _read(PARAMS)
        assert ":secret-id=\"pickerSecretId\"" in source

    def test_mcp_panel_binds_field_select(self):
        source = _read(MCP_PANEL)
        # 面板里变量名是 secretId（key 名撞了 modelValue 的缩写）
        assert ":secret-id=\"secretId\"" in source

    def test_script_wizard_binds_field_select(self):
        source = _read(SCRIPT)
        assert ":secret-id=\"envSecretId\"" in source


class TestValuePathIsActuallySent:
    def test_headers_editor_sends_value_path(self):
        source = _read(HEADERS)
        assert "value_path: valuePath" in source
        # 新建态补写也要带，否则「保存工具后再写绑定」这一步会丢掉字段
        assert "value_path: binding.value_path || ''" in source

    def test_params_editor_sends_value_path(self):
        source = _read(PARAMS)
        assert "value_path: valuePath" in source
        assert "value_path: binding.value_path || ''" in source

    def test_mcp_panel_sends_value_path(self):
        source = _read(MCP_PANEL)
        assert "value_path: editValuePath.value" in source

    def test_script_wizard_sends_value_path(self):
        source = _read(SCRIPT)
        assert "value_path: envValuePath.value" in source
        assert "value_path: row.value_path || ''" in source

    def test_mcp_registry_sends_value_path(self):
        source = _read(MCP_REGISTRY)
        # 三处入口各有一条 POST / 写入
        assert "value_path: paramBindValuePath.value" in source
        assert "value_path: mcpBindingValuePath.value" in source
        assert "value_path: headerBindValuePath.value" in source
        # 共用的 writeBinding 也要带上，否则 Header 两处会漏
        assert "value_path: valuePath" in source

    def test_bindings_are_read_back_with_value_path(self):
        """回填时若拿不到 value_path，重新打开弹窗就显示「未选字段」，等于白丢配置。

        MCP 面板的绑定由 ``/platform-targets`` 直接给对象（不做前端映射），
        所以那条链路由后端保证，见 test_secret_binding_value_path.py。
        """
        for path in (HEADERS, PARAMS, SCRIPT):
            assert "value_path: row.value_path || ''" in _read(path), path.name
        # MCP 面板：打开弹窗时从已有绑定回填
        assert "existing?.value_path || ''" in _read(MCP_PANEL)


class TestMissingFieldBlocksSave:
    def test_headers_editor_refuses_to_save_without_field(self):
        source = _read(HEADERS)
        assert "bindingNeedsField.value && !bindingValuePath.value" in source
        assert "请先选择要取用的字段" in source

    def test_params_editor_refuses_to_save_without_field(self):
        source = _read(PARAMS)
        assert "pickerNeedsField.value && !pickerValuePath.value" in source

    def test_mcp_panel_refuses_to_save_without_field(self):
        source = _read(MCP_PANEL)
        assert "editNeedsField.value && !editValuePath.value" in source

    def test_script_wizard_refuses_to_save_without_field(self):
        source = _read(SCRIPT)
        assert "envNeedsField.value && !envValuePath.value" in source

    def test_mcp_registry_refuses_to_save_without_field(self):
        source = _read(MCP_REGISTRY)
        assert "paramBindNeedsField.value && !paramBindValuePath.value" in source
        assert "mcpBindingNeedsField.value && !mcpBindingValuePath.value" in source
        assert "headerBindNeedsField.value && !headerBindValuePath.value" in source


class TestPickerShowsFormat:
    """下拉里要能看出「单值 / 结构化」——否则用户不知道选中后还要选字段。"""

    def test_picker_item_carries_format(self):
        source = _read(ROOT / "frontend/src/utils/credentialPicker.ts")
        assert "value_format?: string" in source
        assert "value_template?: string | null" in source

    def test_picker_list_marks_structured(self):
        source = _read(PICKER)
        assert "row.item.value_format === 'json'" in source
        assert "结构化" in source
        assert "单值" in source

    def test_picker_trigger_marks_structured(self):
        source = _read(PICKER)
        assert "selected.value_format === 'json'" in source

    def test_backend_picker_returns_format(self):
        source = _read(ROOT / "app/api/portal/endpoints/secrets.py")
        picker_body = source.split("async def picker(")[1].split("@router.get(\"/policy\"")[0]
        assert '"value_format": row.value_format or "text"' in picker_body
        assert '"value_template": row.value_template or ""' in picker_body


class TestHeaderValueExpression:
    def test_binding_writes_plain_placeholder(self):
        """绑定只写 `${CREDENTIAL:标识}`，取哪个字段由绑定上的 value_path 决定。

        曾经改成过 `${secret:标识.字段}`：那种写法在注入层会校验标识一致，
        普通用户绑自己的凭据去覆盖平台默认时必然 `reference_mismatch`。
        「个人覆盖」和「字段级取用」不该互斥——value_path 本来就带得走字段。
        """
        source = _read(HEADERS)
        assert "\\${CREDENTIAL:${secretKey}}" in source
        assert "secret:${secretKey}" not in source

    def test_existing_placeholder_is_replaced_not_stacked(self):
        source = _read(HEADERS)
        assert "\\\\$\\\\{(?:secret|CREDENTIAL):[^}]*\\\\}" in source or "secret|CREDENTIAL" in source

    def test_bearer_prefix_is_preserved(self):
        source = _read(HEADERS)
        # 手填过 `Bearer xxx` 时只替换值那一截，认证方案保留
        assert "text.indexOf(' ')" in source


class TestDetailExplainsHowToUse:
    def test_secret_center_lists_fields_with_copy(self):
        source = _read(SECRET_CENTER)
        assert "怎么用（可取的字段）" in source
        assert "loadFieldsOf" in source
        assert "copyReference" in source

    def test_reference_snippet_matches_backend_syntax(self):
        source = _read(SECRET_CENTER)
        # 后端 references.py 认的就是 ${secret:key.path}
        assert "\\${secret:${item.secret_key}.${field}}" in source

    def test_detail_warns_about_whole_json(self):
        source = _read(SECRET_CENTER)
        assert "不选会整段 JSON 注进去" in source


class TestBindingListShowsField:
    """列表态也要说清「这条绑定取的是哪个字段」。

    只显示 `admin.mail.cre` 会让人以为整条凭据都在用；实际注进去的可能只是
    password。排查时没法从界面看出这一点，只能去翻凭据原文或日志。
    """

    def test_label_component_exists_and_covers_three_parts(self):
        source = _read(BINDING_LABEL)
        assert "SecretFormatBadge" in source          # 值形态
        assert "取 ${props.valuePath}" in source      # 取用字段
        assert "整段 JSON" in source                  # 结构化却没选字段

    def test_structured_without_path_is_flagged(self):
        """结构化 + 整段 = 会把一整份 JSON 注进去，列表里要标黄提醒。"""
        source = _read(BINDING_LABEL)
        assert "structured.value && !props.valuePath" in source
        assert "bg-amber-50 text-amber-700" in source

    def test_every_list_uses_the_label(self):
        for path in (MCP_REGISTRY, PARAMS, SCRIPT, MCP_PANEL, HEADERS):
            assert "SecretBindingLabel" in _read(path), path.name

    def test_mcp_registry_shows_field_in_every_place(self):
        """凡是显示「用了哪条凭据」的地方，都要连「取哪个字段」一起显示。

        只给标识的话，一条结构化凭据供多个参数时，界面上几行长得一模一样，
        用户没法确认 Authorization 里到底会填什么。
        """
        source = _read(MCP_REGISTRY)
        # 参数设置行 / 其他 Header 两处 / Header 绑定弹窗 / Authorization 当前生效
        assert source.count("<SecretBindingLabel") == 5

    def test_backend_bindings_returns_format(self):
        source = _read(ROOT / "app/api/portal/endpoints/secrets.py")
        listing = source.split("async def get_bindings(")[1].split("\n@router.")[0]
        assert '"value_format"' in listing
        assert '"value_template"' in listing

    def test_backend_platform_targets_returns_format(self):
        source = _read(ROOT / "app/api/portal/endpoints/secrets.py")
        targets = source.split("async def list_platform_credential_targets(")[1].split("\n@router.")[0]
        assert '"value_format"' in targets
        assert '"value_template"' in targets


class TestOneSecretServesManyParams:
    """一条凭据供多个参数：反过来的配置顺序。

    逐参数配时，一条邮箱凭据要绑三遍（username 取 username、mailkey 取 password），
    每次都得重新选同一条。映射面板把顺序倒过来：先选凭据，再分字段。
    """

    def test_mapping_panel_exists_and_posts_batch(self):
        src = _read(MAPPING_PANEL)
        # 一次提交整张表：逐条 POST 会出现「3 个成功 2 个失败」的半成品
        assert "/bindings/batch" in src
        assert "value_path" in src

    def test_mapping_panel_only_submits_changed_rows(self):
        """只提交动过的参数，否则没参与映射的参数会被当成「清空」解绑。"""
        src = _read(MAPPING_PANEL)
        assert "pendingChanges" in src
        assert "original" in src

    def test_mapping_panel_offers_auto_match_by_name(self):
        src = _read(MAPPING_PANEL)
        assert "按名字自动匹配" in src

    def test_both_param_dialogs_split_into_two_tabs(self):
        """参数设置按凭据形态分两页：单值逐参数配、多值一条供一组。

        混在一张表里的话，多值那半只能靠二级弹窗补救——用户得先点一次按钮才看得见
        字段映射。拆开之后，选凭据和分字段在同一个面上完成。
        """
        for path in (PARAMS, MCP_REGISTRY):
            src = _read(path)
            assert "SecretFieldMappingPanel" in src, path.name
            assert "'单值凭据' : '多值凭据'" in src, path.name
            assert "paramTab" in src, path.name

    def test_multi_tab_warns_before_overwriting_single_bindings(self):
        """多值页保存是整页覆盖。单值页已配的参数必须先看到这句提醒。"""
        panel = _read(MAPPING_PANEL)
        assert "singleValueParams" in panel
        assert "改回模型填写" in panel
        for path in (PARAMS, MCP_REGISTRY):
            assert "single-value-params" in _read(path), path.name

    def test_multi_tab_offers_rule_source(self):
        """多值凭据也能按规则挑：规则和单值页是同一条，只是多了字段映射。"""
        panel = _read(MAPPING_PANEL)
        assert "按规则匹配" in panel
        assert "value_template" in panel
        assert "strategy: 'rule'" in panel

    def test_batch_endpoint_validates_before_writing(self):
        """先全量校验再写：一条不过就整体拒绝。"""
        src = _read(ROOT / "app/api/portal/endpoints/secrets.py")
        batch = src.split("async def create_bindings_batch(")[1].split("\n@router.")[0]
        assert "dry_run=True" in batch
        assert "未保存任何改动" in batch


class TestRuleBindingTakesField:
    """规则绑定也要能说清取哪个字段。"""

    def test_locator_editor_has_template_and_field(self):
        src = _read(ROOT / "frontend/src/components/secrets/SecretLocatorEditor.vue")
        # 模板约束决定字段能不能下拉
        assert "value_template" in src
        assert "templateFields" in src
        assert "取用字段" in src

    def test_rule_payload_excludes_value_path(self):
        """value_path 落在绑定记录上；混进 rule JSON 会被后端判未知字段。"""
        src = _read(ROOT / "frontend/src/components/secrets/SecretLocatorEditor.vue")
        assert "const { value_path: _path, ...ruleBody } = draft.value" in src

    def test_callers_split_value_path_out_of_rule(self):
        for path in (MCP_REGISTRY, PARAMS, MCP_PANEL):
            src = _read(path)
            assert "value_path: rulePath" in src, path.name

    def test_rule_editor_restores_saved_path(self):
        for path in (MCP_REGISTRY, PARAMS, MCP_PANEL):
            src = _read(path)
            assert "withRulePath" in src, path.name


class TestBindingRegressions:
    """这几条都是「改好了又被改回去」的高危点，用契约钉住。"""

    def test_picker_emits_picked_before_model_update(self):
        """`@picked` 里要比较「换了一条吗」，必须在 v-model 回写之前拿到旧值。

        顺序反了，`item.id !== secretId` 恒为假，上一个凭据的字段路径永远清不掉：
        换选另一条结构化凭据后带着旧字段名保存——下拉空白、拦截失效、后端 400。
        """
        src = _read(ROOT / "frontend/src/components/secrets/CredentialPicker.vue")
        choose = src.split("function choose(")[1].split("\n}")[0]
        assert choose.index("emit('picked'") < choose.index("emit('update:modelValue'")

    def test_header_binding_writes_credential_placeholder(self):
        """Header 绑定写 `${CREDENTIAL:标识}`，不写 `${secret:标识.字段}`。

        后者在注入层要校验「引用里的标识 == 本次解析到的凭据标识」。平台配的是
        管理员那条，普通用户绑自己的凭据覆盖时标识必然对不上 → 个人覆盖直接失效。
        """
        src = _read(HEADERS)
        assert "${CREDENTIAL:" in src
        body = src.split("function buildHeaderValue(")[1].split("\n}")[0]
        assert "secret:" not in body, "默认写法里不该出现字段级引用"

    def test_personal_mcp_mapping_uses_scope_not_permission(self):
        """批量面板写库时是不是全局绑定，看 scope，不看「能不能管这个工具」。"""
        src = _read(MCP_REGISTRY)
        assert ':is-global="isGlobalBinding"' in src
        assert ':is-global="canManageTool' not in src

    def test_mapping_syncs_credential_param_list(self):
        """绑定落库 ≠ 运行时会注入：还要同步「哪些参数由凭据提供」。"""
        panel = _read(MAPPING_PANEL)
        # 整页覆盖：每个参数都要进 items，没选字段的提交 strategy: 'none'
        assert "strategy: 'none'" in panel
        assert "bound: props.params.filter" in panel
        assert "freed: props.params.filter" in panel
        for path in (MCP_REGISTRY, PARAMS):
            assert "bound: string[]" in _read(path), path.name

    def test_param_editor_keeps_format_metadata(self):
        """漏了 value_format/value_template，列表里的结构化徽标会全部退化。"""
        src = _read(PARAMS)
        body = src.split("async function loadBindings(")[1].split("\n}")[0]
        assert "value_format" in body
        assert "value_template" in body


class TestComponentsAreActuallyImported:
    """模板里写 `<SecretBindingLabel>`，就必须有对应的 import。

    没有 import 时 Vue 会把它当成「未知自定义元素」静默跳过——不报错、不渲染，
    界面上那一格直接消失。vue-tsc 和 SFC 编译都查不出来这种，只能靠契约钉住。
    """

    def test_every_user_imports_the_binding_label(self):
        import re

        checked = 0
        for path in (ROOT / "frontend/src").rglob("*.vue"):
            source = _read(path)
            if "<SecretBindingLabel" not in source:
                continue
            checked += 1
            assert re.search(
                r"import\s+SecretBindingLabel\s+from", source
            ), f"{path.name} 用了 SecretBindingLabel 却没 import"
        assert checked >= 4, f"只检查到 {checked} 个文件，路径可能变了"


class TestBindingBadgeFitsNarrowCells:
    """绑定徽标要能在表格的窄值列里正常显示。

    踩过的坑：MCP「其他 HEADER」的值格子里，`来自凭据` 是**裸文本节点**，
    在 flex 容器里会参与收缩——宽度不够时逐字换行，四个字竖着排成四行，
    而旁边的凭据标识被挤到只剩前几个字符。裸文本在 flex 里就没有「不收缩」这回事，
    必须包一层带 `shrink-0 whitespace-nowrap` 的元素。
    """

    def test_plain_text_in_flex_is_wrapped(self):
        source = _read(MCP_REGISTRY)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped == "来自凭据":
                raise AssertionError(
                    "裸文本 `来自凭据` 在 flex 里会被压成竖排，要包 "
                    "`<span class=\"shrink-0 whitespace-nowrap\">`"
                )

    def test_badge_has_a_compact_mode(self):
        """窄格子里用模板 id 就够，全称退到 title。

        「结构化 · 邮箱 SMTP 发送」有 12 个字，在值列里会把凭据标识挤掉——
        而「是哪条凭据」比「哪个模板」更没有替代信息。
        """
        badge = _read(ROOT / "frontend/src/components/secrets/SecretFormatBadge.vue")
        assert "compact" in badge
        assert "credentialTemplateLabel(props.template)" in badge
        label = _read(ROOT / "frontend/src/components/secrets/SecretBindingLabel.vue")
        assert ':compact="compact"' in label


class TestNestedFieldPaths:
    """嵌套字段：后端能存、执行期能取，界面也得选得出来。

    执行期 `pick_field_typed` 一直是逐段走 dict / list 的。曾经的缺口在配置期：
    路径正则不收数字段、`/fields` 只列顶层——「运行时能跑，界面配不出来」。
    """

    def test_path_pattern_accepts_array_index(self):
        from app.services.secrets.center import normalize_value_path

        assert normalize_value_path("list.0.id") == "list.0.id"
        assert normalize_value_path("auth.token") == "auth.token"

    def test_path_pattern_still_rejects_brackets_and_blank_segments(self):
        from app.services.secrets.center import (
            SecretValidationError,
            normalize_value_path,
        )

        for bad in ("a[0]", "a.", ".a", "a b", "0abc"):
            try:
                normalize_value_path(bad)
            except SecretValidationError:
                continue
            raise AssertionError(f"{bad} 应该被拒绝")

    def test_field_select_labels_object_and_array(self):
        """对象/数组也能整段选，文案要说清「选它等于整段取」。"""
        source = _read(FIELD_SELECT)
        assert "整个对象" in source
        assert "整个数组" in source
        assert "kind" in source


class TestSingleValueRuleHasNoFieldPicker:
    """单值凭据 tab 的规则编辑不该出现「取用字段」。

    那一栏的语义是「这个参数拿一个整段值」，命中的凭据整段注入。摆一个字段下拉
    出来等于问用户一个此刻没法回答的问题：命中哪条凭据运行时才知道，下拉里
    根本没有字段可选，只有一个空框加一句「留空 = 整段」。
    """

    def test_param_entries_hide_the_field_section(self):
        for path in (MCP_REGISTRY, ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue"):
            source = _read(path)
            assert ':show-value-path="false"' in source, path.name

    def test_multi_value_rule_keeps_template_only(self):
        """多值页要模板约束，不要取用字段。

        取用字段若放进弹窗，就必须有个落地点。我试过「当所有行的默认值」，
        后果是灾难性的：`load()` 会把第一条规则行的路径回填进来，于是
        「某个参数绑了 password」会让所有留空（= 模型填写）的参数在下次保存时
        全被绑上 password，而界面还显示它们已经回到模型填写。
        它只能在下表逐行选——那里每行本来就有自己的下拉。
        """
        source = _read(ROOT / "frontend/src/components/secrets/SecretFieldMappingPanel.vue")
        assert ':show-value-path="false"' in source
        assert ':show-value-template="true"' in source
        # 留空一律回落模型填写，不能被某个「默认字段」兜底
        assert "defaultPath" not in source
        assert "const path = picks.value[name] || ''" in source

    def test_hidden_section_clears_the_stale_value(self):
        """控件藏了，draft 里可能还留着上次的 value_path。

        不显式清空的话，展开提交会把用户看不见的配置又写回去。
        """
        source = _read(ROOT / "frontend/src/components/secrets/SecretLocatorEditor.vue")
        assert "value_path: props.showValuePath ? draft.value.value_path : ''" in source


class TestNewStateReopenKeepsMeta:
    """新建态重开绑定弹窗，选中项要带着形态回来。

    踩过的坑：`openHeaderBind` 重建 `headerBindPicked` 时只写了 `{ id, secret_key }`。
    新建态下（工具还没有 id）重开同一个 Header 的弹窗、不换凭据直接点保存，
    `saveHeaderBind` 会拿 picked 的字段写回 `pendingBindings`——形态字段是 undefined，
    已经选好的「结构化 + 模板」就被洗成一条裸标识，徽标消失。
    这类「重建时漏字段」的 bug 只会影响保存前的界面回显，很难在测试里复现，
    所以用契约钉住重建点。
    """

    def test_rebuilt_pick_carries_format_and_template(self):
        source = _read(MCP_REGISTRY)
        for marker in ("headerBindPicked.value = existing", "mcpBindingPicked.value = {"):
            assert marker in source, marker
        # 两处重建都必须带上形态
        assert source.count("value_template: existing.value_template") >= 1
        assert source.count("value_template: hit.value_template") >= 1

    def test_pick_handlers_use_the_shared_type(self):
        """事件签名和新态记账共用 PickedSecret，别再各写一份内联类型。

        签名写 `{ id, secret_key }` 时，载荷里的形态字段在类型上「不存在」，
        下一个人照样会漏掉——tsc 也不会提醒。
        """
        registry = _read(MCP_REGISTRY)
        assert "function onHeaderBindPicked(item: PickedSecret | null)" in registry
        assert "function onMcpBindingPicked(item: PickedSecret | null)" in registry
        wizard = _read(ROOT / "frontend/src/components/system/ScriptToolWizard.vue")
        assert "function onEnvSecretPicked(item: PickedSecret | null)" in wizard

    def test_header_editor_shows_pending_binding(self):
        """新建态绑完不写 bindings，「当前已绑定」也得显示出来。"""
        source = _read(ROOT / "frontend/src/components/system/ToolHeadersEditor.vue")
        assert "pendingBindings.value[bindingSlot.value]" in source


class TestPersonalSecretOnPlatformConsumer:
    """平台级消费者 + 个人凭据 = 只有本人能用，界面要讲清楚。

    绑定弹窗默认是全局语义（`is_global: true`），用户很容易以为「配一次所有人都用上了」。
    个人凭据没法给所有人用，后端会把它降级成本人绑定——别人调用时拿到的仍是
    「未绑定」。这个落差必须出现在**选中那一刻**，而不是等别人报错才发现。
    """

    def test_picker_reports_the_scope(self):
        """组件拿到的是单条 item，靠 mine/shared 分组名传不到提示点。"""
        from app.api.portal.endpoints import secrets as endpoints

        source = _read(ROOT / "app/api/portal/endpoints/secrets.py")
        assert '"owner_scope": row.owner_scope' in source
        kinds = _read(ROOT / "frontend/src/utils/credentialPicker.ts")
        assert "owner_scope?: string" in kinds

    def test_both_api_tool_dialogs_warn(self):
        for path in (
            ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue",
            ROOT / "frontend/src/components/system/ToolHeadersEditor.vue",
        ):
            source = _read(path)
            assert "owner_scope !== 'user'" in source, path.name
            assert "只对你生效" in source, path.name

    def test_mcp_warns_only_for_platform_consumers(self):
        """个人 MCP 服务上绑自己的凭据是正常的，不该跳提示。"""
        source = _read(MCP_REGISTRY)
        # 三个 notice 都要有守卫（mcp / header / param），少一个就会在个人 MCP
        # 里跳「别人拿不到值」——而那里本来就是「只对我这个服务生效」
        assert source.count("if (!isGlobalBinding.value) return ''") >= 3
        assert source.count("owner_scope !== 'user'") >= 2


class TestBindingScopeComesFromTheConsumer:
    """绑定的作用域由消费端声明，组件不猜。

    写死 `is_global: true` 现在能跑（后端会按凭据作用域降级），但那是靠后端兜底。
    API 工具目前确实全是平台级，所以值还是 true——区别在于**这个知识现在在
    管这一层的地方**（`ToolRegistry`），而不是散落在三个组件内部。
    真出现「个人级 API 工具」时，改调用方一处就够。
    """

    def test_no_hardcoded_is_global_in_components(self):
        for path in (
            ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue",
            ROOT / "frontend/src/components/system/ToolHeadersEditor.vue",
        ):
            source = _read(path)
            assert "is_global: true" not in source, f"{path.name} 还有写死的 is_global"
            assert "is_global: props.isGlobal" in source, path.name

    def test_the_chain_passes_it_through(self):
        """props 声明了却忘了透传，等于没改——中间层最容易漏。"""
        editor = _read(ROOT / "frontend/src/components/system/ToolParamEditor.vue")
        assert "isGlobal?: boolean" in editor
        assert ':is-global="isGlobal"' in editor
        registry = _read(ROOT / "frontend/src/components/system/ToolRegistry.vue")
        # 消费端必须显式声明，而不是靠子组件默认值
        assert registry.count(':is-global="true"') >= 2


class TestTemplateAndFieldAreSeparateSwitches:
    """「模板约束」和「取用字段」挤在同一节里，但完全是两件事。

    踩过的坑：两者共用一个 `showValuePath` 开关。多值页关掉取用字段（那里的字段在
    映射表每一行选）时，模板下拉被一起关掉了——而多值页恰恰要靠它声明「命中的
    凭据都长这样」，外面的参数表才敢把字段做成下拉。结果新配的规则只能手填字段名。
    """

    def test_editor_has_two_switches(self):
        source = _read(ROOT / "frontend/src/components/secrets/SecretLocatorEditor.vue")
        assert "showValueTemplate?: boolean" in source
        assert "showValuePath?: boolean" in source
        # 容器由两者共同决定，不然只开一个就整节消失
        assert 'v-if="showValueTemplate || showValuePath"' in source

    def test_each_call_site_picks_its_own_combination(self):
        panel = _read(ROOT / "frontend/src/components/secrets/SecretFieldMappingPanel.vue")
        # 多值页只要模板约束（定字段集）；取用字段在下表逐行选
        assert ':show-value-template="true"' in panel
        assert ':show-value-path="false"' in panel
        for path in (
            MCP_REGISTRY,
            ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue",
        ):
            source = _read(path)
            # 参数入口整值注入：模板与字段都不需要
            assert ':show-value-template="false"' in source, path.name
            assert ':show-value-path="false"' in source, path.name

    def test_hidden_template_is_cleared_on_save(self):
        """藏起来的控件不能把旧值写回去——和 value_path 同一个道理。"""
        source = _read(ROOT / "frontend/src/components/secrets/SecretLocatorEditor.vue")
        assert "value_template: props.showValueTemplate ? draft.value.value_template : ''" in source


class TestOwnerScopeFlowsThroughLocalState:
    """`owner_scope` 要从接口一路带到重建的 picked 里。

    上一轮只补了「读」的那一半（重建 picked 时带 owner_scope），数据源没写：
    接口行转本地表时没存、新建态记账时没存，于是 `existing.owner_scope` 恒为
    undefined，「只对你生效」的提示永远不出现。类型上它是可选的，tsc 不吭声。
    """

    def test_local_tables_keep_it(self):
        cases = (
            (ROOT / "frontend/src/components/system/ToolHeadersEditor.vue", "owner_scope: row.owner_scope"),
            (MCP_REGISTRY, "owner_scope: row.owner_scope"),
            (
                ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue",
                "owner_scope: row.owner_scope",
            ),
        )
        for path, marker in cases:
            assert marker in _read(path), path.name

    def test_pending_writes_keep_it(self):
        """新建态（工具还没 id）走的是 pending，这条路径不经过接口。

        `pending.value[name]` 这处最容易漏——它不是「接口行转本地表」，也不是
        已提交后的回写，是第三个写入点。
        """
        for path in (
            ROOT / "frontend/src/components/system/ToolHeadersEditor.vue",
            MCP_REGISTRY,
            ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue",
        ):
            assert "owner_scope: picked.owner_scope" in _read(path), path.name

    def test_notice_skips_personal_consumers(self):
        """个人级工具上绑自己的凭据是正常的，不该跳「只对你生效」。"""
        for path in (
            ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue",
            ROOT / "frontend/src/components/system/ToolHeadersEditor.vue",
        ):
            source = _read(path)
            assert "if (!props.isGlobal) return ''" in source, path.name


class TestRuleResaveKeepsHandTypedFields:
    """「不限定结构」时字段只能手填，重开规则弹窗保存不能把它抹掉。

    模板为空 → `ruleFields` 为空 → 原来那个「模板换了就清掉不存在的字段」的循环
    会把所有手填值清空。用户只是把匹配方式从「精确」改成「不区分大小写」，
    回来发现字段全没了，还得重填一遍。
    """

    def test_cleanup_only_runs_when_fields_are_listed(self):
        source = _read(ROOT / "frontend/src/components/secrets/SecretFieldMappingPanel.vue")
        assert "if (!ruleFields.value.length) {" in source
        # 早退必须排在清理循环之前
        assert source.index("if (!ruleFields.value.length) {") < source.index(
            "!ruleFields.value.some((f) => f.name === picks.value[name])"
        )


class TestEveryParamShowsWhereItsValueComesFrom:
    """每个参数都要有一个来源徽标，包括「模型填写」。

    凭据参数已经从模型可见的 schema 里移除了，但界面上看不出来。只给「已配」的行
    加标记时，留空的行既可能是「就该模型填」也可能是「我漏配了」，用户得逐行回想。
    """

    def test_multi_value_table_labels_all_three_states(self):
        source = _read(ROOT / "frontend/src/components/secrets/SecretFieldMappingPanel.vue")
        assert "服务端注入</span>" in source
        assert ">模型填写</span>" in source
        # 被别的凭据占着的行不能说成「模型填写」，那是第三种状态
        assert "现由 {{ takenByOther[name] }} 提供" in source

    def test_single_value_table_labels_model_rows_too(self):
        source = _read(MCP_REGISTRY)
        # 徽标必须在「模型填写」分支就渲染，不能只在非 model 时出现
        assert 'v-if="paramDraft[name] === \'model\'"' in source
        assert ">模型填写</span>" in source
        assert "服务端注入" in source

    def test_credential_rows_say_the_model_cannot_see_it(self):
        """「服务端注入」的含义是模型压根看不到这个值，title 里要说清。"""
        mapping = _read(ROOT / "frontend/src/components/secrets/SecretFieldMappingPanel.vue")
        assert "模型看不到这个值" in mapping
        registry = _read(MCP_REGISTRY)
        assert "模型看不到这个值" in registry


class TestNewToolModalStartsClean:
    """新建工具时不能继承上一次编辑留下的「未绑定参数」。

    `paramUnboundNames` 只有表单分支的 `ToolParamEditor` 会更新，向导分支没绑这个
    事件。于是在「编辑 A 工具 → 发现 A 有参数没绑 → 关窗 → 新建 B 工具」之后，
    按钮被一个跟 B 毫无关系的列表禁用，提示还写着 A 的参数名。
    """

    def test_open_modal_resets_unbound_names(self):
        source = _read(ROOT / "frontend/src/components/system/ToolRegistry.vue")
        assert "paramUnboundNames.value = []" in source
        # 必须在 openModal 里，且与其它「开窗重置」放在一起
        open_modal = source[source.index("const openModal"):source.index("const cloneTool")]
        assert "paramUnboundNames.value = []" in open_modal

    def test_wizard_does_not_feed_the_flag(self):
        """向导里的参数编辑器没接这个事件，这正是上面那条重置必须存在的原因。

        哪天给向导补上事件透传，这条会红——那时可以重新评估要不要保留重置。
        """
        wizard = _read(ROOT / "frontend/src/components/system/ToolWizard.vue")
        assert "unbound-changed" not in wizard


class TestWizardPendingBindingsSurviveSave:
    """向导里配的凭据绑定要跟着新建的工具一起落库。

    新建时工具还没有 id，绑定先记在 `ToolSystemParamEditor` 的 pending 里，等父组件
    创建成功拿到 id 再补写。向导是默认的新建路径，但父组件原来只持有**表单分支**
    那个 ref——`paramEditorRef.value` 是 null，补写这一步直接跳过，pending 随组件
    卸载消失。表现是：走完向导、试跑通过、保存成功，打开一看凭据一条没绑。
    """

    def test_wizard_exposes_flush_pending(self):
        wizard = _read(ROOT / "frontend/src/components/system/ToolWizard.vue")
        assert 'ref="paramEditorRef"' in wizard
        assert "defineExpose({ flushPending })" in wizard

    def test_registry_flushes_the_wizard_too(self):
        source = _read(ROOT / "frontend/src/components/system/ToolRegistry.vue")
        assert 'ref="wizardRef"' in source
        # 两条分支互斥，必须都补；只留 paramEditorRef 那条等于向导白配
        assert "wizardRef.value.flushPending(newId)" in source
        assert "paramEditorRef.value.flushPending(newId)" in source

    def test_wizard_editor_falls_back_to_api_tool_defaults(self):
        """向导不传 consumerType / isGlobal，靠默认值兜底——默认值错了会绑错表。"""
        editor = _read(ROOT / "frontend/src/components/system/ToolParamEditor.vue")
        assert "consumerType: 'api_tool'" in editor
        assert "isGlobal: true" in editor
