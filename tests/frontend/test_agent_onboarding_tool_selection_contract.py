"""新建智能体向导「工具选择即权威」的契约。

缺陷现场（2026-11-07，智能体 test002）：新建向导里只勾了「系统自治工具」12 个，
保存后打开却显示已选 32 个（落库 33 个，委派双件合并计 1）。核对数据库发现这 33 个
与模板智能体 main 的 PUBLISHED 版本工具**完全相同**——说明不是用户的选择生效了，
而是模板工具被整份带了回来。

根因：新建保存走 `persistNewAgentDraft`，后端先按模板建出 V1（模板工具已落库），
前端又把「模板工具 + 用户勾选」取并集回写。并集语义下模板里的工具永远删不掉，
用户在向导里取消过的选择会被静默覆盖。

契约：
  1. 用户动过工具选择（点选、组全选、MCP 组全选、清理失效引用）就只提交用户选中的那份；
  2. 完全没进工具步骤时才并入模板工具（保留「套模板」的开箱可用能力）；
  3. 因此提交路径不能再出现无条件的「模板 ∪ 用户选择」。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "frontend/src/views/AgentManagement.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return SOURCE_PATH.read_text(encoding="utf-8")


def _persist_new_agent_draft_body(source: str) -> str:
    """截出 persistNewAgentDraft 函数体，避免断言被文件里别处的同名片段命中。"""
    start = source.index("const persistNewAgentDraft = async")
    # 函数在下一个顶层 const 之前结束（该文件用 const 箭头函数，缩进为 0）
    tail = source[start + 1 :]
    candidates = [
        tail.index("\nconst handleVersionEditorClose = async"),
        tail.index("\nconst syncAgentEngineConfigFromForm = async"),
    ]
    end = start + 1 + min(candidates)
    return source[start:end]


def test_new_agent_tools_follow_user_selection_not_template_union():
    source = _source()
    body = _persist_new_agent_draft_body(source)

    # 记录「用户是否动过工具选择」的状态位必须存在
    assert "const toolSelectionTouched = ref(false)" in source
    assert "const markToolSelectionTouched = ()" in source

    # 提交以用户选择为准，未触碰时才退回模板
    assert "const submittedTools = toolSelectionTouched.value" in body
    assert "tools: submittedTools" in body

    # 无条件并集必须消失：它会让模板工具永远删不掉
    assert "mergedToolMap" not in body
    assert "tools: [...mergedToolMap.values()]" not in source


def test_tool_selection_entry_points_mark_touched():
    source = _source()

    for entry in (
        "const toggleTool = (toolName: string) => {",
        "const toggleSelectAllStatic = (label: string) => {",
        "const toggleSelectAllMcp = (serverName: string, tools: any[]) => {",
    ):
        start = source.index(entry)
        body = source[start : start + 500]
        assert "markToolSelectionTouched()" in body, f"{entry} 未标记工具选择已变动"

    # 清理失效引用（用户主动删工具）同样算动过选择
    stale_start = source.index("const removeStaleToolRefs = (names: string[]) => {")
    stale_body = source[stale_start : stale_start + 1200]
    assert "markToolSelectionTouched()" in stale_body


def test_each_new_agent_session_starts_untouched():
    source = _source()
    start = source.index("const startAgentCreation = () => {")
    body = source[start : start + 2000]
    assert "toolSelectionTouched.value = false" in body


def test_untouched_tools_still_inherit_template():
    """没进工具步骤时保留模板能力：这条兜底不能被「用户优先」顺手删掉。"""
    body = _persist_new_agent_draft_body(_source())
    assert "created.data.version.tools" in body
