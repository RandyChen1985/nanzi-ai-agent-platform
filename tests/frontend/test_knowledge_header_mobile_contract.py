"""知识库管理页顶部操作区的移动端布局契约。

「引擎状态 / 从 RAGFlow 同步 / 刷新 / 新建知识库」在窄屏并排会把中文挤成两三行、
高度参差；排成两行又白占一行竖向空间。最终口径是：窄屏一行图标（引擎状态压成
圆点 + 短文案），lg 起恢复文字按钮。

断点用 lg 而不是 md/sm：768~1023 区间左侧导航已经占掉宽度，四个文字按钮放不下。
"""

from __future__ import annotations

from pathlib import Path

import pytest

VIEW = Path("frontend/src/views/KnowledgeBaseManagement.vue")
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _actions_block(source: str) -> str:
    start = source.index("操作区：窄屏一行图标")
    end = source.index("<!-- 知识库 5 步全流程指引横幅 -->", start)
    return source[start:end]


def test_narrow_actions_are_icon_only_in_a_single_row():
    actions = _actions_block(_source())

    # 单行 flex 操作区；前面多出来的 relative z-10 是为了压在顶部卡片的书架剪影之上，
    # 所以这里只校验与布局意图相关的类，不再钉死整串 class
    assert "shrink-0 items-center gap-2 lg:gap-3" in actions
    assert "relative z-10 flex shrink-0 items-center gap-2 lg:gap-3" in actions
    # 不回退到两行网格：那正是上一次改动被否掉的原因（占竖向空间）
    assert "grid-cols-2" not in actions
    assert "col-span-2" not in actions

    # 四个操作按钮（引擎状态 → 同步 → 刷新 → 新建）：窄屏 40x40 方形，
    # lg 恢复文字按钮的自动宽度。加按钮时这里要跟着改。
    assert actions.count("inline-flex h-10 w-10 shrink-0") == 4
    assert actions.count("lg:h-auto lg:w-auto") == 3

    # 图标按钮没有可见文字，必须有可读名称（带引号，避开注释里的提法）
    assert actions.count('aria-label="') == 4
    assert "刷新知识库列表" in actions
    assert "新建知识库" in actions
    assert "从 RAGFlow 同步" in actions


def test_icon_and_text_variants_toggle_at_lg():
    actions = _actions_block(_source())

    # 图标只在窄屏出现，文字只在 lg 起出现，避免同时渲染图标和文字
    assert actions.count('class="h-5 w-5 lg:hidden"') == 3
    assert actions.count("hidden whitespace-nowrap text-sm font-medium lg:inline") == 3

    # 引擎状态：窄屏短文案，桌面保留「引擎 + 完整状态」
    assert "{{ engineStatusShort }}" in actions
    assert "引擎 {{ engineStatusText }}" in actions
    source = _source()
    assert "const engineStatusShort = computed(" in source
    # 短文案不能再套完整状态（「知识库引擎连接中 ...」会把图标行撑宽）
    short_block = source[source.index("const engineStatusShort"):]
    short_block = short_block[: short_block.index("const selectedDatasetId")]
    assert "engineStatusText" not in short_block


def test_header_stacks_title_and_actions_until_desktop():
    source = _source()

    # lg 以下标题与操作区上下堆叠：800px 左右并排会把操作区压成一条窄缝
    assert "flex flex-col lg:flex-row lg:items-center justify-between gap-4" in source
    # 标题区可收缩，避免把操作区挤出容器
    assert "flex min-w-0 flex-col gap-1" in source
