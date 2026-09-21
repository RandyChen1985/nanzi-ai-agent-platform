from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_data_portal_saved_reports_tab_and_create_modal_contract():
    portal_home = _source("frontend/src/views/DataPortalHome.vue")
    report_section = _source("frontend/src/components/data-portal/DataPortalReportSection.vue")
    create_modal = _source("frontend/src/components/data-portal/DataPortalReportCreateModal.vue")
    capability_menu = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")

    # 1. 验证数据门户 Tab 更名为「固化报表」且包含新建入口与指南大弹窗
    assert "固化报表" in portal_home
    assert "showCreateModal" in portal_home
    assert "showSpecsModal" in portal_home
    assert "DataPortalReportCreateModal" in portal_home
    assert "固化报表设计规范与使用指南" in portal_home

    # 2. 验证 ReportSection 包含新建按钮与固化报表标题
    assert "新建固化报表" in report_section
    assert "固化报表" in report_section

    # 3. 验证数据门户侧边栏/抽屉（DatasetCapabilityMenu）包含顶层 Tab 与新建入口
    assert "menuActiveTab" in capability_menu
    assert "showCreateReportModal" in capability_menu
    assert "新建报表" in capability_menu
    assert "DataPortalReportCreateModal" in capability_menu

    # 4. 验证新建手工开发 Modal 功能完备性
    assert "新建固化报表" in create_modal
    assert "/api/portal/saved-reports/preview-sql" in create_modal
    assert "试跑测试" in create_modal
    assert "/api/portal/saved-reports" in create_modal


def test_saved_reports_full_frontend_renaming_contract():
    embed_chat = _source("frontend/src/views/EmbedChat.vue")
    agent_debug = _source("frontend/src/views/AgentDebug.vue")
    capability_menu = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    editor_modal = _source("frontend/src/components/data-portal/DataPortalReportCreateModal.vue")
    bell = _source("frontend/src/components/PortalNotificationBell.vue")

    # 验证全部不再包含「黄金报表」且已切换为「固化报表」
    assert "黄金报表" not in embed_chat
    # EmbedChat 的消息操作入口已抽到 MessageActionMenus 子组件（见 c1d5f563），
    # 入口文案在子组件，EmbedChat 侧以 save-report 事件接线作为行为锚点。
    message_actions = _source("frontend/src/components/chat/MessageActionMenus.vue")
    assert "添加固化报表" in message_actions
    assert '@save-report="handleSaveReportFromMessage(msg)"' in embed_chat

    assert "黄金报表" not in agent_debug
    assert "添加固化报表" in agent_debug

    assert "黄金报表" not in capability_menu
    assert "固化报表" in capability_menu

    assert "黄金报表" not in editor_modal
    assert "固化报表" in editor_modal

    assert "黄金报表" not in bell
    assert "固化报表" in bell
