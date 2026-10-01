"""忘记密码（邮件找回）前端契约测试。

这些断言守的是「后端已实现但前端漏接」这一类静默失败：路由少了 public 会让邮件链接
永远跳登录页、入口无条件显示会让用户在未配置邮件服务时提交后拿到 503、
优先展示自己写的文案会让前端的防枚举表述与后端漂移。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGIN = (ROOT / "frontend/src/views/Login.vue").read_text(encoding="utf-8")
ROUTER = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
RESET = (ROOT / "frontend/src/views/ResetPassword.vue").read_text(encoding="utf-8")
PERSONAL = (ROOT / "frontend/src/views/PersonalCenter.vue").read_text(encoding="utf-8")
SYSCONFIG = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")
CHECKLIST = (
    ROOT / "frontend/src/components/system/DeploymentChecklist.vue"
).read_text(encoding="utf-8")


def test_reset_route_exists_and_is_public():
    assert "path: '/reset-password'" in ROUTER
    idx = ROUTER.index("path: '/reset-password'")
    window = ROUTER[idx : idx + 400]
    # 邮件链接进来时用户必然未登录；缺了 public，守卫会把他重定向到登录页
    assert "public: true" in window
    assert "ResetPassword.vue" in window


def test_login_reads_flag_from_public_config():
    assert "password_reset_available" in LOGIN
    assert "/api/portal/auth/config/public" in LOGIN


def test_login_entry_is_gated_by_flag():
    """入口必须受开关控制，且只出现在本地账号页签。

    断言跟着结构走：两个辅助入口合并到同一行后，「页签」判定上移到容器
    （链接自身的 v-if 只管开关），因此这里分别锚定容器与链接，而不是继续假设
    「忘记密码」自己那个 div 上同时挂了两条判定。
    """
    link_idx = LOGIN.index('to="/reset-password"')
    assert 'v-if="passwordResetAvailable"' in LOGIN[max(0, link_idx - 200) : link_idx]

    anchor = LOGIN.index("底部辅助入口")
    container = LOGIN[anchor : LOGIN.index("</form>", link_idx)]
    assert "activeTab === 'password'" in container, "SSO 登录的密码不归本平台管，入口应只出现在本地账号页签"
    assert "registrationEnabled || passwordResetAvailable" in container


def test_login_aux_entries_share_one_row_and_degrade_gracefully():
    """两个入口同排一行且整体居中；分隔符只在两项都在时出现。

    背景：原先两个独立 div 各自 text-center + pt-1，间距与「按钮到第一行」相同，
    「忘记密码？」看起来像一个脱离出来的独立区块。改为一行后必须保证：
    ① 只剩一个入口时仍居中（用 justify-center，不能用 justify-between）；
    ② 不会只剩一根孤立的竖线。
    """
    anchor = LOGIN.index("底部辅助入口")
    container = LOGIN[anchor : LOGIN.index("</form>", anchor)]
    assert "justify-center" in container
    assert "justify-between" not in container

    sep = container.index("h-3 w-px bg-slate-200")
    assert "registrationEnabled && passwordResetAvailable" in container[max(0, sep - 250) : sep]


def test_reset_page_has_two_modes():
    assert "route.query.token" in RESET
    assert "password-reset/request" in RESET
    assert "password-reset/confirm" in RESET


def test_reset_page_prefers_server_message():
    """防枚举文案的权威定义在服务端；前端另写一句会随服务端修改而漂移。"""
    assert "response.data.message" in RESET or "response.data?.message" in RESET


def test_reset_page_clears_local_session_after_success():
    """重置成功会吊销全部会话，本机也必须清掉本地身份快照。"""
    assert "clearUserSession" in RESET


def test_change_password_logs_out_current_device():
    """PUT /auth/password 现在会吊销全部会话（含当前设备），前端必须跟着跳登录页。"""
    idx = PERSONAL.index("'/api/portal/auth/password'")
    window = PERSONAL[idx : idx + 1400]
    assert "clearUserSession" in window, "改密成功后必须清本地凭据"
    assert "/login" in window, "改密成功后必须回登录页重新认证"


def test_download_url_prefix_copy_mentions_reset_link():
    """该前缀同时服务重置链接，说明必须写清楚，否则管理员不会把两者联系起来。"""
    assert "重置链接" in SYSCONFIG
    idx = SYSCONFIG.index("download_url_prefix: '")
    short_desc = SYSCONFIG[idx : idx + 400]
    assert "重置链接" in short_desc or "重置" in short_desc
    assert "重置" in CHECKLIST


def test_config_search_covers_code_side_descriptions():
    """本项的新说明只写在代码里（无迁移），搜索必须覆盖 configShortDescriptions，
    否则用户搜「找回密码」会找不到这个配置。"""
    idx = SYSCONFIG.index("const match =")
    window = SYSCONFIG[idx : idx + 500]
    assert "configShortDescriptions[item.key]" in window


def test_existing_download_prefix_migration_contract_untouched():
    """V129 / V29 里的历史文案保持不变：本特性零迁移，不去改已发布的迁移。"""
    mysql = (ROOT / "db-prod/V129-add_download_url_prefix_config.sql").read_text(
        encoding="utf-8"
    )
    pg = (ROOT / "db-prod-pg/V29-add_download_url_prefix_config.sql").read_text(
        encoding="utf-8"
    )
    for text, path in ((mysql, "db-prod/V129"), (pg, "db-prod-pg/V29")):
        assert "download_url_prefix" in text, path
