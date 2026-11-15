"""管理员站内信群发的前端契约。

群发是「一次写上千行、发出去收不回」的广播动作，交互上的两条护栏必须钉死：

1. **只能先预览再发送**：发送请求必须带 ``confirm_total``，且按钮可用性依赖
   预览结果。少了这条，管理员看着「300 人」点发送，后端却按当时的真实人数发；
2. **任何范围/正文改动都要作废预览**：否则预览是旧的、发送按新范围算，
   前端展示的人数与实际收件人对不上。

另外钉住入口可见性（仅 admin）与铃铛里的「公告」标识，避免它们被顺手改掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def panel() -> str:
    return _read("frontend/src/components/system/SystemBroadcastPanel.vue")


@pytest.fixture(scope="module")
def system_config() -> str:
    return _read("frontend/src/views/SystemConfig.vue")


def test_entry_visible_to_admin_only(system_config: str) -> None:
    assert (
        "import SystemBroadcastPanel from '../components/system/SystemBroadcastPanel.vue'"
        in system_config
    )
    # 入口与面板都用 isAdmin 兜住；后端两个端点同样 require_admin
    assert 'v-if="isAdmin && diagSection === \'broadcast\'"' in system_config
    assert 'v-if="isAdmin"' in system_config
    assert "@click=\"diagSection = 'broadcast'\"" in system_config


def test_send_carries_previewed_total(panel: str) -> None:
    assert "/api/portal/broadcast/preview" in panel
    assert "/api/portal/broadcast/send" in panel
    # 发送请求必须回传预览时的人数，后端据此拒绝「预览后人数变化」
    assert "confirm_total: preview.value.total" in panel
    # 没有有效预览就不允许发送
    assert "preview.value.total > 0" in panel


def test_scope_change_invalidates_preview(panel: str) -> None:
    assert "watch([userIds, deptIds, roleIds, title, content, level]" in panel
    # 后端返回 409（人数变了）时也要作废预览，否则会拿旧人数重试
    assert "status === 409" in panel
    assert "resetPreview()" in panel


def test_audience_submits_scope_not_resolved_user_list(panel: str) -> None:
    """前端只提交范围，最终名单由服务端解析。"""
    assert "user_ids: userIds.value" in panel
    assert "dept_ids: deptIds.value" in panel
    assert "role_ids: roleIds.value" in panel


def test_bell_labels_broadcast_as_announcement() -> None:
    bell = _read("frontend/src/components/PortalNotificationBell.vue")
    assert 'item?.category === "system_broadcast"' in bell
    assert 'return "公告"' in bell
    assert "admin_broadcast" in bell


def test_audience_block_is_collapsible(panel: str) -> None:
    """收件人区域可折叠：折叠后只留一行范围摘要，正文与历史列表才放得下。"""
    assert "audienceCollapsed" in panel
    assert 'v-show="!audienceCollapsed"' in panel
    assert 'audienceCollapsed = !audienceCollapsed' in panel


def test_history_list_and_resend_fill_back_the_form(panel: str) -> None:
    """「一键重发」= 回填表单 + 自动预览，而不是直接调用发送接口。"""
    assert "/api/portal/broadcast/records" in panel
    assert "resendRecord" in panel
    # 回填的是历史输入（标题/正文/级别/三个范围维度）
    for field in ("detail.title", "detail.content", "detail.level", "detail.audience"):
        assert field in panel
    # 回填之后必须重新预览：人员可能已变化，不能照旧人数直接发
    assert "await runPreview()" in panel
    # 并把「历史人数 vs 现在人数」的差异显示出来
    assert "resendBaseline" in panel
    assert "历史命中" in panel


def test_resend_suppresses_preview_reset_during_fill(panel: str) -> None:
    """回填会触发 watch；若不抑制，刚跑出来的预览会被立刻清空。"""
    assert "suppressPreviewReset" in panel
    assert "if (!suppressPreviewReset) resetPreview();" in panel
