"""前端契约：邮箱字段必须在注册/管理员/个人中心三处都露出，且移动端适配不退化。"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_shared_user_type_has_email():
    src = _read("frontend/src/api/portal.ts")
    assert "email" in src


def test_register_form_has_email_input_with_mobile_classes():
    src = _read("frontend/src/views/Login.vue")
    assert 'v-model="registerForm.email"' in src or "registerForm.email" in src
    # 移动端适配类名与其它注册输入框一致
    assert "px-4 py-3 text-base" in src and "sm:py-2.5 sm:text-sm" in src


def test_register_payload_includes_email():
    src = _read("frontend/src/views/Login.vue")
    assert "email:" in src


def test_users_view_has_email_in_form_and_list():
    src = _read("frontend/src/views/Users.vue")
    assert "form.email" in src or "userForm.email" in src or ".email" in src
    assert "邮箱" in src


def test_personal_center_supports_editing_email():
    src = _read("frontend/src/views/PersonalCenter.vue")
    assert "email" in src
    assert "/me/profile" in src or "updateMyProfile" in src


# --------------------------------------------------------------------------- #
# Task 11: 系统配置「邮件服务」分组
# --------------------------------------------------------------------------- #
def test_email_category_registered_after_other():
    src = _read("frontend/src/views/SystemConfig.vue")
    # 顺序数组里 email 必须在 other 之后
    marker = "const order = ["
    start = src.index(marker)
    order_line = src[start:src.index("]", start)]
    assert "'email'" in order_line, "分组顺序数组必须包含 email"
    assert order_line.index("'email'") > order_line.index("'other'"), "邮件服务必须在其他参数之后"


def test_email_category_has_label_icon_and_subtitle():
    src = _read("frontend/src/views/SystemConfig.vue")
    assert "邮件服务" in src
    for marker in ("const map: Record<string, { short: string; full: string }>",
                   "const CATEGORY_ICONS",
                   "const getGroupSubtitle"):
        assert marker in src


def test_email_boolean_key_registered_in_toggle_list():
    src = _read("frontend/src/views/SystemConfig.vue")
    assert "'email_service_enabled'" in src, "布尔开关必须登记到硬编码清单，否则退化成文本框"


def test_email_security_key_registered_as_select():
    src = _read("frontend/src/views/SystemConfig.vue")
    assert "'email_smtp_security'" in src


def test_email_group_has_test_button():
    src = _read("frontend/src/views/SystemConfig.vue")
    assert "configs/email/test" in src, "必须有测试发送按钮"


# --------------------------------------------------------------------------- #
# Task 15: 审核发信反馈与重发
# --------------------------------------------------------------------------- #
def test_users_view_reports_mail_result_and_offers_resend():
    src = _read("frontend/src/views/Users.vue")
    assert "email_sent" in src, "审核后必须区分通知邮件是否发送成功"
    assert "notify-review-mail" in src, "必须有重发入口"


# --------------------------------------------------------------------------- #
# Task 17: 个人中心通知来源分层与抄送自己
# --------------------------------------------------------------------------- #
def test_notification_configs_has_source_radio_and_include_self():
    src = _read("frontend/src/components/personal/NotificationConfigs.vue")
    assert "smtp_source" in src, "必须有全局/自定义来源选择"
    assert "include_self" in src, "必须有抄送自己开关"
    assert "同时发送到我的邮箱" in src
    # 默认勾选
    assert "include_self: true" in src or "include_self ?? true" in src or "include_self !== false" in src


def test_notification_configs_hides_custom_smtp_when_global():
    src = _read("frontend/src/components/personal/NotificationConfigs.vue")
    assert "smtp_source === 'custom'" in src or "smtp_source === \"custom\"" in src


def test_notification_config_type_declares_source_and_include_self():
    src = _read("frontend/src/api/portal.ts")
    assert "smtp_source?: 'global' | 'custom'" in src
    assert "include_self?: boolean" in src


# --------------------------------------------------------------------------- #
# Task 18: 邮箱参与第三方用户同步
# --------------------------------------------------------------------------- #
def test_user_sync_field_map_includes_email():
    src = _read("app/schemas/user_sync.py")
    assert "email" in src


def test_user_sync_drawer_offers_email_mapping():
    src = _read("frontend/src/components/ThirdPartyUserSyncDrawer.vue")
    assert "email" in src


# --------------------------------------------------------------------------- #
# 个人中心「测试连通性」可用性提示（回归：2026-10-01 用户实测 400）
#
# 用户选「全局」（默认值）点测试连通性，后端报的却是「SMTP 服务地址、账号和授权码
# 不能为空」——而这三个输入框在前端根本没渲染。根因是后端 _test_email 自成一套、
# 不认 smtp_source（已修复并有用例钉住）。前端这半边必须保证：平台服务不可用时
# 提前说清楚，且判定口径与后端一致（开关关闭 或 没填主机 都算不可用），
# 否则界面显示「可用」、后端拒绝，用户又会撞上同一个困惑。
# --------------------------------------------------------------------------- #
def test_personal_center_warns_when_global_mail_service_unavailable():
    src = _read("frontend/src/components/personal/NotificationConfigs.vue")
    assert "平台尚未启用邮件服务" in src, "全局服务不可用时必须提前告知，而不是等测试报错"
    assert "改用自定义 SMTP" in src, "要给出可执行的下一步"


def test_global_overview_requires_both_switch_and_host():
    """概览判定必须同时要求「开关打开」与「填了主机」，与后端保持一致。

    只看主机就会在管理员填了地址但忘记打开开关时显示成可用，而后端
    load_global_smtp_settings 在开关关闭时返回 None —— 界面与行为脱节。
    """
    src = _read("frontend/src/components/personal/NotificationConfigs.vue")
    assert "email_service_enabled" in src, "概览必须读取开关状态"
    assert "!enabled || !host" in src, "开关关闭或未填主机都应显示为不可用"


# --------------------------------------------------------------------------- #
# 系统配置「邮件服务」分组：开关排第一，且关闭时只显示开关
# 与 knowledge_base_enabled 同一惯例（既有分类就是这么做的）。
# --------------------------------------------------------------------------- #
def _email_visible_items_branch():
    """抽出 getVisibleItems 里的 email 分支源码，避免全文件子串误匹配。"""
    import re
    src = _read("frontend/src/views/SystemConfig.vue")
    m = re.search(r"if \(category === 'email'\) \{.*?\n  \}", src, re.S)
    assert m, "getVisibleItems 必须有 email 分支，否则顺序与隐藏都不生效"
    return m.group(0)


def test_email_service_switch_is_listed_first():
    branch = _email_visible_items_branch()
    import re
    m = re.search(r"const order = \[(.*?)\]", branch, re.S)
    assert m, "必须有显式的展示顺序"
    keys = re.findall(r"'([^']+)'", m.group(1))
    assert keys[0] == "email_service_enabled", (
        f"启用开关必须排第一，便于先判断再展开，实际首位是 {keys[0]!r}"
    )
    # 顺序表要覆盖全部 8 个键，漏掉的会退化成按 key 字母序排到末尾
    for k in ["email_smtp_host", "email_smtp_port", "email_smtp_security",
              "email_smtp_user", "email_smtp_password",
              "email_from_address", "email_sender_name"]:
        assert k in keys, f"顺序表缺少 {k}，它会掉到分组末尾"


def test_email_details_hidden_until_service_enabled():
    branch = _email_visible_items_branch()
    # 关闭时只保留开关自己；不能连开关一起隐藏，否则用户没法再打开它
    assert "if (!enabled) {" in branch, "必须有「未启用」分支"
    assert "filter(x => x.key === 'email_service_enabled')" in branch, (
        "未启用时应当只保留启用开关，隐藏其余 SMTP 明细"
    )
    # 判定口径与其它布尔开关一致（'true' / 'false' 字符串）
    assert "=== 'true'" in branch, "开关判定口径必须与其它布尔配置一致"


def test_config_search_respects_visibility_rules():
    """搜索也必须走 getVisibleItems，否则会搜到界面上不存在、点进去也定位不到的参数。"""
    src = _read("frontend/src/views/SystemConfig.vue")
    import re
    m = re.search(r"const configSearchResults = computed\(\(\) => \{.*?\n\}\)", src, re.S)
    assert m, "找不到 configSearchResults"
    body = m.group(0)
    assert "getVisibleItems(items, cat)" in body, (
        "搜索必须复用可见性判定；直接遍历 configGroups 会绕开「关闭时隐藏」的规则"
    )


# --------------------------------------------------------------------------- #
# 发送测试邮件必须是应用内弹窗，不能再用浏览器原生输入弹窗
#
# 用户实测反馈「这个测试怎么是 alert 弹框」——原先用浏览器原生输入弹窗实现，
# 视觉与平台脱节，且做不了格式校验与发送中状态、部分浏览器还会拦截。
# --------------------------------------------------------------------------- #
def test_email_test_uses_in_app_modal_not_native_dialog():
    src = _read("frontend/src/views/SystemConfig.vue")
    assert "window.prompt" not in src, "不得再用浏览器原生输入弹窗，应使用应用内模态"
    assert "window.alert" not in src and "window.confirm" not in src
    # 应用内模态应具备：开关状态、可聚焦输入、发送中态、提交处理
    for token in ["showEmailTestModal", "emailTestInputRef", "submitEmailTest",
                  "openEmailTestModal", "closeEmailTestModal"]:
        assert token in src, f"缺少 {token}，测试发送的应用内弹窗不完整"


def test_email_test_modal_validates_before_sending():
    """填了收件人就要先在前端挡掉明显笔误，留空仍允许（后端回落到发件人地址）。"""
    src = _read("frontend/src/views/SystemConfig.vue")
    import re
    m = re.search(r"const submitEmailTest = async \(\) => \{.*?\n\}", src, re.S)
    assert m, "找不到 submitEmailTest"
    body = m.group(0)
    assert "EMAIL_TEST_RE.test(to)" in body, "必须先做格式校验再发请求"
    assert "if (to &&" in body, "留空必须被允许（后端会发给发件人地址自测）"


# --------------------------------------------------------------------------- #
# 个人中心邮件「测试连通性」的结果必须留在卡片里
#
# 用户实测「后端错误，前端也没有什么提示」：失败原因只走全局 toast，3.5 秒即消失，
# 用户连点多次也只看到 400 刷屏。测试结果改为写入卡片内的持久提示。
# --------------------------------------------------------------------------- #
def _notification_configs_src():
    return _read("frontend/src/components/personal/NotificationConfigs.vue")


def test_email_test_only_self_hint_is_shown_next_to_button():
    """「测试只发给自己」必须在界面上说清楚。

    后端已把连通性测试的收件人收敛为调用者本人（防止借平台全局 SMTP 向任意地址
    发信），若界面不说，用户会以为测试能验证「其他收件人」是否配对了。
    断言必须锚定在按钮附近，全文任意位置出现不算——否则别处写一句同样的话就能蒙混。
    """
    src = _notification_configs_src()
    hint = src.find("连通性测试只发到「我的邮箱」")
    assert hint != -1, "邮件渠道「测试连通性」旁必须说明只发给自己"
    button = src.find("testConfig('email')", hint)
    assert button != -1 and button - hint < 600, "该提示必须紧邻邮件渠道的测试按钮"


def test_email_test_result_is_shown_in_card_not_only_toast():
    src = _notification_configs_src()
    assert "emailTestResult" in src, "邮件测试结果必须持久留在卡片内"
    # 成功与失败两条路径都要写入
    assert src.count("emailTestResult.value = {") >= 2, "成功与失败都要留下结果"
    # 模板里要有渲染
    assert 'v-if="emailTestResult"' in src, "结果必须真的渲染出来"
    assert "emailTestResult.ok ?" in src, "成功与失败要能区分"


def test_email_test_failure_surfaces_backend_detail():
    """失败原因要原样展示后端 detail —— 它通常就写着下一步该做什么。"""
    src = _notification_configs_src()
    import re
    m = re.search(r"const testConfig = async \(channel: string\) => \{.*?\n\}", src, re.S)
    assert m, "找不到 testConfig"
    body = m.group(0)
    assert "error.response?.data?.detail" in body, "必须取后端 detail 作为展示文案"
    assert "emailTestResult.value = { ok: false, message }" in body


def test_email_test_result_cleared_when_source_changes():
    """切换来源后旧结论不再适用，必须清掉，否则会显示针对另一种来源的失败原因。"""
    src = _notification_configs_src()
    import re
    m = re.search(
        r"watch\(\s*\(\) => configs\.value\.email\?\.smtp_source,.*?\n\)", src, re.S
    )
    assert m, "必须有来源变化的 watch"
    assert "emailTestResult.value = null" in m.group(0)


def test_email_test_button_not_disabled_by_unreadable_global_config():
    """**守住一个刻意的取舍**：不能因为读不到全局配置就置灰测试按钮。

    globalEmailOverview 依赖 GET /api/portal/system/configs；无该权限的用户会拿到空，
    若据此置灰，本来可用的全局邮件服务也会被锁死测不了（功能性回退）。让用户点下去、
    以测试结果为准才可靠。
    """
    src = _notification_configs_src()
    import re
    m = re.search(
        r":disabled=\"testingChannel\['email'\] \|\| savingChannel\['email'\][^\"]*\"",
        src,
    )
    assert m, "找不到邮件测试按钮的 disabled 表达式"
    assert "emailTestUnavailable" not in m.group(0), (
        "不得把 emailTestUnavailable 并入 disabled：读不到配置可能只是权限问题"
    )


# --------------------------------------------------------------------------- #
# 邮件服务未启用时，「发送测试邮件」必须置灰
#
# 用户指出：开关关着的时候按钮还能点，点下去后端必然拒绝（刷 400），
# 用户却得不到有用信息。判定依据是本页同一个开关，值确定可读。
# --------------------------------------------------------------------------- #
def _system_config_src():
    return _read("frontend/src/views/SystemConfig.vue")


def test_email_test_button_disabled_when_service_off():
    src = _system_config_src()
    import re
    # 置灰必须按**已保存**的开关判断：后端测试接口只读库里已保存的配置，
    # 用未保存的内存值判断会出现两种错配——刚把开关拨开（未保存）按钮就可点、
    # 点下去必然失败；已保存为启用但拨回关（未保存）按钮又被错误置灰。
    m = re.search(r":disabled=\"emailTestSending \|\| !emailServiceEnabledSaved\"", src)
    assert m, "邮件服务未启用时，「发送测试邮件」必须置灰，且依据是已保存的开关"
    # 置灰时必须说明原因，否则用户不知道去哪打开
    assert "邮件服务未启用" in src, "置灰要给出可操作的原因"
    assert "email_service_enabled" in src


def test_email_test_button_uses_saved_switch_not_unsaved_value():
    """置灰依据必须是已保存的值（originalConfigs），而不是可编辑的内存值。"""
    src = _system_config_src()
    import re
    m = re.search(r"const emailServiceEnabledSaved = computed\(\(\) => \{.*?\n\}\)", src, re.S)
    assert m, "找不到 emailServiceEnabledSaved"
    body = m.group(0)
    assert "originalConfigs.value['email_service_enabled']" in body, "必须读已保存的开关值"


def test_email_service_enabled_uses_string_convention():
    """本文件里开关值统一是字符串 'true'/'false'（见开关 @click 赋值），不能按布尔判断。"""
    src = _system_config_src()
    import re
    m = re.search(r"const emailServiceEnabled = computed\(\(\) => \{.*?\n\}\)", src, re.S)
    assert m, "找不到 emailServiceEnabled"
    body = m.group(0)
    assert "(item?.value ?? 'false') === 'true'" in body, "必须沿用 'true'/'false' 字符串口径"
    assert "email_service_enabled" in body


def test_open_email_test_modal_guards_against_disabled_service():
    """禁用不该是唯一防线，打开动作本身也要拦住（同样按已保存的值）。"""
    src = _system_config_src()
    import re
    m = re.search(r"const openEmailTestModal = \(\) => \{.*?\n\}", src, re.S)
    assert m, "找不到 openEmailTestModal"
    assert "if (!emailServiceEnabledSaved.value) return" in m.group(0)
