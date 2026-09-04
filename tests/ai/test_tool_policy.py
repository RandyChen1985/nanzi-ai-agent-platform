import pytest
from types import SimpleNamespace

from app.services.ai.grounding.models import EvidenceType
from app.services.ai.tool_policy import ToolMetadata, resolve_tool_metadata


pytestmark = pytest.mark.no_infrastructure


def test_known_tool_metadata_describes_capability_without_granting_permission():
    metadata = resolve_tool_metadata(SimpleNamespace(name="execute_sql_query"))

    assert metadata.capability == "data_query"
    assert metadata.source == "internal_structured_data"
    assert metadata.confirmation == "policy"
    assert metadata.to_dict()["nudge_mode"] == "evidence"


def test_unknown_tool_metadata_is_neutral():
    metadata = resolve_tool_metadata(SimpleNamespace(name="new_tool"))

    assert metadata == ToolMetadata()
    assert metadata.to_dict()["capability"] == "unknown"


def test_read_only_mcp_evidence_tool_is_marked_for_current_turn_nudge():
    metadata = resolve_tool_metadata(
        SimpleNamespace(
            name="mcp_get_tickets",
            source_type="mcp",
            permission_scope="read",
            evidence_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
        )
    )

    assert metadata.capability == "external_tool"
    assert metadata.source == "mcp"
    assert metadata.freshness == "dynamic"
    assert metadata.nudge_mode == "evidence"


def test_runtime_write_scope_overrides_known_read_only_metadata():
    metadata = resolve_tool_metadata(
        SimpleNamespace(
            name="execute_sql_query",
            source_type="generic_api",
            permission_scope="ask",
            evidence_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
        )
    )

    assert metadata.nudge_mode != "evidence"
    assert metadata.side_effect != "read"
