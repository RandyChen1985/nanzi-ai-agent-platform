"""禁用工具目录：内置候选与运行时别名必须同源、同口径。"""

import pytest

from app.services.ai.tools import forbidden_catalog
from app.services.ai.tools.registry import AGENTSCOPE_BUILTIN_TOOL_ALIASES


pytestmark = pytest.mark.no_infrastructure

EXPECTED_BUILTIN_IDS = (
    "exec_command",
    "write_file",
    "edit_file",
    "read_file",
    "search_text",
    "glob_files",
    "manage_process",
    "list_process",
    "search_qa_examples",
    # 能跨目录读取图片正文（含其他租户工作区）的工具，必须可被禁用
    "read_image",
)


def test_builtin_catalog_covers_all_high_risk_execution_tools():
    ids = tuple(candidate.id for candidate in forbidden_catalog.BUILTIN_FORBIDDEN_TOOL_CANDIDATES)

    assert ids == EXPECTED_BUILTIN_IDS


def test_builtin_candidate_aliases_always_contain_its_own_id():
    for candidate in forbidden_catalog.BUILTIN_FORBIDDEN_TOOL_CANDIDATES:
        assert candidate.id in candidate.aliases


def test_builtin_candidate_aliases_match_runtime_alias_table():
    """目录别名必须与运行时别名表完全一致，避免出现禁不掉的缺口。"""
    for candidate in forbidden_catalog.BUILTIN_FORBIDDEN_TOOL_CANDIDATES:
        alias_target = AGENTSCOPE_BUILTIN_TOOL_ALIASES.get(candidate.id)
        expected = {
            alias
            for alias, target in AGENTSCOPE_BUILTIN_TOOL_ALIASES.items()
            if target == alias_target
        }
        if not expected:
            # 不在别名表中的平台工具：只允许包含自身
            assert candidate.aliases == (candidate.id,)
            continue
        expected.add(candidate.id)
        assert set(candidate.aliases) == expected


def test_builtin_catalog_categories_are_high_risk_execution_scoped():
    categories = {candidate.category for candidate in forbidden_catalog.BUILTIN_FORBIDDEN_TOOL_CANDIDATES}

    assert categories == {"sandbox", "file", "process", "knowledge"}


def test_merge_keeps_builtin_first_and_drops_duplicate_dynamic_ids():
    builtin = forbidden_catalog.BUILTIN_FORBIDDEN_TOOL_CANDIDATES[:1]
    dynamic = [
        forbidden_catalog.ForbiddenToolCandidate(
            id="exec_command",
            label="重复项",
            description="",
            category="api",
            aliases=("exec_command",),
            group_key="api:__ungrouped__",
            group="自定义 API · 未分组",
        ),
        forbidden_catalog.ForbiddenToolCandidate(
            id="crm:query",
            label="CRM 查询",
            description="",
            category="mcp",
            aliases=("crm:query",),
            group_key="mcp:CRM",
            group="MCP · CRM",
        ),
    ]

    merged = forbidden_catalog.merge_forbidden_tool_candidates(builtin, dynamic)

    assert [candidate.id for candidate in merged] == ["exec_command", "crm:query"]
    assert merged[0].label == builtin[0].label
