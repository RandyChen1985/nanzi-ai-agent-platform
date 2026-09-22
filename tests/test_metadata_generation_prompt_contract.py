"""元数据解析提示词契约。

背景：`metadata-specialist` 智能体在两个库的种子里都被禁用且再无迁移启用，因此
`MetadataGeneratorService.generate_from_ddl()` 会稳定落到代码常量
`DEFAULT_METADATA_SYSTEM_PROMPT`。它**不是**"以防万一"的占位符，而是线上真正生效的
提示词——一旦它变薄（历史上只有 3 行），`enums` / `synonyms` / `metrics` /
`relationships` / 多表 这些能力就会静默退化，且不会有任何报错。

本文件锁定：能力覆盖、与 JSON Schema 的一致性、占位符契约、兜底分支的接线，
以及两份架构文档与现实不脱节。
"""

from pathlib import Path

import pytest

from app.services.metadata_generator import (
    DEFAULT_METADATA_SYSTEM_PROMPT,
    ImportResult,
    MetadataGeneratorService,
)

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "app" / "services" / "metadata_generator.py"
DOC_META = ROOT / "architech" / "prompts" / "meta" / "metadata_generator.md"
DOC_AGENT = (
    ROOT / "architech" / "prompts" / "system_agents" / "metadata" / "metadata_specialist.md"
)

# 提示词必须显式要求、且 ImportResult 必须真的能承载的能力。
# 键为提示词中的字段名，值为用于断言"这条要求确实被写进了提示词"的中文线索。
REQUIRED_CAPABILITIES = {
    "term": "业务术语",
    "description": "业务含义描述",
    "physical_name": "物理名",
    "enums": "枚举值",
    "synonyms": "同义词",
    "metrics": "指标",
    "relationships": "关联",
    "partition_fields": "分区字段",
    "index_fields": "索引字段",
}


def _schema_text() -> str:
    import json

    return json.dumps(ImportResult.model_json_schema(), ensure_ascii=False)


def _effective_prompt_block() -> str:
    """取出 metadata_generator.md 中"实际生效"那一节的提示词代码块。"""
    text = DOC_META.read_text(encoding="utf-8")
    section = text.index("## 实际生效的系统提示词")
    fence_open = text.index("```text", section)
    body_start = text.index("\n", fence_open) + 1
    body_end = text.index("```", body_start)
    return text[body_start:body_end]


# --- 能力覆盖 ---


@pytest.mark.parametrize("field,hint", sorted(REQUIRED_CAPABILITIES.items()))
def test_prompt_states_each_capability_requirement(field: str, hint: str) -> None:
    assert field in DEFAULT_METADATA_SYSTEM_PROMPT, f"提示词缺少对 {field} 的要求"
    assert hint in DEFAULT_METADATA_SYSTEM_PROMPT, f"提示词未说明 {field} 的业务含义"


def test_prompt_requires_returning_all_tables() -> None:
    """多表是历史兜底模板最先丢的能力之一。"""
    assert "多个 CREATE TABLE" in DEFAULT_METADATA_SYSTEM_PROMPT
    assert "返回所有表" in DEFAULT_METADATA_SYSTEM_PROMPT


@pytest.mark.parametrize("field", sorted(REQUIRED_CAPABILITIES))
def test_prompt_never_asks_for_a_field_the_schema_cannot_carry(field: str) -> None:
    """提示词要求的字段必须真实存在于 ImportResult 的 Schema 中，否则等于死要求。"""
    assert field in _schema_text(), f"提示词要求 {field}，但 ImportResult Schema 里没有它"


def test_schema_carries_the_query_optimization_fields_with_guidance() -> None:
    """PR #197 新增的两个字段必须连同提取引导一起进入 Schema。"""
    schema = _schema_text()
    assert "必须从 DDL 的 PARTITION 定义提取" in schema
    assert "UNIQUE/KEY/INDEX" in schema


# --- 占位符契约 ---


def test_prompt_has_exactly_one_format_instructions_placeholder() -> None:
    assert DEFAULT_METADATA_SYSTEM_PROMPT.count("{format_instructions}") == 1


def test_rendered_prompt_substitutes_the_placeholder() -> None:
    rendered = DEFAULT_METADATA_SYSTEM_PROMPT.replace(
        "{format_instructions}",
        MetadataGeneratorService._format_instructions(ImportResult),
    )
    assert "{format_instructions}" not in rendered
    # 注入后的 Schema 必须带上新字段，确保模型真的能看到
    assert "partition_fields" in rendered
    assert "index_fields" in rendered


# --- 接线 ---


def test_fallback_branch_uses_the_named_constant() -> None:
    """防止有人再把一段临时薄模板内联回兜底分支。"""
    source = GENERATOR.read_text(encoding="utf-8")
    assert "system_prompt_template = DEFAULT_METADATA_SYSTEM_PROMPT" in source

    marker = "# 4. Resolve System Prompt"
    branch_start = source.index(marker)
    branch_end = source.index("else:", branch_start)
    branch = source[branch_start:branch_end]
    # 兜底分支内不得再出现内联的字符串拼接模板
    assert 'system_prompt_template = (' not in branch


# --- 文档同步 ---


@pytest.mark.parametrize("doc", [DOC_META, DOC_AGENT], ids=["meta", "system_agents"])
def test_docs_cover_the_query_optimization_fields(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    assert "partition_fields" in text
    assert "index_fields" in text


@pytest.mark.parametrize("doc", [DOC_META, DOC_AGENT], ids=["meta", "system_agents"])
def test_docs_record_that_the_agent_is_disabled(doc: Path) -> None:
    """文档必须说明 DB 提示词不会生效，否则会误导后来者去改一份死文案。"""
    text = doc.read_text(encoding="utf-8")
    assert "is_enabled" in text
    assert "禁用" in text


def test_docs_reproduce_the_effective_prompt_without_drift() -> None:
    """metadata_generator.md 里"实际生效"的提示词必须覆盖代码常量的每一行要求。"""
    block = _effective_prompt_block()
    missing = [
        line.strip()
        for line in DEFAULT_METADATA_SYSTEM_PROMPT.splitlines()
        if line.strip() and line.strip() not in block
    ]
    assert missing == [], f"文档中的生效提示词落后于代码常量，缺失：{missing}"
