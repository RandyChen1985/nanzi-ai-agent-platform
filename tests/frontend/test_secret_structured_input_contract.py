"""凭据结构化录入的前端契约（源码级）。

用户要的是「高级的 UI 输入视图」：模板化录入、可视化表单，但**入库仍是 JSON 字符串**。
这里锁定这条链路上的几个容易悄悄退化的点：

1. 模板是前端常量（改模板要发版），且必须自带 `secret` 标记；
2. 示例值只作 placeholder，绝不预填——预填等于提交了一份用户没看过的值；
3. 两个凭据页面共用同一个编辑器，并把 `value_format / value_template /
   value_secret_fields` 三个字段真的提交出去；
4. 轮换沿用原形态：结构化凭据被轮换成单值，等于把结构丢了、摘要也失效；
5. 列表用统一徽标区分「单值」与「结构化 · 模板名」。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "frontend/src/utils/credentialTemplates.ts"
EDITOR = ROOT / "frontend/src/components/secrets/CredentialValueEditor.vue"
BADGE = ROOT / "frontend/src/components/secrets/SecretFormatBadge.vue"
SECRET_CENTER = ROOT / "frontend/src/views/SecretCenter.vue"
ADMIN_CENTER = ROOT / "frontend/src/views/AdminSecretCenter.vue"
ENDPOINT = ROOT / "app/api/portal/endpoints/secrets.py"
CENTER = ROOT / "app/services/secrets/center.py"
REFERENCES = ROOT / "app/services/secrets/references.py"


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


class TestTemplateCatalogue:
    def test_catalogue_covers_the_common_credential_kinds(self):
        source = _read(TEMPLATES)
        for template_id in (
            "smtp",
            "imap",
            "sftp",
            "ftp",
            "mysql",
            "postgres",
            "redis",
            "mongodb",
            "es",
            "aws",
            "aliyun-oss",
            "wecom",
            "dingtalk",
            "feishu",
            "slack",
            "bearer",
            "api-key",
            "basic",
            "oauth2",
            "ssh-key",
            "tls-cert",
            "gcp-sa",
            "kafka",
            "rabbitmq",
            "proxy",
            "openai",
        ):
            assert f"id: '{template_id}'" in source, f"模板缺少 {template_id}"

        # 自定义 JSON 是「高级直编」的入口，必须是 raw
        assert "id: 'custom'" in source
        custom_block = source.split("id: 'custom'")[1]
        assert "raw: true" in custom_block.split("}")[0] or "raw: true" in custom_block[:400]

    def test_groups_start_with_all(self):
        source = _read(TEMPLATES)
        assert "['全部', ...Array.from(new Set(" in source

    def test_secret_fields_are_declared_for_passwords(self):
        source = _read(TEMPLATES)
        # 摘要排除完全依赖这个标记：漏标一个，密码就会进 value_summary
        assert source.count("secret: true") >= 10
        assert re.search(r"key: 'password', label: '[^']+', type: 'password'[^}]*secret: true", source)

    def test_structured_flag_is_the_only_storage_hint(self):
        """模板不下发到后端：库里只有 value_format + value_template + JSON 字符串。"""
        source = _read(TEMPLATES)
        assert "value_format = 'json'" in source or "value_format" in source
        assert "不进库" in source or "不入库" in source


class TestEditorComponent:
    def test_two_modes_are_offered(self):
        source = _read(EDITOR)
        assert "自由输入" in source
        assert "结构化（模板）" in source
        assert "switchFormat('text')" in source
        assert "switchFormat('json')" in source

    def test_examples_are_placeholders_only(self):
        source = _read(EDITOR)
        # placeholder 取 example；空值只能是 ''/false/select 首项
        assert "function fieldPlaceholder" in source
        assert "String(field.example)" in source
        empty_fn = source.split("function emptyValueOf")[1].split("}")[0]
        assert "example" not in empty_fn

    def test_required_fields_block_submit(self):
        source = _read(EDITOR)
        assert "这个字段必填" in source
        assert "valid.value = next" in source

    def test_secret_field_names_are_emitted_to_parent(self):
        source = _read(EDITOR)
        assert "filter((field) => field.secret)" in source
        assert "secretFields.value = next" in source

    def test_raw_json_mode_validates_object(self):
        source = _read(EDITOR)
        assert "顶层必须是 JSON 对象" in source

    def test_paste_recognition_exists(self):
        source = _read(EDITOR)
        assert "function recognize" in source
        assert "识别并套用" in source


class TestPersonalCenterWiring:
    def test_editor_is_used_in_wizard_and_rotate(self):
        source = _read(SECRET_CENTER)
        assert source.count("<CredentialValueEditor") == 2
        assert "v-model:secretFields=\"form.value_secret_fields\"" in source
        assert "v-model:secretFields=\"rotateSecretFields\"" in source

    def test_wizard_submits_format_template_and_secret_fields(self):
        source = _read(SECRET_CENTER)
        payload = source.split("await axios.post('/api/portal/secrets', {")[1].split("})")[0]
        assert "value_format: form.value.value_format" in payload
        assert "value_template:" in payload
        assert "value_secret_fields:" in payload

    def test_rotation_keeps_original_format(self):
        source = _read(SECRET_CENTER)
        assert "rotateFormat.value = item.value_format === 'json' ? 'json' : 'text'" in source
        rotate_payload = source.split("/rotate`, {")[1].split("})")[0]
        assert "value_format: rotateFormat.value" in rotate_payload
        assert "value_template:" in rotate_payload

    def test_list_distinguishes_value_shape(self):
        source = _read(SECRET_CENTER)
        assert "<SecretFormatBadge" in source

    def test_detail_pane_shows_summary(self):
        source = _read(SECRET_CENTER)
        assert "selected.value_summary" in source


class TestAdminCenterWiring:
    def test_editor_is_used_in_wizard_and_rotate(self):
        source = _read(ADMIN_CENTER)
        assert source.count("<CredentialValueEditor") == 2

    def test_wizard_submits_structured_fields(self):
        source = _read(ADMIN_CENTER)
        payload = source.split("await axios.post('/api/portal/secrets/admin', {")[1].split("})")[0]
        assert "value_format: form.value.value_format" in payload
        assert "value_template:" in payload
        assert "value_secret_fields:" in payload

    def test_both_card_and_table_views_show_badge(self):
        source = _read(ADMIN_CENTER)
        assert source.count("<SecretFormatBadge") == 2


class TestBadge:
    def test_badge_names_the_template(self):
        source = _read(BADGE)
        assert "结构化 · " in source
        assert "'单值'" in source
        assert "credentialTemplateLabel" in source


class TestBackendContract:
    def test_request_models_accept_structured_fields(self):
        source = _read(ENDPOINT)
        assert source.count("value_secret_fields: List[str]") == 2
        assert "value_format: str = Field(default=\"text\"" in source
        assert "value_format: Optional[str] = None" in source

    def test_serializer_exposes_shape_for_the_list_badge(self):
        source = _read(ENDPOINT)
        assert '"value_format": row.value_format or "text"' in source
        assert '"value_template": row.value_template' in source
        assert '"value_summary": row.value_summary' in source

    def test_summary_never_includes_declared_or_secret_shaped_values(self):
        source = _read(CENTER)
        assert "_SUMMARY_SKIP_KEYS" in source
        assert "blocked" in source
        assert "_looks_like_secret(text)" in source

    def test_reference_parsing_refuses_to_downgrade(self):
        source = _read(REFERENCES)
        # 解析不出来必须报错，不能退回整段明文（那会把整份 JSON 塞进 Authorization 头）
        assert "ReferenceError.NOT_JSON" in source
        assert "ReferenceError.KEY_MISMATCH" in source
        assert "不做「失败就跳过」" in source or "不做「失败就跳过」" in source


class TestRegressionGuards:
    """静态评审抓出来的几处，逐条钉死。

    这些都不是「风格问题」，而是会让功能静默失效或丢数据的坑。
    """

    def test_template_import_path_is_the_real_relative_path(self):
        """组件在 components/secrets/ 下，模板常量在 utils/ ——要退两级。

        写错时 `vue-tsc --noEmit` 报不出来（根 tsconfig 是 project references，
        什么都不检查），只有真实构建才会失败，所以在这里守住。
        """
        source = _read(EDITOR)
        assert "from '../../utils/credentialTemplates'" in source
        assert "from '../utils/credentialTemplates'" not in source

    def test_custom_json_does_not_prefill_an_empty_object(self):
        """预填 `{}` 会让「一个字段都没写」看起来可以提交。"""
        source = _read(EDITOR)
        assert "{\\n  \\n}" not in source

    def test_empty_custom_json_reports_an_error(self):
        source = _read(EDITOR)
        # 2026-11-15：自定义 JSON 的默认视图改成键值对表格，空状态提示也得跟着改——
        # 让用户在表格里看到「去写一个 JSON 对象」只会更困惑。
        assert "至少填一个字段" in source
        # 顶层必须是对象的校验仍然在（JSON 视图下输入数组要拦）
        assert "顶层必须是 JSON 对象" in source

    def test_number_input_keeps_raw_text_instead_of_nan(self):
        source = _read(EDITOR)
        assert "Number.isNaN(num) ? raw : num" in source

    def test_switching_to_structured_never_silently_drops_content(self):
        source = _read(EDITOR)
        # 非 JSON 内容交给「自定义 JSON」承载，而不是清空
        assert "已原样放进「自定义 JSON」" in source
        # 模板不认识的键要明说会被丢弃
        assert "这些键不在模板里，保存时会丢弃" in source

    def test_submit_also_checks_value_validity(self):
        for path in (SECRET_CENTER, ADMIN_CENTER):
            source = _read(path)
            submit = source.split("async function submitCreate()")[1][:600]
            assert "if (!valueValid.value)" in submit

    def test_admin_card_view_shows_summary_like_the_table(self):
        source = _read(ADMIN_CENTER)
        # 卡片与列表两种视图都要能看到摘要
        assert source.count("item.value_summary") >= 2


class TestUnresolvedReferenceIsNotSentOut:
    def test_placeholder_detection_covers_reference_syntax(self):
        source = _read(ROOT / "app/services/ai/tools/tool_credentials.py")
        # 注入失败时保留的 ${secret:...} 必须仍被当作「未填」，
        # 否则这句占位符会原样发给外部接口
        assert "has_secret_reference(value)" in source

    def test_summary_has_server_side_sensitive_fallback(self):
        source = _read(CENTER)
        # 前端没声明敏感字段时，后端按「键名分词」兜底，并跳过含账密的 URL
        assert "_is_sensitive_key" in source
        assert "_SENSITIVE_KEY_WORDS" in source
        assert "_looks_like_credentialed_url" in source


class TestReferenceParserCaseHandling:
    def test_field_path_keeps_original_case(self):
        source = _read(REFERENCES)
        # 只把 key 做大小写无关匹配，路径必须原样保留
        assert "rest = candidate[len(known) + 1 :]" in source
        assert "candidate = expression.strip().lower()" not in source


class TestSwitchingModeKeepsWhatUserTyped:
    """切到结构化时把当前文本回填进表单 —— 否则用户刚写的 JSON 会被默认值顶掉。"""

    def test_switch_uses_fill_template_not_bare_choose(self):
        source = _read(EDITOR)
        switch_body = source.split("function switchFormat(")[1].split("\n}\n\n/** 这段 JSON")[0]
        # 回填必须经过 fillTemplate（chooseTemplate 只从当前表单继承，表单此刻是空的）
        assert "fillTemplate(recognized, source)" in switch_body
        assert "chooseTemplate(recognized)" not in switch_body

    def test_fill_template_overwrites_values_before_push_out(self):
        source = _read(EDITOR)
        body = source.split("function fillTemplate(")[1].split("\n}")[0]
        assert "chooseTemplate(templateId)" in body
        assert "values.value = next" in body
        assert "pushOut()" in body

    def test_paste_path_shares_the_same_fill_helper(self):
        source = _read(EDITOR)
        paste = source.split("function applyPasted(")[1].split("\n}")[0]
        assert "fillTemplate(matched, source)" in paste

    def test_switching_with_existing_template_still_refills(self):
        """已选过模板再切回来时，不能提前 return 跳过重新回填。"""
        source = _read(EDITOR)
        switch_body = source.split("function switchFormat(")[1].split("\n}\n\n/** 这段 JSON")[0]
        assert "if (template.value) {\n      format.value = 'json'\n      return\n    }" not in switch_body


class TestCustomJsonValidationMessages:
    def test_plain_json_without_template_is_not_called_invalid(self):
        """合法 JSON 但没有内置模板 ≠ 不是 JSON 对象，文案要分开。"""
        source = _read(EDITOR)
        assert "这段 JSON 没有对应的内置模板" in source
        assert "当前内容不是 JSON 对象" in source

    def test_empty_object_is_rejected(self):
        source = _read(EDITOR)
        assert "至少要有一个字段，不能是空对象" in source

    def test_advanced_field_shows_error_border(self):
        source = _read(EDITOR)
        # 两个 border-color 类同时存在时谁生效取决于 CSS 生成顺序，不能靠叠加；
        # 高级字段必须和主字段一样用三元互斥。
        assert "'border-rose-400'" not in source
        assert (
            source.count(
                "errors[field.key] ? 'border-rose-500' : 'border-gray-300 focus:border-primary'"
            )
            >= 7
        )

    def test_clearing_resets_notice_and_reveal(self):
        source = _read(EDITOR)
        for name in ("clearTemplate", "clearFields"):
            body = source.split(f"function {name}(")[1].split("\n}")[0]
            assert "revealed.value = {}" in body
            assert "notice.value = ''" in body

    def test_switching_template_resets_reveal(self):
        """两个模板都可能有 password 字段，换模板后不能还停在明文状态。"""
        source = _read(EDITOR)
        body = source.split("function chooseTemplate(")[1].split("\n}")[0]
        assert "revealed.value = {}" in body


class TestCustomJsonKeyValueEditor:
    """自定义 JSON 从「一个空 textarea」改成「键值对表格 + 可切 JSON」后的契约。

    背景：生产环境里这个模板是别人在用，要求他们先学会 JSON 语法、再自己找
    语法错在哪，实际是劝退。表格是默认视图，JSON 视图保留给嵌套结构。
    """

    def test_key_value_view_is_the_default(self):
        source = _read(EDITOR)
        assert "const rawView = ref<'kv' | 'json'>('kv')" in source

    def test_number_columns_keep_numeric_type(self):
        # 端口这类字段必须是 465 而不是 "465"，否则对方的 schema 判类型不符
        source = _read(EDITOR)
        assert "Number.isFinite(num) ? num : text" in source

    def test_secret_checkbox_feeds_the_parent(self):
        # 自定义 JSON 没有模板声明，敏感字段只能来自逐行勾选；
        # 漏一个就等于让密码进列表摘要
        source = _read(EDITOR)
        raw_branch = source.split("if (current.raw) {")[1].split("return current.fields")[0]
        assert "row.secret" in raw_branch

    def test_row_keys_must_be_unique(self):
        source = _read(EDITOR)
        assert "字段名重复" in source

    def test_illegal_secret_field_name_is_blocked(self):
        # 后端拿同一套正则校验；这里不拦就是保存时才报错，用户不知道该改哪
        source = _read(EDITOR)
        assert "敏感字段名只能用字母开头的字母/数字/下划线" in source
        assert r"/^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/" in source

    def test_switching_back_to_table_requires_parsable_json(self):
        # 半截 JSON 不能覆盖表格：解析失败就留在 JSON 视图
        source = _read(EDITOR)
        assert "function switchRawView" in source
        body = source.split("function switchRawView(")[1].split("\n}")[0]
        assert "JSON.parse" in body
        assert "return" in body

    def test_switching_views_keeps_secret_flags(self):
        # 敏感标记不在 JSON 里，切视图时按字段名沿用，否则每切一次勾选就丢光
        source = _read(EDITOR)
        body = source.split("function hydrateRaw(")[1].split("\n}")[0]
        assert "remembered" in body
        assert "secret: remembered.get(key)" in body

    def test_row_error_uses_the_same_border_color(self):
        # 与字段错误同一个色号；两套色号叠加时谁生效取决于 CSS 生成顺序
        source = _read(EDITOR)
        assert "'border-rose-400'" not in source
        assert "rawRowErrors[index] ? 'border-rose-500'" in source

    def test_rotation_allows_editing_keys_but_warns_about_broken_references(self):
        """轮换不换结构——但这句话原来是用「锁死编辑器」实现的，反而把功能锁坏了。

        锁死之后，「自定义 JSON」的凭据在轮换时连字段都改不了：键名写错改不回来、
        新增一个字段也加不上，只能取消重来。所以现在改成**可编辑 + 说清代价**：
        键名能改，但界面明说改掉键名会让别处写好的 `{{ secret:标识.字段 }}` 引用失效。

        锁住的只剩「档位与模板」——换模板等于换掉一整份结构，那是另一回事。
        """
        source = _read(EDITOR)

        # 行级增删不再被锁
        for name in ("addRawRow", "removeRawRow"):
            body = source.split(f"function {name}(")[1].split("\n}")[0]
            assert "props.lockStructure" not in body, f"{name} 不该再因轮换早退"

        # 但代价必须写在原地，不能只靠用户自己想到
        assert "引用会失效" in source
        assert "键名请与原来保持一致" in source

        # 模板与档位仍然锁住
        assert "function chooseTemplate(id: string) {\n  if (props.lockStructure) return" in source
        assert "function switchFormat(next: 'text' | 'json') {\n  if (props.lockStructure) return" in source
        assert "轮换不能换模板" in source


class TestApiToolMappingReminder:
    """API 工具的参数来源要用户手动「保存工具」才写回，提示不能自己消失。

    这里和 MCP 不一样：MCP 的「由凭据提供」清单是工具表上的独立列，可以由
    bindings/batch 同批写；API 工具认的是参数自己的 `source`，它存在
    `parameter_schema` 里，而整份 schema 是用户在弹窗里编辑的草稿——批量接口直接
    覆盖会和用户其它未保存的改动打架。

    所以只能靠提示把人叫住：绑定落库了但没点「保存」，运行时 `generic_api`
    只注入 `source == 'credential'` 的行，凭据根本不会被用，而且不报错。
    """

    def test_hint_does_not_auto_dismiss(self):
        source = _read(ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue")
        assert "window.setTimeout(() => { mappingHint.value = '' }" not in source
        assert "还要点下面的「保存」把参数来源写回工具" in source

    def test_hint_has_a_manual_dismiss(self):
        source = _read(ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue")
        assert '@click="mappingHint = \'\'"' in source


class TestRuleSavedParamsAreMarkedAsRule:
    """多值面板按规则保存后，参数来源必须标成 `rule`。

    原来 `onMappingSaved` 一律把 `bound` 里的参数标成 `credential`（注释还停在
    「generic_api 只注入 source==='credential' 的行」，而后端早就支持 `'rule'` 了）。
    后果是连环的：`unboundNames` 按 `credential` 去查 `fixedBindingOf`——规则参数
    没有固定绑定——判成「未选，保存会被拦住」并红字提示，`ToolRegistry` 底部
    「保存」按钮随之禁用，用户配完规则反而存不了工具。
    """

    def test_panel_reports_which_source_was_saved(self):
        panel = (ROOT / "frontend/src/components/secrets/SecretFieldMappingPanel.vue").read_text(encoding="utf-8")
        assert "source: source.value," in panel

    def test_editor_maps_bound_params_to_the_right_source(self):
        editor = (ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue").read_text(encoding="utf-8")
        assert "payload.source === 'rule' ? 'rule' : 'credential'" in editor
        # 退回 literal 要覆盖两种来源，否则原本按规则匹配的参数改回模型填写后
        # 还停在 rule，同样会被判未绑定
        assert "(p.source === 'credential' || p.source === 'rule')" in editor

    def test_rule_save_failure_is_visible(self):
        """规则保存失败时弹窗不能提前关闭，错误也必须能被看到。"""
        editor = (ROOT / "frontend/src/components/system/ToolSystemParamEditor.vue").read_text(encoding="utf-8")
        # pickerError 原来只在 pickingName 弹窗里渲染，配规则时那个弹窗是关着的
        assert 'v-if="pickerError && !pickingName"' in editor


class TestMcpParamMarkPermissionGuard:
    def test_guard_dereferences_the_computed(self):
        """`!canPublishMcp` 对 ComputedRef 恒为 false —— 守卫等于不存在。

        卡片上的按钮靠模板自动解包隐藏了，但每个参数的 `<code>` 芯片也绑了
        点击/回车打开这个弹窗，漏写 `.value` 时无 publish 权限一样能进来。
        """
        registry = (ROOT / "frontend/src/components/system/McpServerRegistry.vue").read_text(encoding="utf-8")
        assert "!canPublishMcp.value" in registry
        assert "!canPublishMcp)" not in registry
