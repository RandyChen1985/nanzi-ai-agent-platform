"""契约：登录页「申请账号」入口、系统配置开关、用户管理「待审核」页签。

这些断言针对 Vue 单文件组件的源码结构与关键调用点。它们不替代真实浏览器验证，
但能挡住「入口被删掉」「开关没接上」「待审核页签泄漏了无关操作」这类静默回归。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LOGIN = (ROOT / "frontend/src/views/Login.vue").read_text(encoding="utf-8")
SYSTEM_CONFIG = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")
USERS = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")

CONFIG_KEY = "user_registration_enabled"


def _review_block() -> str:
    """截取 Users.vue 里「待审核视图」模板片段，用于断言它没有混入无关操作。"""
    start = USERS.index("待审核视图")
    end = USERS.index("用户列表视图", start)
    return USERS[start:end]


# --------------------------------------------------------------------------- #
# 登录页
# --------------------------------------------------------------------------- #

def test_login_reads_registration_switch_from_public_config():
    assert "const registrationEnabled = ref(false)" in LOGIN
    assert "user_registration_enabled" in LOGIN
    # 必须取自公开配置接口（登录页未登录即可读取）
    assert "/api/portal/auth/config/public" in LOGIN


def test_login_shows_register_entry_only_when_enabled():
    """注册入口仅在开关开启、且处于本地账号页签时出现。

    注：底部两个辅助入口（申请账号 / 忘记密码）合并为同一行后，原先「开关 + 页签」
    写在一条 v-if 里的结构没了——页签判定上移到容器、开关留在入口自己身上，因此这里
    改成分开断言两处判定，而不是继续钉死那条合并写法（断言本意未变）。
    """
    assert "申请账号" in LOGIN
    assert "openRegister" in LOGIN

    # 入口自身的开关判定。锚在按钮的 @click 上：「申请账号」在右侧面板标题里也出现过，
    # 用文字做锚会定位到标题而不是入口
    btn = LOGIN.index('@click="openRegister"')
    assert 'v-if="registrationEnabled"' in LOGIN[max(0, btn - 300) : btn], "注册入口必须受开关控制"

    # 页签判定在承载两个入口的容器上
    anchor = LOGIN.index("底部辅助入口")
    container = LOGIN[anchor : LOGIN.index("</form>", anchor)]
    assert "activeTab === 'password'" in container


def test_login_register_form_has_required_fields():
    for field in ("user_name", "real_name", "password", "confirmPassword", "remark"):
        assert f"registerForm.{field}" in LOGIN, f"注册表单缺少字段 {field}"
    assert "用户姓名" in LOGIN
    assert "备注（可选）" in LOGIN
    assert "确认密码" in LOGIN


def test_login_register_posts_to_register_endpoint():
    assert "axios.post('/api/portal/auth/register'" in LOGIN
    assert "提交注册申请" in LOGIN


def test_login_register_name_rule_matches_backend():
    # 与后端 USER_NAME_PATTERN 同规则：3–32 位、字母开头
    assert r"/^[A-Za-z][A-Za-z0-9._-]{2,31}$/" in LOGIN
    assert "账号名需为 3–32 位、以字母开头" in LOGIN


def test_login_surfaces_duplicate_name_as_field_level_error():
    """重名必须挂在账号名输入框下方，而不是丢进顶部通用错误条。"""
    assert "registerUserNameError" in LOGIN
    assert "message.includes('已被占用')" in LOGIN
    assert "registerUserNameError.value = message" in LOGIN
    # 字段级提示渲染在账号名输入框附近
    assert 'v-if="registerUserNameError"' in LOGIN
    assert "text-red-600 ml-1" in LOGIN


def test_login_register_success_state_and_back_to_login():
    assert "注册申请已提交" in LOGIN
    assert "backToLogin" in LOGIN
    assert "返回登录" in LOGIN


# --------------------------------------------------------------------------- #
# 账号名可用性预检（边输边查）
# --------------------------------------------------------------------------- #

def test_login_checks_user_name_availability_on_input():
    """不能等到提交才告诉用户账号名被占用。"""
    assert "/api/portal/auth/register/available" in LOGIN
    assert "@input=\"onNameInput\"" in LOGIN
    assert "@blur=\"onNameBlur\"" in LOGIN
    # 防抖：逐字符打字不应逐字符发请求
    assert "NAME_CHECK_DEBOUNCE_MS" in LOGIN
    assert "setTimeout(" in LOGIN


def test_login_name_check_discards_stale_responses():
    """慢响应不得覆盖新输入的结论。"""
    assert "nameCheckSeq" in LOGIN
    assert "if (seq !== nameCheckSeq) return" in LOGIN


def test_login_name_check_degrades_when_rate_limited():
    """预检被限流时静默退回「提交时校验」，不能把用户挡在注册之外。"""
    assert "nameCheckDisabled" in LOGIN
    assert "e.response?.status === 429" in LOGIN


def test_login_name_check_renders_three_states():
    assert "nameCheckState" in LOGIN
    for state in ("available", "taken", "checking"):
        assert f"'{state}'" in LOGIN
    assert "可以使用" in LOGIN


def test_login_name_check_resets_on_leaving_register_view():
    assert "resetNameCheck" in LOGIN
    # 开表单 / 返回登录 / 切 Tab / 卸载都要复位，避免残留上一次的结论
    assert LOGIN.count("resetNameCheck") >= 4


def test_login_taken_name_short_circuits_submit():
    """预检已判定被占用时直接拦下，不必再等一次往返。"""
    assert "nameCheckState.value === 'taken'" in LOGIN
    assert "registerUserNameError.value = nameCheckMessage.value" in LOGIN


# --------------------------------------------------------------------------- #
# 移动端适配
# --------------------------------------------------------------------------- #

def test_login_shell_uses_dynamic_viewport_height():
    """100vh 在移动端含被地址栏遮挡的区域，会把面板底部裁掉。"""
    assert "login-shell" in LOGIN
    assert "height: 100vh; height: 100dvh;" in LOGIN


def test_login_panel_scrolls_when_content_overflows():
    """注册表单 5 个字段高于视口时必须能在面板内滚动。

    父容器是 overflow-hidden，所以 min-h-0 不能少：否则 flex 子项被内容撑高、
    再被父级裁掉，连滚动条都不会出现。
    """
    assert "min-h-0 overflow-y-auto flex flex-col bg-white" in LOGIN
    # 移动端从顶部排布，lg 起才垂直居中
    assert "py-6 xl:py-0 lg:justify-center" in LOGIN
    assert "flex-1 flex flex-col justify-center" not in LOGIN


def test_login_register_inputs_are_mobile_friendly():
    """iOS 对 font-size < 16px 的输入框会在聚焦时自动放大页面。"""
    assert "py-3 text-base text-slate-900 outline-none" in LOGIN
    assert "sm:py-2.5 sm:text-sm" in LOGIN
    # 账号名输入框关掉自动大写/纠错，移动端键盘不会把首字母改写或自作主张纠错
    assert 'autocapitalize="none"' in LOGIN
    assert 'autocorrect="off"' in LOGIN
    assert 'spellcheck="false"' in LOGIN


def test_login_mobile_header_aligns_with_form():
    """移动端品牌区的左右内边距必须与表单一致，否则左边线错位。"""
    assert 'lg:hidden pt-8 px-6 pb-0' in LOGIN


# --------------------------------------------------------------------------- #
# 系统配置 › 常规设置
# --------------------------------------------------------------------------- #

def test_system_config_exposes_registration_switch_in_general_group():
    assert CONFIG_KEY in SYSTEM_CONFIG
    # 排序：与 password_expire_days 相邻，落在 general 分组
    assert "'password_expire_days',\n      'user_registration_enabled'," in SYSTEM_CONFIG
    # 复用布尔开关渲染分支（放在数组首位，避免打断既有 agentscope 契约的收尾断言）
    assert "'user_registration_enabled', 'embedchat_watermark_enabled'" in SYSTEM_CONFIG
    # 左侧简短说明
    assert f"{CONFIG_KEY}: '账号自主注册申请开关" in SYSTEM_CONFIG


# --------------------------------------------------------------------------- #
# 用户管理 › 待审核
# --------------------------------------------------------------------------- #

def test_users_page_has_review_tab_with_pending_badge():
    assert "activeView" in USERS
    assert "用户列表" in USERS
    assert "待审核" in USERS
    assert "/api/portal/management/users/pending-count" in USERS
    assert "pendingCount" in USERS
    assert "pendingCount > 0" in USERS


def test_users_review_view_filters_pending_status():
    """待审核页签仍按 status=2 拉取，切回用户列表时重置筛选。

    注意：用户列表**不再**展示待审核账号（这正是本用例原先第二个断言所主张的行为，
    已按需求移除，见 test_user_list_tab_excludes_pending_review 与
    test_status_dropdown_does_not_offer_pending_review），所以这里只钉待审核页签自身。
    """
    assert 'statusFilter.value = "2"' in USERS
    # 切回用户列表必须清空筛选，否则会把 status=2 带进主列表
    assert 'statusFilter.value = ""' in USERS


def test_users_review_list_only_exposes_approve_and_reject():
    block = _review_block()
    assert "openApproveDialog" in block
    assert "openRejectDialog" in block
    assert "通过并启用" in block
    assert "禁用" in block
    # 待审核视图不得混入批量勾选 / API Key / 角色编辑等主表格操作
    for leaked in ("selectedUserIds", "viewApiKey", "editUser(", "regenerateApiKey", "toggleSelectUser"):
        assert leaked not in block, f"待审核视图泄漏了主表格操作：{leaked}"


def test_users_approve_dialog_assigns_optional_business_roles():
    assert "reviewRoleIds" in USERS
    assert "role_ids: reviewRoleIds.value" in USERS
    # 角色列表拿不到时要降级为纯通过，不能卡住审核
    assert "无可分配的业务角色" in USERS


def test_users_reject_dialog_warns_name_is_permanently_taken():
    assert "永久占用" in USERS
    assert "该用户无法用同名账号重新申请" in USERS


def test_users_review_actions_respect_edit_permission():
    """两个审核按钮都受 element:user:edit 保护，无权限时展示只读提示。"""
    block = _review_block()
    assert 'v-if="canEditUser"' in block
    assert "无审核权限" in block


def test_users_review_refreshes_badge_after_action():
    assert "fetchPendingCount()" in USERS
    # 审核动作后同时刷新列表与徽章
    assert "await Promise.all([fetchUsers(), fetchPendingCount()]);" in USERS


# --------------------------------------------------------------------------- #
# 用户列表不得混入待审核账号
# --------------------------------------------------------------------------- #

def test_user_list_tab_excludes_pending_review():
    """「用户列表」拉取时必须排除待审核（它们只应出现在「待审核」页签）。

    关键是不能写成无条件排除：待审核视图正是靠 status=2 拉数据的，
    无条件排除会让那一页变成空列表。所以断言两个条件都必须紧邻该参数。
    """
    assert "params.exclude_status = 2" in USERS
    idx = USERS.index("params.exclude_status = 2")
    guard = USERS[max(0, idx - 400):idx]
    assert 'activeView.value === "list"' in guard, "排除待审核必须只在用户列表视图生效"
    assert 'statusFilter.value !== "2"' in guard, "待审核视图按 status=2 拉取，不能被排除掉"


def test_status_dropdown_does_not_offer_pending_review():
    """用户列表的状态筛选不再提供「待审核」。

    那些账号在用户列表里操作按钮都用不了，列出来只会误导管理员；
    留着这个选项还会让筛选结果为空，看起来像功能坏了。
    """
    assert '<option value="2">待审核</option>' not in USERS
