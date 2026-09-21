"""Contract: 项目会话门户横幅与技能 scope 增删一致性。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend" / "src" / "views" / "EmbedChat.vue"


def test_portal_banners_use_matching_resource_scope_flags():
    source = EMBED.read_text(encoding="utf-8")
    knowledge_block = source.split("<KnowledgePortalDrawer", 1)[1].split("/>", 1)[0]
    dataset_block = source.split("<DatasetPortalDrawer", 1)[1].split("/>", 1)[0]
    assert "projectSessionHasKnowledgeScope" in knowledge_block
    assert "已挂载的知识库" in knowledge_block
    assert "projectSessionHasDatasetScope" not in knowledge_block
    assert "projectSessionHasDatasetScope" in dataset_block
    assert "已挂载的数据集" in dataset_block
    assert "projectSessionHasKnowledgeScope" not in dataset_block


def test_skill_scope_is_used_for_keys_and_removal():
    source = EMBED.read_text(encoding="utf-8")
    assert "resourceScopeEntryKey" in source
    assert "resourceScopeEntriesMatch" in source
    # 入口 key 改为按分组类型统一生成，且在 scope 存在时把 scope 拼进 key，
    # 保证同名个人技能/公共技能不会互相覆盖。
    assert "resourceScopeEntryKey(type, item, index)" in source
    assert "scope ? `${type}:${scope}:${id}`" in source
    assert "resourceScopeEntriesMatch(entry, item)" in source
    assert "...(option.scope ? { scope: option.scope } : {})" in source
    assert "scope }" in source or "scope: option.scope" in source
