"""平台全局 tooltip 指令契约。

`v-tooltip` 是平台统一的提示气泡，替代浏览器原生 `title`；这里锁定两件事：
1. 指令保持全局注册，页面不需要逐个 import；
2. 气泡层级必须高于业务弹层、低于全局 Toast——否则 Modal 内的提示会被盖住。
"""

from pathlib import Path
import re

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).parents[2]


def _tooltip_source() -> str:
    return (ROOT / "frontend/src/directives/tooltip.ts").read_text(encoding="utf-8")


def test_tooltip_directive_is_registered_globally():
    main = (ROOT / "frontend/src/main.ts").read_text(encoding="utf-8")

    assert "import { tooltip } from '@/directives/tooltip'" in main
    assert "app.directive('tooltip', tooltip)" in main


def test_tooltip_bubble_sits_above_modals_below_toast():
    source = _tooltip_source()
    match = re.search(r"const BUBBLE_Z_INDEX = '(\d+)'", source)
    assert match, 'BUBBLE_Z_INDEX 必须显式声明'
    z_index = int(match.group(1))

    # 业务弹层最高到 ConfirmModal 的 z-[13000]，Toast 是 z-[100000]
    assert z_index > 13000, '气泡会被 Modal / ConfirmModal 盖住'
    assert z_index < 100000, '气泡不应盖住全局 Toast'
    assert "zIndex: BUBBLE_Z_INDEX," in source


def test_tooltip_keeps_single_native_title_free_bubble():
    source = _tooltip_source()

    # 原生 title 必须移除，避免原生提示与自定义气泡叠加
    assert "el.removeAttribute('title')" in source
    # 长文本需要换行而不是被压成一行
    assert "whiteSpace: 'normal'" in source
    assert "wordBreak: 'break-word'" in source
    # 不在 DOM 里留常驻节点：离开 / 滚动 / 缩放即销毁
    assert "pointerEvents: 'none'" in source
    assert "window.addEventListener('scroll', onViewportChange, true)" in source
    assert "window.addEventListener('resize', onViewportChange)" in source
