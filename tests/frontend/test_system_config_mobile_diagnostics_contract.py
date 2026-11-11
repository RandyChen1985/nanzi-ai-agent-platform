"""系统配置 → 系统诊断的窄屏布局契约。

手机上（375~430px）原来右侧内容区只剩约 200px：诊断内部那个固定 `w-44`
（176px）的二级左栏把宽度吃光，于是——

* 「服务连接与组件能力概览」被折成三行，「收起概览」挤在右上角；
* Redis 卡片的标题竖排成「Red / 服 / 务」，徽章压在标题上；
* 「测试连接」「扫描 Keys」被裁到屏幕外；
* 实例页的「自动刷新 15s：开」折成两行，「卡片 / 表格」变成「卡 / 片」；
* 节点卡片名字只剩「演…」，主/从徽标「主节 / 点」竖排。

所以窄屏的三条底线：**二级导航横排、卡片内部可换行、中文按钮不折字**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/SystemConfig.vue"
NODES = ROOT / "frontend/src/components/system/InstanceNodesPanel.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nodes() -> str:
    return NODES.read_text(encoding="utf-8")


def test_diagnostics_subnav_stacks_above_content_on_narrow_screens(page: str) -> None:
    """二级导航在窄屏变成顶部横排页签，宽屏才回到左侧栏。"""
    assert 'class="flex h-full min-h-0 flex-col gap-3 lg:flex-row lg:gap-4"' in page
    # 左栏宽度只在 lg 生效：固定 176px 会把右侧压到 200px 以下
    assert '<aside class="flex shrink-0 lg:w-44">' in page
    assert '<aside class="flex w-44 shrink-0">' not in page


def test_diagnostics_subnav_hides_its_chrome_on_narrow_screens(page: str) -> None:
    """窄屏只留两个页签：组标题与底部说明在手机上只是白占高度。"""
    assert 'class="hidden px-2 pb-1.5 pt-1 text-[10.5px] font-semibold tracking-wide text-gray-400 lg:block"' in page
    assert 'class="mt-auto hidden rounded-lg border border-dashed border-gray-200 bg-gray-50/60' in page
    # 两个页签并排时平分宽度、不折字
    assert page.count("flex flex-1 items-center justify-center gap-2 whitespace-nowrap") == 2


def test_service_cards_header_wraps_instead_of_squeezing(page: str) -> None:
    """徽章放不下就换行，不能压着标题——否则标题被逐字竖排。"""
    assert page.count('class="flex flex-wrap items-start justify-between gap-3"') == 2
    assert page.count("whitespace-nowrap text-base font-semibold text-gray-900") == 2


def test_service_card_meta_strip_and_actions_wrap(page: str) -> None:
    assert (
        'class="mt-3.5 mb-3 py-1.5 px-3 rounded-lg bg-gray-50/80 border border-gray-100 text-xs text-gray-500 flex flex-wrap items-center justify-between gap-2"'
        in page
    )
    # 三个操作按钮挤不下时整组换行
    assert '<div class="border-t border-gray-100 pt-3 flex flex-wrap items-center justify-between gap-2">' in page


def test_redis_browser_columns_stack_on_narrow_screens(page: str) -> None:
    """两列各 ~150px 时键名只剩几个字，窄屏必须上下叠。"""
    assert "lg:flex-row lg:gap-0 lg:space-x-4" in page
    # 左列：窄屏整宽、有界高度（内部列表才滚得动），宽屏才回到 2/5 并撑满
    assert "max-h-[45vh] min-h-[240px] w-full flex-col" in page
    assert "lg:h-full lg:max-h-none lg:w-2/5" in page


def test_console_tabs_do_not_break_into_vertical_text(page: str) -> None:
    assert page.count("flex cursor-pointer items-center whitespace-nowrap rounded-md px-3.5") == 2


def test_instance_toolbar_controls_never_wrap_their_labels(nodes: str) -> None:
    assert "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border px-2 py-1" in nodes
    assert nodes.count("whitespace-nowrap rounded-md px-2.5 py-1 text-[11px]") == 2


def test_instance_stats_metrics_drop_to_two_columns(nodes: str) -> None:
    """四格指标在窄屏每格只有 ~72px，「在线用户」会和数字叠在一起。"""
    assert "mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4" in nodes


def test_long_env_flag_does_not_overflow_the_card(nodes: str) -> None:
    assert 'class="break-all text-[11px] text-gray-400">TASK_SCHEDULER_ENABLED=true' in nodes


def test_instance_card_name_and_badge_are_readable(nodes: str) -> None:
    """名字 240px 上限只在宽屏有意义；窄屏要给满可用宽度，否则只剩「演…」。"""
    assert "max-w-full truncate text-sm font-semibold text-gray-900 lg:max-w-[240px]" in nodes
    assert "whitespace-nowrap rounded-full border px-1.5 py-0.5 text-[10px] font-semibold" in nodes
