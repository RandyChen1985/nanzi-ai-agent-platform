from __future__ import annotations

import pytest

from app.services.ai.grounding.models import EvidenceType
from app.services.ai.tools.registry import (
    TOOL_EVIDENCE_POLICY,
    TOOL_EVIDENCE_TYPES,
    resolve_tool_evidence_types,
)
from app.services.ai.tools.resource_catalog_tools import (
    list_accessible_datasets,
    list_accessible_directories,
    list_accessible_knowledge_bases,
    list_available_agents,
)
from app.services.ai.tools.system_executive_tools import (
    list_available_skills,
    read_skill_instruction,
)

pytestmark = pytest.mark.no_infrastructure


def test_resource_catalog_tools_registered_in_tool_evidence_types():
    """验证资源目录与系统隐式工具已正确登记至 Grounding 证据映射表。"""
    assert "list_accessible_datasets" in TOOL_EVIDENCE_TYPES
    assert TOOL_EVIDENCE_TYPES["list_accessible_datasets"] == frozenset({EvidenceType.INTERNAL_DATA})

    assert "list_accessible_knowledge_bases" in TOOL_EVIDENCE_TYPES
    assert TOOL_EVIDENCE_TYPES["list_accessible_knowledge_bases"] == frozenset({EvidenceType.INTERNAL_KNOWLEDGE})

    assert "list_available_agents" in TOOL_EVIDENCE_TYPES
    assert TOOL_EVIDENCE_TYPES["list_available_agents"] == frozenset({EvidenceType.RUNTIME_STATE})

    assert "list_accessible_directories" in TOOL_EVIDENCE_TYPES
    assert TOOL_EVIDENCE_TYPES["list_accessible_directories"] == frozenset({EvidenceType.RUNTIME_STATE})

    assert "list_available_skills" in TOOL_EVIDENCE_TYPES
    assert TOOL_EVIDENCE_TYPES["list_available_skills"] == frozenset({EvidenceType.RUNTIME_STATE})

    assert "read_skill_instruction" in TOOL_EVIDENCE_TYPES
    assert TOOL_EVIDENCE_TYPES["read_skill_instruction"] == frozenset({EvidenceType.RUNTIME_STATE})


def test_resource_catalog_tools_have_allow_empty_success_policy():
    """目录查询若无可用条目返回空列表属于合法事实，必须允许空成功入账。"""
    for tool_name in (
        "list_accessible_datasets",
        "list_accessible_knowledge_bases",
        "list_available_agents",
        "list_accessible_directories",
        "list_available_skills",
        "read_skill_instruction",
    ):
        assert TOOL_EVIDENCE_POLICY.get(tool_name) == "allow_empty_success", f"{tool_name} must have allow_empty_success"


def test_tool_objects_have_explicit_evidence_attributes():
    """验证工具函数对象本身挂载了双重保障的 evidence 元数据。"""
    assert getattr(list_accessible_datasets, "evidence_types", None) == frozenset({EvidenceType.INTERNAL_DATA})
    assert getattr(list_accessible_datasets, "evidence_policy", None) == "allow_empty_success"

    assert getattr(list_accessible_knowledge_bases, "evidence_types", None) == frozenset({EvidenceType.INTERNAL_KNOWLEDGE})
    assert getattr(list_accessible_knowledge_bases, "evidence_policy", None) == "allow_empty_success"

    assert getattr(list_available_agents, "evidence_types", None) == frozenset({EvidenceType.RUNTIME_STATE})
    assert getattr(list_available_agents, "evidence_policy", None) == "allow_empty_success"

    assert getattr(list_accessible_directories, "evidence_types", None) == frozenset({EvidenceType.RUNTIME_STATE})
    assert getattr(list_accessible_directories, "evidence_policy", None) == "allow_empty_success"

    assert getattr(list_available_skills, "evidence_types", None) == frozenset({EvidenceType.RUNTIME_STATE})
    assert getattr(list_available_skills, "evidence_policy", None) == "allow_empty_success"

    assert getattr(read_skill_instruction, "evidence_types", None) == frozenset({EvidenceType.RUNTIME_STATE})
    assert getattr(read_skill_instruction, "evidence_policy", None) == "allow_empty_success"


def test_resolve_tool_evidence_types_resolves_catalog_tools():
    """验证 resolve_tool_evidence_types 解析函数能够正确输出证据类型集合。"""
    resolved_ds = resolve_tool_evidence_types("list_accessible_datasets")
    assert EvidenceType.INTERNAL_DATA in resolved_ds

    resolved_kb = resolve_tool_evidence_types("list_accessible_knowledge_bases")
    assert EvidenceType.INTERNAL_KNOWLEDGE in resolved_kb
