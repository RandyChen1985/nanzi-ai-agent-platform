from pathlib import Path
import pytest
from app.models.mcp import McpOutboundAuditLog

pytestmark = pytest.mark.no_infrastructure


def test_mysql_mcp_outbound_audit_logs_migration_contract():
    sql_path = Path("db-prod/V145-add_mcp_outbound_audit_logs.sql")
    assert sql_path.exists(), "MySQL V145 迁移脚本必须存在"
    content = sql_path.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS sys_mcp_outbound_audit_logs" in content
    assert "server_id VARCHAR(36) NOT NULL" in content
    assert "tool_name VARCHAR(255) NOT NULL" in content
    assert "tool_input JSON" in content
    assert "tool_output JSON" in content
    assert "latency_ms INT" in content
    assert "status VARCHAR(32) NOT NULL" in content
    assert "idx_mcp_outbound_server" in content
    assert "idx_mcp_outbound_trace" in content


def test_postgresql_mcp_outbound_audit_logs_migration_contract():
    sql_path = Path("db-prod-pg/V46-add_mcp_outbound_audit_logs.sql")
    assert sql_path.exists(), "PostgreSQL V46 迁移脚本必须存在"
    content = sql_path.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS sys_mcp_outbound_audit_logs" in content
    assert "server_id VARCHAR(36) NOT NULL" in content
    assert "tool_input JSONB" in content
    assert "tool_output JSONB" in content
    assert "latency_ms INTEGER" in content
    assert "status VARCHAR(32) NOT NULL" in content
    assert "idx_mcp_outbound_server" in content


def test_orm_mcp_outbound_audit_log_model_contract():
    assert McpOutboundAuditLog.__tablename__ == "sys_mcp_outbound_audit_logs"
    col_names = {c.name for c in McpOutboundAuditLog.__table__.columns}
    required_cols = {
        "id",
        "server_id",
        "server_name",
        "tool_name",
        "agent_id",
        "agent_name",
        "user_id",
        "user_name",
        "trace_id",
        "status",
        "latency_ms",
        "error_message",
        "tool_input",
        "tool_output",
        "created_at",
    }
    assert required_cols.issubset(col_names)


def test_frontend_mcp_server_log_modal_contract():
    modal_path = Path("frontend/src/components/system/McpServerLogModal.vue")
    registry_path = Path("frontend/src/components/system/McpServerRegistry.vue")

    assert modal_path.exists(), "McpServerLogModal.vue 必须存在"
    modal_content = modal_path.read_text(encoding="utf-8")
    assert "/api/portal/mcp/servers/" in modal_content
    assert "outbound-logs" in modal_content
    assert "metrics" in modal_content
    assert "formatJson" in modal_content

    registry_content = registry_path.read_text(encoding="utf-8")
    assert "McpServerLogModal" in registry_content
    assert "showLogModal" in registry_content
    assert "openLogModal" in registry_content


def test_global_mcp_outbound_logs_api_contract():
    from app.api.portal.endpoints.mcp import router

    route_paths = [r.path for r in router.routes]
    assert "/outbound-logs" in route_paths
    assert "/servers/{server_id}/outbound-logs" in route_paths


def test_global_mcp_audit_log_tab_frontend_contract():
    tab_comp_path = Path("frontend/src/components/mcp/McpAuditLogTab.vue")
    view_path = Path("frontend/src/views/McpManagement.vue")

    assert tab_comp_path.exists(), "McpAuditLogTab.vue 必须存在"
    tab_content = tab_comp_path.read_text(encoding="utf-8")
    assert "/api/portal/mcp/outbound-logs" in tab_content
    assert "serverOptions" in tab_content
    assert "timeRangeFilter" in tab_content
    assert "formatJson" in tab_content

    view_content = view_path.read_text(encoding="utf-8")
    assert "McpAuditLogTab" in view_content
    assert "tab-audit-logs" in view_content
    assert "activeScope === 'audit'" in view_content
    assert 'v-if="isAdmin"' in view_content

