"""Contract: @ 浮层的文件 tab（工作区文件选择器）。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PICKER = ROOT / "frontend" / "src" / "components" / "embed" / "WorkspaceFilePicker.vue"
MENTION_LIST = ROOT / "frontend" / "src" / "components" / "agent" / "MentionList.vue"
CHAT_INPUT = ROOT / "frontend" / "src" / "components" / "embed" / "ChatInput.vue"
EMBED_CHAT = ROOT / "frontend" / "src" / "views" / "EmbedChat.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_picker_hits_three_fs_endpoints_with_bounded_search():
    text = _read(PICKER)
    assert "/api/v1/chat/fs/list" in text
    assert "/api/v1/chat/fs/search" in text
    assert "/api/v1/chat/fs/recent-files" in text
    assert "max_results" in text, "搜索结果必须有上限，避免一次灌进几百行"
    assert "params" in text


def test_picker_debounces_search_and_drops_stale_responses():
    text = _read(PICKER)
    assert "setTimeout" in text and "clearTimeout" in text, "搜索必须防抖"
    assert "searchSeq" in text, "用自增序号丢弃过期响应"
    assert "seq !== searchSeq" in text


def test_picker_exposes_keyboard_handler():
    text = _read(PICKER)
    assert "handleKeydown" in text
    assert "defineExpose({ handleKeydown" in text
    for key in ("ArrowUp", "ArrowDown", "Enter", "Escape"):
        assert key in text


def test_picker_marks_mounted_rows_and_blocks_click():
    text = _read(PICKER)
    assert "mountedPaths" in text
    assert "已挂载" in text
    assert "isMounted" in text


def test_mention_list_switches_between_expert_and_file_tabs():
    text = _read(MENTION_LIST)
    assert "activeTab" in text, "tab 状态必须由 MentionList 持有"
    assert '"expert" | "file"' in text
    assert "activeTab === 'expert'" in text
    assert "initialTab" in text
    assert "WorkspaceFilePicker" in text
    assert "select-file" in text
    assert "open-workspace" in text


def test_mention_list_resets_tab_when_reopened():
    text = _read(MENTION_LIST)
    assert "watch(" in text
    assert "activeTab.value = props.initialTab" in text, "每次 @ 重新打开都应回到 initialTab，不能记住上次停留"


def test_mention_list_routes_keyboard_to_active_tab():
    text = _read(MENTION_LIST)
    assert 'activeTab.value === "file"' in text
    assert "pickerRef" in text


def test_mention_list_shows_locked_hint_instead_of_experts():
    text = _read(MENTION_LIST)
    assert "expertLocked" in text
    assert "已锁定指定智能体" in text


def test_chat_input_mention_detection_guards_ime_and_drops_expert_gate():
    text = _read(CHAT_INPUT)
    assert "!isComposing.value" in text, "@ 检测必须避开输入法组合态"
    assert "mentionInitialTab" in text
    # 旧门槛：@ 只在「未锁定专家且有可切换专家」时才响应，现在文件 tab 不受此限
    assert "atMatch && !props.lockExpertAgent" not in text
    assert "isStartOfWord && props.allowedAgents.length > 0" not in text


def test_chat_input_exposes_push_local_file():
    text = _read(CHAT_INPUT)
    assert "pushLocalFile" in text
    assert "local_file" in text
    assert "mountedFilePaths" in text


def test_chat_input_returns_focus_after_closing_mention_list():
    text = _read(CHAT_INPUT)
    assert "handleMentionClose" in text
    assert "inputRef.value?.focus()" in text


def test_embed_chat_reuses_push_local_file_for_drawer_selection():
    text = _read(EMBED_CHAT)
    start = text.index("const handleSelectLocalFs")
    body = text[start:start + 320]
    assert "pushLocalFile" in body, "抽屉选中必须走同一个入列方法，避免两份去重逻辑"
    assert "uploadedFiles.push" not in body


def test_picker_switches_tab_from_empty_search_with_arrow_keys():
    text = _read(PICKER)
    assert "switch-tab" in text, "搜索框要把左右键上抛给浮层切 tab"
    assert "canLeaveByArrow" in text, "搜索框有内容时左右键必须留给光标"
    assert "defineExpose({ handleKeydown, canLeaveByArrow, focusSearch })" in text
    assert "metaKey" in text or "ctrlKey" in text, "有内容时用修饰键强制切走"


def test_mention_list_arrows_switch_tabs():
    text = _read(MENTION_LIST)
    assert "ArrowLeft" in text and "ArrowRight" in text
    assert "onPickerSwitchTab" in text
    assert "tab-change" in text


def test_chat_input_refocuses_textarea_when_tab_returns_to_expert():
    text = _read(CHAT_INPUT)
    assert '@tab-change="handleMentionTabChange"' in text
    assert "handleMentionTabChange" in text


def test_input_placeholder_mentions_files_for_at_trigger():
    text = _read(CHAT_INPUT)
    assert "@ 选择专家或文件" in text, "占位文案要说明 @ 也能选文件"
    assert "@ 选择智能体专家" not in text, "旧文案只说切专家，已过期"
    assert "@ 选择文件" in text, "锁定专家时 @ 仍能选文件，文案不能漏"


def test_mention_list_header_title_follows_active_tab():
    text = _read(MENTION_LIST)
    assert "panelTitle" in text, "标题应由当前 tab 派生，不能写死"
    assert "选择文件" in text, "文件 tab 的标题不能仍显示「选择专家」"
    assert "activeTab.value === 'file' ? '选择文件' : '选择专家'" in text
    assert 'aria-label="选择专家"' not in text, "aria-label 也要跟随 tab，不能写死"
