"""凭据选择器的搜索契约。

背景：绑定凭据的入口有 5 处（MCP 服务 Authorization、MCP 工具参数、Header、
API 工具参数、脚本环境变量），全部复用同一个 `CredentialPicker`。凭据一多，
纯列表只能逐条翻，而用户记得的往往是标识而不是显示名，于是这里锁定：

1. 过滤逻辑走共享纯函数（行为见 `tests/frontend/credential_picker_search.mjs`）；
2. 浮层里有搜索输入，且支持 ↑↓ / Enter / Esc；
3. 浮层 Teleport 到 body —— 绑定弹窗带遮罩且宽度固定，留在弹窗内会被裁掉；
4. 高亮用分段 `<span>` 而不是 `v-html`（凭据名是用户输入，不能当 HTML 渲染）；
5. 「清除绑定」与孤儿绑定提示不能被搜索改造挤掉。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PICKER = ROOT / "frontend/src/components/secrets/CredentialPicker.vue"
UTIL = ROOT / "frontend/src/utils/credentialPicker.ts"


def _run_node(script: str) -> str:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_search_behaviour_runs_in_node():
    output = _run_node("tests/frontend/credential_picker_search.mjs")
    assert "credential_picker_search: all assertions passed" in output


class TestWiring:
    def test_component_reuses_shared_filter_functions(self):
        source = PICKER.read_text(encoding="utf-8")
        util = UTIL.read_text(encoding="utf-8")

        assert "from '../../utils/credentialPicker'" in source, "过滤逻辑必须来自共享纯函数"
        assert "filterCredentialItems(" in source
        assert "highlightCredentialField(" in source
        # 组件里不得再自行写一遍匹配规则
        assert ".includes(query" not in source
        assert "toLowerCase()" not in source

        for name in (
            "filterCredentialItems",
            "credentialMatchesScene",
            "normalizeCredentialQuery",
            "highlightCredentialField",
        ):
            assert f"export function {name}" in util, f"缺少纯函数 {name}"

    def test_search_only_matches_name_key_and_category(self):
        util = UTIL.read_text(encoding="utf-8")
        fields_start = util.index("export function credentialSearchFields")
        fields_block = util[fields_start : util.index("}", fields_start)]
        assert "item.name" in fields_block
        assert "item.secret_key" in fields_block
        assert "item.category" in fields_block
        # 值的影子不参与匹配，避免「搜什么就能验证什么」的探测面
        assert "value_masked" not in fields_block


class TestFloatingLayer:
    def test_menu_is_teleported_above_modals(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "<Teleport to=\"body\">" in source, "绑定弹窗会裁掉内部浮层，必须 Teleport 到 body"
        assert "zIndex: '10100'" in source, "浮层要压过 z-[100] 的遮罩"
        assert "position: 'fixed'" in source

    def test_long_lists_scroll_inside_a_capped_menu(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "max-h-[70vh]" in source
        assert "overflow-y-auto" in source
        # 搜索结果为空时给出搜索语境下的提示
        assert "没有匹配" in source


class TestSearchAffordances:
    def test_has_search_input_with_clear_button(self):
        source = PICKER.read_text(encoding="utf-8")

        assert 'v-model="query"' in source
        assert 'aria-label="搜索凭据"' in source
        # 清空走项目统一的 `type="search"` 原生按钮（见 test_clearable_filter_inputs.py），
        # 这里不另画一个，免得两个清空按钮叠在一起
        assert 'type="search"' in source
        # 打开即聚焦，用户可以直接开始打字
        assert "searchRef.value?.focus()" in source

    def test_keyboard_navigation_moves_selects_and_closes(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "function onSearchKeydown" in source
        for key in ("'ArrowDown'", "'ArrowUp'", "'Enter'", "'Escape'"):
            assert key in source, f"缺少键盘处理 {key}"
        assert "function moveActive" in source
        assert "function confirmActive" in source
        assert "scrollIntoView" in source, "键盘移动后要把高亮项滚进视野"

    def test_click_outside_closes_the_menu(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "function onDocumentPointerDown" in source
        assert "document.addEventListener('mousedown', onDocumentPointerDown)" in source
        assert "document.removeEventListener('mousedown', onDocumentPointerDown)" in source

    def test_group_headings_show_matched_over_total_while_searching(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "function countLabel" in source
        assert "`${matched} / ${total}`" in source
        # 两个分组共用同一段模板，只靠 label / tint 区分——重复写两遍必然漏改
        assert "{{ group.label }} · {{ countLabel(group.rows.length, group.total) }}" in source
        assert "label: '我的凭据'" in source
        assert "label: '系统共享'" in source
        assert source.count('v-for="row in group.rows"') == 1

    def test_binding_still_returns_only_the_id(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "emit('update:modelValue', item ? item.id : null)" in source
        assert "value_masked" not in source, "选择器不展示、不缓存任何值"


class TestExistingBehaviourKept:
    def test_highlight_uses_segments_not_v_html(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "v-html" not in source, "凭据名是用户输入，不能当 HTML 渲染"
        assert "nameSegments" in source
        assert "keySegments" in source

    def test_clear_binding_and_orphan_notice_survive(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "清除绑定" in source
        assert "已不在可选列表里" in source
        assert "selected || orphaned" in source

    def test_scene_filter_and_selected_mark_are_both_renderable(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "credentialMatchesScene(item, props.scene)" in source
        assert ':aria-selected="row.item.id === modelValue"' in source
        assert "M5 13l4 4L19 7" in source, "当前绑定项要有对勾，避免搜到一半忘了选的是哪条"

    def test_reload_still_exposed_for_parents(self):
        source = PICKER.read_text(encoding="utf-8")

        assert "defineExpose({ reload: load })" in source
