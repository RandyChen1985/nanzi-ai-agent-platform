"""ChatBI 智能体绑定数据集后，用户侧要有可见提示。

绑定收窄的是「智能体可见的数据集范围」，但还会与用户自身数据权限取交集，
且 SQL 执行层并不认这层绑定，所以文案只做信息告知，不能写成「仅限 / 超出即拒绝」。
这里把三处载体（后端字段、输入框横幅、选择卡片标签）一并锁住，避免以后被改回
「不提示」或改成承诺强制的措辞。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
BANNER = ROOT / "frontend/src/components/chat/DatasetScopeBanner.vue"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
MENTION_LIST = ROOT / "frontend/src/components/agent/MentionList.vue"
CASCADE_MENU = ROOT / "frontend/src/components/embed/DataKnowledgeCascadeMenu.vue"
CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"
AGENT_ENDPOINT = ROOT / "app/api/portal/endpoints/agents.py"
AGENT_MANAGER = ROOT / "app/services/ai/agent_manager.py"
AGENT_PAGE = ROOT / "frontend/src/views/AgentManagement.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_banner_component_exists_and_promises_no_hard_scope():
    banner = _read(BANNER)
    # 有标题、有按智能体与绑定数量拼的说明
    assert "当前智能体已绑定数据集" in banner
    assert "boundCount" in banner
    assert "已绑定的 ${count} 个数据集" in banner
    # 措辞必须交代「仍受用户自身权限约束」，且渲染文案不能出现硬拦截承诺
    assert "仍取决于你自身的数据权限" in banner
    template = banner.split("</script>", 1)[1]
    assert "仅限" not in template
    assert "直接拒绝" not in template


def test_embed_mounts_banner_next_to_knowledge_scope_banner():
    embed = _read(EMBED)
    assert 'import DatasetScopeBanner from "@/components/chat/DatasetScopeBanner.vue"' in embed
    assert "showDatasetScopeBanner" in embed
    # 数据来源是 allowed 列表上的 metadata_dataset_count
    assert "metadata_dataset_count" in embed
    # 按智能体记关闭状态，换一个智能体重新提示
    assert "datasetScopeDismissedAgentId" in embed


def test_mention_list_shows_dataset_badge():
    page = _read(MENTION_LIST)
    assert "metadata_dataset_count" in page
    assert "数据集" in page


def test_allowed_endpoint_fills_bound_dataset_count():
    endpoint = _read(AGENT_ENDPOINT)
    assert "attach_bound_metadata_dataset_counts" in endpoint

    manager = _read(AGENT_MANAGER)
    assert "async def attach_bound_metadata_dataset_counts" in manager
    # 统计口径与 list_agents 共用同一个函数，避免两处算法漂移
    assert "_count_bound_metadata_datasets" in manager


def test_plus_menu_dataset_tab_locked_when_agent_binds_datasets():
    """智能体绑定了数据集时，加号菜单里手动挂数据集没有意义，必须置灰并说明原因。"""
    menu = _read(CASCADE_MENU)
    assert "datasetLocked" in menu
    # 与「不支持数据查询」分开提示，否则用户会以为智能体缺少数据能力
    assert "当前智能体已绑定数据集，查询范围由智能体决定，手动挂载不会生效。" in menu
    assert "当前智能体不支持数据查询，无法挂载数据集。" in menu
    # 锁定只作用于列表项（页签仍可切换，用户要能看到提示和范围）
    assert "props.datasetDisabled || props.datasetLocked" in menu
    assert "const datasetTabDisabled = computed(() => props.datasetDisabled)" in menu

    chat_input = _read(CHAT_INPUT)
    assert "boundDatasetCount" in chat_input
    assert ':dataset-locked="boundDatasetCount > 0"' in chat_input

    embed = _read(EMBED)
    assert ':bound-dataset-count="datasetScopeBoundCount"' in embed


def test_agent_center_card_marks_dataset_scope_as_limited():
    """智能体中心卡片要明确「仅限数据集」，不能只有一个中性的数量徽章。"""
    page = _read(AGENT_PAGE)
    # 网格卡片与列表行两处都要有
    assert page.count("仅限数据集") >= 2
    assert "仅限查询这 ${agent.metadata_dataset_count} 个数据集" in page
    # 知识库徽章保持中性灰，两类绑定在视觉上要能区分
    assert 'bg-blue-50 text-blue-700' in page
