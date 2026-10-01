"""契约：个人中心 › 通知配置里「全局邮件服务」的可用性判据。

守的是一类**权限导致的误判**：该组件原先靠管理员接口 `GET /system/configs` 推断
「平台是否启用邮件服务」，普通用户调用拿到 403 被 catch 吞掉，界面于是对普通用户
断言「平台尚未启用邮件服务」——而服务其实启用着。

这类 bug 不报错、不 500、不影响功能（发信仍走真实全局服务），只是**对用户说了假话**，
所以只能靠契约测试钉住判据来源。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = (
    ROOT / "frontend/src/components/personal/NotificationConfigs.vue"
).read_text(encoding="utf-8")
SYSTEM_PY = (ROOT / "app/api/portal/endpoints/system.py").read_text(encoding="utf-8")


def test_enabled_flag_comes_from_auth_me():
    """启用判据必须取自 /auth/me 的 mail_service_available（全角色可用）。

    断言精确的赋值语句而不是「包含这个字符串」：文件里注释也会提到该字段名，
    按第一次出现匹配会匹配到注释，断言就失去了意义。
    """
    me_idx = COMPONENT.index("axios.get('/api/portal/auth/me')")
    read_idx = COMPONENT.index("res.data?.data?.mail_service_available")
    assert read_idx > me_idx, "标志位必须在 /auth/me 的响应处理里读取"
    assert "typeof res.data?.data?.mail_service_available === 'boolean'" in COMPONENT, (
        "只接受布尔值：字段缺失或后端未升级时按「未知」处理，不得当成未启用"
    )
    assert "mailServiceAvailable.value = null" in COMPONENT, (
        "未知态必须用 null 表达，与明确的 false（未启用）区分开"
    )


def test_admin_config_no_longer_decides_enabled():
    """管理员配置只能提供明细，不得再作为「是否启用」的判据。

    email_service_enabled 是管理员接口的字段：普通用户读不到它，一旦拿它判断启用状态，
    403 就会被误解成「平台未启用」——正是本 bug 的成因。
    """
    assert "email_service_enabled" not in COMPONENT, (
        "不得用管理员配置中的 email_service_enabled 判断启用状态，"
        "否则普通用户会因 403 被误判为「平台未启用」"
    )


def test_unavailable_copy_is_gated_by_flag_not_by_permission():
    """「尚未启用」文案只能由标志位为假触发，不能由「读不到配置」触发。"""
    # 三态：未启用文案只在**明确** false 时出现（未知态另有中性说明）
    flag_idx = COMPONENT.index('v-if="mailServiceAvailable !== false"')
    copy_idx = COMPONENT.index("平台尚未启用邮件服务")
    assert flag_idx < copy_idx, "未启用文案必须位于标志位分支之后"
    between = COMPONENT[flag_idx:copy_idx]
    assert "v-else" in between, "未启用文案必须是标志位为假时的 v-else 分支"


def test_smtp_detail_rows_are_gated_by_detail_presence():
    """主机/端口/发件人明细只对能读到管理员配置的角色展示（方案 A：不暴露内网地址）。"""
    detail_idx = COMPONENT.index('v-if="globalEmailDetail"')
    host_idx = COMPONENT.index("globalEmailDetail.host")
    assert detail_idx < host_idx, "SMTP 明细必须包在 globalEmailDetail 存在性判断之内"
    assert "已由平台统一配置" in COMPONENT, "读不到明细时应提示已统一配置，而非未启用"


def test_test_button_hint_uses_flag():
    """「测试连通性」的未启用提示也必须用同一判据，避免 UI 与自身行为矛盾。"""
    idx = COMPONENT.index("const emailTestBlockedReason = computed")
    window = COMPONENT[idx : idx + 800]
    assert "mailServiceAvailable.value === false" in window, (
        "全局来源的置灰原因必须以权威标志位为准（且只在明确 false 时）"
    )
    assert "customSmtpConfigured" in window, "自定义来源也要给出各自的置灰原因"
    assert "globalEmailOverview" not in window, "不得再依赖旧的、基于管理员配置的推导"


def test_admin_config_endpoint_stays_admin_only():
    """钉住本 bug 的前提：该接口本就要求系统配置权限。

    防止有人为了「修」这个显示问题去放宽接口权限 —— 那会把 SMTP 主机、账号等
    配置暴露给所有登录用户。
    """
    idx = SYSTEM_PY.index('@router.get("/configs"')
    signature = SYSTEM_PY[idx : SYSTEM_PY.index("):", idx)]
    assert 'require_permission("menu", "menu:system:config")' in signature, (
        "GET /system/configs 必须保持系统配置权限，前端不得依赖它判断可用性"
    )
