"""数据源管理页 卡片 / 列表 视图与信息层级契约。

页面形态：默认卡片视图，选择写入 localStorage；两种视图共用同一份数据与操作区，
避免出现「卡片里能摸排、列表里不能」这类分叉。
本轮同时锁定：画像徽章三态语义、连接串复制、类型筛选/排序、操作图标化、骨架屏与筛选空态。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).parents[2]


def _source() -> str:
    return (ROOT / "frontend/src/views/DataSourceManagement.vue").read_text(encoding="utf-8")


def test_data_source_view_mode_state_and_persistence():
    source = _source()

    # 默认卡片视图，并记住用户选择
    assert "const viewMode = ref<'card' | 'list'>(" in source
    assert "localStorage.getItem('data_sources_view_mode')" in source
    assert "localStorage.setItem('data_sources_view_mode', newMode)" in source
    assert "|| 'card'" in source


def test_data_source_view_mode_toggle_buttons():
    source = _source()

    assert 'data-testid="data-source-view-mode-toggle"' in source
    assert 'v-tooltip="\'卡片视图\'"' in source
    assert 'v-tooltip="\'列表视图\'"' in source
    assert "@click=\"viewMode = 'card'\"" in source
    assert "@click=\"viewMode = 'list'\"" in source


def test_data_source_card_and_list_layouts():
    source = _source()

    # 卡片：响应式网格 + 白底卡片 + 浅灰底部工具条
    assert "'grid grid-cols-1 gap-4 rounded-b-xl bg-gray-50/40 p-4 md:grid-cols-2 xl:grid-cols-3'" in source
    assert "rounded-xl border border-gray-200 bg-white shadow-sm" in source
    assert "rounded-b-xl border-t border-gray-100 bg-gray-50/60" in source
    # 列表：窄屏纵向、桌面横向的单行高密度布局
    assert "divide-y divide-gray-100 overflow-visible" in source
    assert "sm:flex-row sm:items-center sm:gap-4" in source


def test_data_source_card_view_shows_db_type_icon():
    source = _source()

    # 数据库类型图标统一走 DbTypeIcon（官方单色 SVG），不再用 emoji
    assert "import DbTypeIcon from '../components/common/DbTypeIcon.vue'" in source
    assert '<DbTypeIcon :type="item.db_type"' in source
    assert '<DbTypeIcon :type="db.id" :size="24" />' in source
    assert "const dbTypeLabel = (type: string) =>" in source
    assert "const dbTypeTint = (type: string) =>" in source
    # 兼容历史别名
    assert "'mssql' ? 'sqlserver'" in source
    for emoji in ("🐬", "🦆", "🧊", "🔴", "🟦", "🐘", "🗄️"):
        assert emoji not in source, f'数据库类型不应再用 emoji：{emoji}'


def test_profile_badge_0_tables_is_not_success_green():
    """0 张画像必须是「未发现表/待摸排」的中性态，不能沿用成功绿。"""
    source = _source()

    assert "type ProfileTone = 'empty' | 'running' | 'done' | 'failed' | 'paused'" in source
    assert "empty: 'bg-gray-50 text-gray-500 border-gray-200'" in source
    assert "done: 'bg-emerald-50 text-emerald-700 border-emerald-100'" in source
    # status=2 且 total=0 归到 empty 分支
    assert "total > 0" in source
    assert "{ tone: 'empty', text: '未发现表'" in source
    assert ":class=\"PROFILE_TONE_CLASS[profileStates[item.id]?.tone || 'empty']\"" in source


def test_profile_progress_uses_full_width_bar():
    source = _source()

    assert "const progressPercent = (item: DbConnectionConfig) =>" in source
    assert ":style=\"{ width: progressPercent(item) + '%' }\"" in source
    # 旧实现是挤在用途标签旁的 80px 小条
    assert 'class="w-20 bg-gray-200 rounded-full h-1' not in source
    assert "h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100" in source


def test_connection_string_is_copyable():
    source = _source()

    assert "const connectionText = (item: DbConnectionConfig) =>" in source
    assert "await navigator.clipboard.writeText(connectionText(item))" in source
    assert "已复制连接地址" in source
    assert '@click="copyConnection(item)"' in source


def test_type_filter_and_sorting():
    source = _source()

    assert "const typeFilter = ref('all')" in source
    assert "const sortBy = ref<'default' | 'name' | 'updated' | 'profiles'>('default')" in source
    assert "const displayConfigs = computed(() => {" in source
    assert "const availableDbTypes = computed(() => {" in source
    assert "const hasActiveFilter = computed(() =>" in source
    for option in ("全部类型", "名称 A→Z", "最近更新", "画像数量"):
        assert option in source
    # 所有渲染入口都必须走 displayConfigs，避免筛选后列表不生效
    assert 'v-for="item in displayConfigs"' in source
    assert 'v-for="item in filteredConfigs"' not in source


def test_low_frequency_actions_are_icon_buttons():
    source = _source()

    # 摸排/测试/编辑/更多 改成图标按钮，提示走平台 v-tooltip，同时保留 aria-label
    for tip, label in (
        ("智能摸排（增量 / 全量 / 中断）", "智能摸排"),
        ("测试连接", "测试连接"),
        ("编辑数据源", "编辑数据源"),
    ):
        assert f'v-tooltip="\'{tip}\'"' in source
        assert f'aria-label="{label}"' in source
    # 主操作仍是可读的文字按钮
    assert "<span>查看画像</span>" in source


def test_data_source_uses_platform_tooltip_instead_of_native_title():
    """页面提示统一走全局 v-tooltip（深色气泡 / 长文本换行 / 触摸端可控），不再用原生 title。

    例外只有 ConfirmModal 的 title / :title 属性——那是组件入参，不是 DOM 提示。
    """
    source = _source()

    assert source.count("v-tooltip=") >= 14
    # 动态提示（画像状态、复制地址、超宽单元格）也要走指令
    for dynamic in (
        'v-tooltip="profileStates[item.id]?.title"',
        'v-tooltip="`点击复制 ${connectionText(item)}`"',
        'v-tooltip="String(cell)"',
    ):
        assert dynamic in source

    # 用途备注是纯展示文本：列表里它撑满剩余宽度，挂提示会让整行右侧误弹气泡
    assert 'v-tooltip="item.description"' not in source
    assert ':title="item.description"' not in source

    # 原生 title 只允许出现在 ConfirmModal 组件属性上
    assert source.count('title="确认删除数据源"') == 1
    assert ":title=\"profileFullReset ? '确认强制全量摸排' : '确认启动智能摸排'\"" in source
    for legacy in (
        'title="卡片视图"',
        'title="列表视图"',
        'title="测试连接"',
        'title="编辑数据源"',
        'title="数据源连接与表画像摸排规范"',
        ':title="item.description"',
        ':title="String(cell)"',
    ):
        assert legacy not in source


def test_loading_skeleton_and_filtered_empty_state():
    source = _source()

    assert "animate-pulse" in source
    assert "没有匹配的数据源" in source
    assert "清除筛选" in source

def test_data_source_actions_shared_by_both_views():
    source = _source()

    # 操作区两种视图共用，只切换容器布局类名；主操作靠左、图标组靠右
    assert "mt-auto flex items-center gap-1 rounded-b-xl" in source
    assert "flex shrink-0 items-center justify-end gap-1" in source
    assert "ml-auto flex items-center gap-1" in source
    for action in ("openTableProfiles(item)", "testSavedConnection(item)", "editConfig(item)"):
        assert action in source


def test_list_view_desc_shares_line_with_connection_meta():
    """列表视图必须两行高密度：用途与「主机 / 库 / 用户」同行，卡片里才换行。"""
    source = _source()

    assert ':class="viewMode === \'card\' ? \'w-full\' : \'flex-1\'"' in source
    assert ":class=\"viewMode === 'card' ? 'line-clamp-2' : 'line-clamp-1'\"" in source
    assert "px-4 py-2.5 transition-colors hover:bg-gray-50" in source
