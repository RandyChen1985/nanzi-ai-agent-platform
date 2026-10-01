"""契约：任务/巡检的结果通知渠道「能不能勾」由后端就绪接口裁决。

守的是一类**界面说假话**的 bug：前端按「必须填 smtp_host + smtp_user」自行判断
邮件渠道，而个人中心的邮件配置有来源分层——选「使用全局邮件服务」时个人配置里
本就没有 host/user，于是完全可用的渠道被锁死在灰复选框上。

判定必须收敛到后端（那里的判据与发信路径同源，包括前端根本看不到的
「平台全局邮件服务是否启用」）。本测试钉住：前端不再自己推断邮件可用性。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TASK_CENTER = (ROOT / "frontend/src/views/TaskCenter.vue").read_text(encoding="utf-8")
INSPECTION_MODAL = (
    ROOT / "frontend/src/components/metadata/MetadataCronInspectionModal.vue"
).read_text(encoding="utf-8")
ENDPOINT = (ROOT / "app/api/portal/endpoints/notifications.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "app/services/notification_service.py").read_text(encoding="utf-8")

# 旧的（错误的）邮件判定：要求个人配置里必须有 smtp_host 与 smtp_user。
# 全局来源下这两个字段必然为空，正是把可用渠道锁死的那一行。
LEGACY_EMAIL_GUARD = "String(cfg.smtp_host || '').trim() && String(cfg.smtp_user || '').trim()"


def test_both_components_ask_the_backend_for_readiness():
    for name, source in (("TaskCenter", TASK_CENTER), ("MetadataCronInspectionModal", INSPECTION_MODAL)):
        assert "/api/portal/notifications/readiness" in source, f"{name} 必须使用后端就绪接口"
        assert "notificationReadiness" in source, f"{name} 必须持有就绪状态"


def test_components_no_longer_infer_email_availability_from_personal_smtp():
    for name, source in (("TaskCenter", TASK_CENTER), ("MetadataCronInspectionModal", INSPECTION_MODAL)):
        assert LEGACY_EMAIL_GUARD not in source, (
            f"{name} 不得再用「必须填 smtp_host+smtp_user」判断邮件渠道 —— "
            "选全局邮件服务的用户个人配置里本就没有这两个字段"
        )


def test_fallback_judgement_respects_smtp_source():
    """就绪接口不可用时的兜底判定必须认 smtp_source，且未知态不锁死渠道。"""
    for name, source in (("TaskCenter", TASK_CENTER), ("MetadataCronInspectionModal", INSPECTION_MODAL)):
        assert "smtp_source" in source, f"{name} 的兜底判定必须解析 smtp_source"
        assert "source === 'global'" in source, f"{name} 必须单独处理全局来源"


def test_blocked_hint_shows_backend_reason():
    """置灰提示要展示后端给出的具体原因，而不是笼统的「未配置」。"""
    for name, source in (("TaskCenter", TASK_CENTER), ("MetadataCronInspectionModal", INSPECTION_MODAL)):
        assert "unavailableExternalChannelsHint" in source, f"{name} 必须展示带原因的提示"
        assert "?.reason" in source, f"{name} 的提示必须取自就绪接口的 reason"


def test_readiness_endpoint_delegates_to_service():
    assert '@router.get("/readiness"' in ENDPOINT
    assert "NotificationService.get_channel_readiness" in ENDPOINT


def test_send_email_and_readiness_share_one_verdict():
    """发信与就绪判定必须走同一个函数：两边各写一份就会再次分叉。"""
    assert "async def resolve_email_send_plan" in SERVICE
    assert SERVICE.count("resolve_email_send_plan(") >= 3, (
        "resolve_email_send_plan 应被 send_email 与就绪判定共同复用"
    )


def test_readiness_reports_per_channel_reason():
    assert "load_raw_channel_config" in SERVICE, "就绪判定必须读原始配置，不合并默认值"
    assert "平台尚未启用全局邮件服务" in SERVICE, "全局来源不可用时必须指出是平台侧问题"
