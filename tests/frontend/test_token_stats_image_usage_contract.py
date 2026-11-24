"""Token 统计页「图片用量」Tab 的呈现契约。

## 背景

生图/改图的用量留痕在轨迹 `meta_info.image_usage`，刻意不进 `total_tokens`
（那个数驱动月度配额）。于是本页原来的三块（趋势 / 智能体分布 / 用户账单）全部
按 Token 聚合，**图片在里面一律是空的**——用户按「我那张图花了多少」来查，只能
一轮一轮点进轨迹。

这个 Tab 是图片只有一个汇总入口，所以它必须自己说清三件事，否则会被误读：

1. **上游 Token 不是人民币**：平台没接单价，这里只是网关原样返回的 token 累加；
2. **「上游未返回」不能显示成 0**：多数图像端点不返回 `usage`，显示 0 会被读成
   「这次没花钱」，必须显式说明有几个调用拿不到数字；
3. **图片不并入月度配额**：额度页永远不会因为出图而扣减，这是刻意设计。

## 要求

- 两个 Tab 共用顶部同一个时间选择器，切 Tab 不重置区间；
- 加载中和「暂无数据」必须是不同分支（本页既有的教训，见
  `test_token_stats_page_contract.py`）；
- 导出与页面同源：同一个端点族、同一套聚合参数，不允许前端另算一遍；
- 「按用户」维度只对管理员展示——后端对普通用户直接 400。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOKEN_STATS = ROOT / "frontend/src/views/TokenStats.vue"
IMAGE_PANEL = ROOT / "frontend/src/components/usage/ImageUsagePanel.vue"

ENDPOINT = "/api/portal/dashboard/token-stats/image-usage"


def _stats_source() -> str:
    return TOKEN_STATS.read_text(encoding="utf-8")


def _panel_source() -> str:
    assert IMAGE_PANEL.exists(), f"缺少图片用量面板组件：{IMAGE_PANEL}"
    return IMAGE_PANEL.read_text(encoding="utf-8")


def _normalized(source: str) -> str:
    return re.sub(r"\s+", " ", source)


def test_token_stats_page_has_image_usage_tab():
    """页面必须给出第二个 Tab，否则图片用量无处可达。"""
    source = _normalized(_stats_source())

    assert "图片用量" in source, "Token 统计页应有「图片用量」Tab"
    assert "ImageUsagePanel" in source, "图片 Tab 应复用独立组件，别把页面撑成第二个巨型文件"


def test_both_tabs_share_one_date_range():
    """切 Tab 不能重置区间：两个 Tab 共用页头同一个月历。"""
    source = _normalized(_stats_source())

    # 面板必须接收页面上的区间，而不是自己再造一个
    assert "date-range" in source or ":dateRange" in source
    assert "DateRangePicker" in source


def test_panel_calls_the_aggregate_endpoint():
    """取数端点只有一处：页面与导出一致，不允许前端自行聚合轨迹。"""
    source = _normalized(_panel_source())

    assert ENDPOINT in source
    assert "group_by" in source, "分组维度要传给后端，前端不重算"
    # 导出必须是同一端点族的 /export，且参数来自同一个构造器（否则页面与导出会分叉）
    assert "/export" in source
    assert source.count("queryParams()") >= 2, "页面取数与导出应共用同一份查询参数"


def test_panel_explains_missing_upstream_tokens():
    """上游没给 usage 时不能显示 0，否则会被读成「这次没花钱」。"""
    source = _normalized(_panel_source())

    assert "tokens_missing_calls" in source
    assert "上游未返回" in source


def test_panel_states_image_usage_is_outside_token_total():
    """必须写明不并入大模型 Token、也不扣额度。

    「额度没涨但上游账单涨了」是最容易被当成 bug 报上来的问题，说明必须留在页面上
    （用户只要求删掉开头那句「按张计费 / 不计入配额」，这条语义不能跟着一起没）。
    """
    source = _normalized(_panel_source())

    assert "不并入" in source
    assert "额度" in source


def test_loading_and_empty_are_separate_branches():
    """加载中不能落到「暂无数据」分支：本页历史上就是这个 bug。"""
    source = _normalized(_panel_source())

    assert "加载中" in source
    assert "暂无" in source
    # 裸的空态判断（没有 loading 参与）会把「正在请求」显示成「确实没有」
    assert not re.search(r'v-if="[^"]*(groups|trends)\.length === 0"', source), (
        "空态判断必须避开 loading 状态，不能裸判 length === 0"
    )


def test_user_dimension_is_admin_only():
    """「按用户」只对管理员展示：后端对普通用户直接 400，前端不该给出这个选项。"""
    source = _normalized(_panel_source())

    assert "isAdmin" in source or "is-admin" in source
    assert 'value="user"' in source or '"user"' in source
