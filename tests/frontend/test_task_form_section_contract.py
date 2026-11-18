"""定时任务表单三段折叠的契约。

锁定四件事：
1. 折叠组件是可访问的受控组件（button + aria-expanded/aria-controls + v-show）；
2. 收起后标题行给出摘要，组内有必填错误时给出红点，两者同源；
3. TaskCenter.vue 三组标题与内容都被组件包住，没有内容漏在折叠外；
4. 保存失败会自动展开第一个出错的组。

行为验证（摘要拼装与错误归组）见 tests/frontend/task_form_sections.mjs。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SECTION = "frontend/src/components/task/TaskFormSection.vue"

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _run_node(script: str) -> str:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_section_logic_behaviour_via_node():
    output = _run_node("tests/frontend/task_form_sections.mjs")
    assert "task_form_sections 行为测试通过" in output


def test_section_header_is_accessible_toggle_button():
    source = _source(SECTION)

    assert "<button" in source
    assert 'type="button"' in source
    assert ':aria-expanded="modelValue"' in source
    assert ':aria-controls="sectionId"' in source
    # 内容区必须保留 DOM（v-show 而不是 v-if），否则 aria-controls 指向空气
    assert 'v-show="modelValue"' in source
    assert ':id="sectionId"' in source


def test_section_props_cover_title_hint_summary_and_error():
    source = _source(SECTION)

    for token in ("sectionId", "title", "hint", "summary", "hasError", "modelValue"):
        assert token in source, f"组件 props 缺少 {token}"
    assert "defineEmits" in source
    assert "'update:modelValue'" in source


def test_section_shows_summary_only_when_collapsed_and_error_replaces_it():
    source = _source(SECTION)

    # 收起才显示摘要行
    assert 'v-if="!modelValue && (hasError || summary)"' in source
    # 有错时摘要位置换成「需补全」，不并存
    assert "'需补全'" in source
    assert "'font-bold text-red-600'" in source


TASK_CENTER = "frontend/src/views/TaskCenter.vue"


def test_three_sections_are_wrapped_by_the_component():
    source = _source(TASK_CENTER)

    assert "from '@/components/task/TaskFormSection.vue'" in source
    # 三组各出现一次组件标签
    assert source.count("<TaskFormSection") >= 3
    for section_id in (
        "TASK_SECTION_IDS.what",
        "TASK_SECTION_IDS.when",
        "TASK_SECTION_IDS.result",
    ):
        assert section_id in source, f"缺少 {section_id} 绑定"
    # 摘要与红点都来自纯函数模块，不是模板里现拼
    assert "buildTaskSectionSummaries(" in source
    assert "resolveTaskSectionIssues(" in source
    assert "taskSectionSummary.what" in source
    assert "taskSectionIssues.issues.what" in source


def test_sections_default_to_expanded():
    source = _source(TASK_CENTER)

    assert "const showWhatSection = ref(true)" in source
    assert "const showWhenSection = ref(true)" in source
    assert "const showResultSection = ref(true)" in source


def test_nested_retry_fold_still_independent():
    source = _source(TASK_CENTER)

    # 内层「执行失败策略」仍是自己的开关，没被组级折叠取代
    assert "const showAdvancedRetry = ref(false)" in source
    assert 'v-show="showAdvancedRetry"' in source


def test_save_expands_first_invalid_section_before_toasting():
    source = _source(TASK_CENTER)

    assert "expandSection('what')" in source
    assert "expandSection('when')" in source
    # 名称/提示词的前端校验必须早于 cron 校验，否则「做什么」组永远等后端 422
    assert source.index("expandSection('what')") < source.index("expandSection('when')")
    assert "请填写任务名称" in source
    assert "请填写「要做什么」" in source


def test_section_title_has_leading_icon_and_chevron_indicator():
    source = _source(SECTION)

    # 标题前有语义图标（由调用方传入，组件不硬编码业务含义）
    assert "icon?: Component" in source
    assert ":is=\"icon\"" in source
    # 收起/展开用 chevron 图标 + 旋转，而不是纯字符，视觉上更明确
    assert "ChevronDownIcon" in source
    assert "rotate-180" in source


def test_task_center_passes_semantic_icons_to_three_sections():
    source = _source(TASK_CENTER)

    for token in ("PencilSquareIcon", "ClockIcon", "PaperAirplaneIcon"):
        assert token in source, f"缺少图标 {token}"
    assert source.count(":icon=") >= 3
