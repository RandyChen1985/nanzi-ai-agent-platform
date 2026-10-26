"""全平台审计入口契约。

逐库的操作日志只回答"这个库发生了什么"；管理员需要的是
"某个人今天动了哪些库"，还得能导出给合规。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_admin_only_entry(page: str) -> None:
    assert "openGlobalAudit" in page
    block = page.split('aria-label="全平台审计"', 1)[0][-400:]
    assert 'v-if="isAdmin"' in block


def test_queries_global_endpoint_with_filters(page: str) -> None:
    assert page.count("'/api/portal/ragflow/audit-logs'") >= 2
    assert "globalAuditKeyword" in page
    assert "globalAuditStart" in page


def test_csv_export_requests_blob(page: str) -> None:
    block = page.split("const exportGlobalAudit", 1)[1].split("\n}", 1)[0]
    assert "export: 'csv'" in block
    assert "responseType: 'blob'" in block


def test_new_actions_have_labels(page: str) -> None:
    # 新增的写操作都要有中文名，否则审计表里出现裸 chunks:update
    for action in ("documents:replace", "documents:update", "chunks:create", "chunks:delete"):
        assert f"'{action}':" in page
