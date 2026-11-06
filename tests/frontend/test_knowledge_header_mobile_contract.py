"""知识库管理页顶部标题行 / 操作区的移动端布局契约。

「刷新 / 新建知识库」在窄屏并排会把中文挤成两三行、高度参差；排成两行又白占
一行竖向空间。最终口径是：窄屏一行图标，lg 起恢复文字按钮。

断点用 lg 而不是 md/sm：768~1023 区间左侧导航已经占掉宽度，多个文字按钮放不下。

引擎状态不参与这条行的并排计算：它挂在标题行末尾，正常态只是一个圆点
（hover 出地址 / Key），检测中 / 断连 / 功能关闭才补两三个字。
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


def _title_row_block(source: str) -> str:
    start = source.index("引擎连接状态：正常态只留一个小圆点")
    end = source.index("<!-- 折叠按钮独立于整卡点击", start)
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

    # 三个操作按钮（刷新 → 审计 → 新建）：窄屏 40x40 方形，
    # lg 恢复文字按钮的自动宽度（审计按钮在桌面仍是方形图标，所以这里只数 2）。
    assert actions.count("inline-flex h-10 w-10 shrink-0") == 3
    assert actions.count("lg:h-auto lg:w-auto") == 2

    # 图标按钮没有可见文字，必须有可读名称（带引号，避开注释里的提法）
    assert actions.count('aria-label="') == 3
    assert "刷新知识库列表" in actions
    assert "新建知识库" in actions
    # 「从 RAGFlow 同步」从这里移除了：概览面板右上角有同一个入口，并排纯属重复。
    # 这是一条反向断言，防止有人顺手加回来。
    assert "从 RAGFlow 同步" not in actions


def test_icon_and_text_variants_toggle_at_lg():
    actions = _actions_block(_source())

    # 图标只在窄屏出现，文字只在 lg 起出现，避免同时渲染图标和文字
    assert actions.count('class="h-5 w-5 lg:hidden"') == 2
    assert actions.count("hidden whitespace-nowrap text-sm font-medium lg:inline") == 2


def test_engine_status_is_a_dot_in_the_title_row():
    """引擎状态已从操作区移到标题行：正常态只留圆点，异常 / 检测中才补短文案。

    圆点不占操作区那一行，窄屏图标按钮不会因为它被挤成两行；
    反过来，「一个黄点」必须配文字，否则用户不知道是引擎断了还是功能被关了。
    """
    source = _source()
    head = _title_row_block(source)

    # 正常态没有文字：短文案在已连接时返回空串，模板靠 v-if 兜住
    assert 'v-if="engineStatusHint"' in head
    # 圆点本身没有文字，无障碍名称必须带主语，不能直接复用短文案
    assert "engineStatusAriaLabel" in head
    # 完整配置（地址 / Key）收在 hover tooltip 里
    assert "知识库引擎信息" in head
    assert "ragflowApiUrl" in head

    # 反向断言：操作区里不能再出现引擎指示器，防止有人顺手加回来
    actions = _actions_block(source)
    assert "知识库引擎信息" not in actions
    assert "engineStatusHint" not in actions


def test_engine_status_hint_stays_short():
    source = _source()
    assert "const engineStatusHint = computed(" in source
    block = source[source.index("const engineStatusHint"):]
    block = block[: block.index("const engineStatusAriaLabel")]
    # 短文案不能再套完整描述（「知识库引擎连接中 ...」会把标题行撑宽）
    assert "知识库引擎" not in block
    # 已连接时必须留空，不能常驻「已连接」三个字
    assert "return ''" in block


def test_header_stacks_title_and_actions_until_desktop():
    source = _source()

    # lg 以下标题与操作区上下堆叠：800px 左右并排会把操作区压成一条窄缝
    assert "flex flex-col lg:flex-row lg:items-center justify-between gap-4" in source
    # 标题区可收缩，避免把操作区挤出容器
    assert "flex min-w-0 flex-col gap-1" in source
