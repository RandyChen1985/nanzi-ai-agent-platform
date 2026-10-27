"""资源访问申请的模型契约：资源类型列与数据集审计表的形状。

这两处在迁移 SQL（V192 / V92）里定义了，模型必须逐字对上，
否则 ORM 写入与迁移建出来的表会静默错位。
"""

import pytest

from app.models.knowledge import KnowledgeAccessRequest
from app.models.metadata import MetadataAuditLog

pytestmark = pytest.mark.no_infrastructure


def test_access_request_has_resource_type_defaulting_to_knowledge():
    column = KnowledgeAccessRequest.__table__.c.resource_type
    assert column.nullable is False
    # 存量行都是知识库，默认值让旧写入路径不改也能继续工作
    assert column.default.arg == "dataset"
    assert column.server_default.arg == "dataset"


def test_access_request_has_type_status_index():
    index_names = {index.name for index in KnowledgeAccessRequest.__table__.indexes}
    assert "idx_kbar_type_status" in index_names


def test_metadata_audit_log_matches_migration():
    table = MetadataAuditLog.__table__
    assert MetadataAuditLog.__tablename__ == "ai_agent_metadata_audit_logs"
    for name in (
        "metadata_dataset_id",
        "action",
        "user_id",
        "user_name",
        "status_code",
        "detail",
        "client_ip",
        "error_message",
        "created_at",
    ):
        assert name in table.c, f"缺少列 {name}"

    assert table.c.metadata_dataset_id.nullable is False
    assert table.c.action.nullable is False

    index_names = {index.name for index in table.indexes}
    for name in ("idx_mal_dataset_created", "idx_mal_action_created", "idx_mal_user"):
        assert name in index_names, f"缺少索引 {name}"
