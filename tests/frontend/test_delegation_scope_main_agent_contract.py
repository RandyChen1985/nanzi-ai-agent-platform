"""「主助手不可作为委派目标」的前端契约。

配套后端测试见 `tests/ai/tools/test_delegation_main_agent_exclusion.py`。

要点（三处必须同一口径，缺一处就会出现「界面能选、调用报错」或反之）：
  1. 弹窗里 main 置灰、不能勾选，并显示「系统兜底 · 不可委派」；
  2. 存量名单里已存在的 main 要**可见且可清理**：显示提示、保存时剔除；
  3. 智能体列表不能在上游把 main 过滤掉，否则老配置里那条 main 既看不见也删不掉。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODAL = ROOT / "frontend/src/components/agent/DelegationScopeModal.vue"
VIEW = ROOT / "frontend/src/views/AgentManagement.vue"

pytestmark = pytest.mark.no_infrastructure


def _modal_source() -> str:
    return MODAL.read_text(encoding="utf-8")


def test_modal_recognises_main_by_id_and_slugs():
    source = _modal_source()

    # 与后端 is_main_general_agent_target 同一份口径：固定 ID + 三个兼容 slug
    assert 'const MAIN_AGENT_ID = "sys-agent-chat"' in source
    assert '"main", "assistant", "general-chat"' in source
    assert "const isMainAgent" in source


def test_main_is_not_selectable():
    source = _modal_source()

    assert "const isSelectable = (agent: DelegationScopeAgent) =>" in source
    # 可勾选 = 有授权 且 不是 main
    assert "isAuthorized(agent) && !isMainAgent(agent)" in source


def test_legacy_main_entry_is_visible_and_cleaned_on_save():
    source = _modal_source()

    # 打开时能识别出名单里的 main，并给出可操作提示
    assert "legacyMainIds" in source
    assert "保存时会自动移除" in source
    assert "dropLegacyMain" in source

    # 保存 payload 必须剔除 main，而不是原样回写
    submit = source[source.index("const submit = () => {") :]
    submit_body = submit[: submit.index("};")]
    assert "mainAgentIds.value.has(id)" in submit_body
    assert 'emit("save", cleaned)' in submit_body

    # 计数只算真正生效的条目
    assert "effectiveSelectedCount" in source


def test_main_row_explains_why_it_is_disabled():
    source = _modal_source()

    assert "系统兜底 · 不可委派" in source
    # 团主专家的「改用这 N 位成员」不该出现在 main 行上
    assert "!isMainAgent(agent)" in source


def test_view_keeps_main_in_candidate_payload_for_cleanup():
    """列表层不能提前过滤 main：弹窗靠它识别存量条目。"""
    source = VIEW.read_text(encoding="utf-8")
    start = source.index("const delegationScopeAgents = computed(")
    body = source[start : start + 900]

    assert "isMainAgent" not in body
    assert "主助手（main）不在这里过滤" in body
