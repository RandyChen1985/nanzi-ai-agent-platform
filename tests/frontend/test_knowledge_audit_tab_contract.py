"""知识库详情页的「操作日志」tab。

诉求很直接：谁创建了、改了什么、传了什么、删了什么，之前一概查不到 ——
后端其实一直在写审计，但沉在通用访问日志里，知识库负责人根本没有入口。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_detail_page_has_audit_tab(page):
    assert "detailTab" in page
    assert "操作日志" in page
    # 切换的是「文档与设置 / 操作日志」两个视图
    assert "'documents', label: '文档与设置'" in page
    assert "'audit', label: '操作日志'" in page


def test_audit_log_is_rendered_as_readable_sentence(page):
    """原始 JSON 给人看没有意义，必须翻成人话。"""
    assert "const auditLogText" in page
    assert "上传了" in page and "删除了" in page and "转移了知识库归属人" in page


def test_sensitive_actions_are_management_only(page):
    """授权变更、转移归属、删库这类不能让被授权的外部成员看到。"""
    assert "ADMIN_ONLY_AUDIT_ACTIONS" in page
    block = page.split("const ADMIN_ONLY_AUDIT_ACTIONS = [", 1)[1].split("]", 1)[0]
    for action in ("datasets:grant", "datasets:revoke", "datasets:transfer_owner", "datasets:delete"):
        assert action in block


def test_audit_tab_refreshes_every_time_it_is_opened(page):
    """每次切到日志 tab 都要重新拉。

    只在"列表为空"时加载会让「在文档 tab 传完文件、切回日志看看记没记上」
    看到一个旧列表 —— 而这恰恰是用户切回来的唯一目的。
    """
    block = page.split("watch(detailTab", 1)[1].split("})", 1)[0]
    assert "if (tab === 'audit')" in block
    assert "!auditLogs.value.length" not in block


def test_refresh_keeps_previous_rows_on_failure(page):
    """刷新失败时保留旧数据：把用户正在看的内容清空比不刷新更糟。"""
    block = page.split("const loadAuditLogs = async", 1)[1].split("const auditLogText", 1)[0]
    assert "不清空旧列表" in block
    catch_block = block.split("} catch (err) {", 1)[1].split("} finally", 1)[0]
    assert "auditLogs.value = []" not in catch_block


def test_audit_logs_clear_when_switching_dataset(page):
    # 换库必须清空，否则会短暂显示上一个库的记录
    assert "auditLogs.value = []" in page


def test_backend_endpoint_and_table_align(page):
    backend = (ROOT / "app/api/portal/endpoints/ragflow.py").read_text(encoding="utf-8")
    assert '@router.get("/datasets/{dataset_id}/audit-logs")' in backend
    assert "KNOWLEDGE_CONTENT_AUDIT_ACTIONS" in backend

    model = (ROOT / "app/models/knowledge.py").read_text(encoding="utf-8")
    assert "ai_agent_knowledge_audit_logs" in model
