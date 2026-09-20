"""知识库运营分析页契约：骨架/错误/空态、真实排序、下钻、导出、可访问性与竞态防护。

这些断言锁定的是「容易在后续迭代中被改回去」的体验与正确性约定，
而不是具体实现细节，因此以源码契约形式固定下来。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


VIEW = "frontend/src/views/KnowledgeMetrics.vue"
CHART_UTILS = "frontend/src/utils/knowledgeMetricsChart.ts"
STAT_CARD = "frontend/src/components/knowledge/KnowledgeMetricStatCard.vue"
EMPTY_STATE = "frontend/src/components/knowledge/KnowledgeMetricsEmptyState.vue"


def test_metrics_view_loads_and_error_states_are_visible_not_only_toasts():
    view = _source(VIEW)

    # 骨架屏：首屏不再以 0 占位后跳变
    assert "animate-pulse" in view
    assert "showSkeleton" in view

    # 错误态：页面内有可重试入口，而不是只弹一次 toast
    assert 'role="alert"' in view
    assert "errorMessage" in view
    assert "重试" in view

    # 空态：抽出统一引导组件，并提供下一步动作
    assert "KnowledgeMetricsEmptyState" in view
    empty_state = _source(EMPTY_STATE)
    assert "去知识库管理" in empty_state
    assert "/dashboard/knowledge-bases" in empty_state


def test_metrics_view_toast_only_fires_for_explicit_refresh():
    view = _source(VIEW)

    # 成功提示必须受显式 notify 控制，避免切区间/首屏也刷 toast
    assert "notify?: boolean" in view
    assert "notify: true" in view
    assert "if (options.notify) showToast('数据已刷新', 'success')" in view

    # 业务失败不得再提示成功
    assert "res.data.code !== 0" in view


def test_metrics_view_guards_against_out_of_order_responses():
    view = _source(VIEW)

    assert "AbortController" in view
    assert "requestSeq" in view
    assert "signal: controller.signal" in view
    # 被取消/被取代的请求不得写入错误态
    assert "isCanceled(error)" in view


def test_metrics_view_ranking_uses_server_side_sort_not_client_resort():
    view = _source(VIEW)

    # 排序依据必须回传后端，Top10 集合才真的按所选指标选取
    assert "order_by: orderBy.value" in view
    assert "按引用量" in view
    assert "按检索量" in view
    assert "selectOrderBy" in view


def test_metrics_view_supports_knowledge_base_drilldown_and_export():
    view = _source(VIEW)

    # 知识库下钻：dataset 行的 target_id 本身就是知识库 id，无需额外字段
    assert "onDatasetChartClick" in view
    assert "query: { dataset: item.id }" in view

    # 护栏：文档下钻需要「文档 → 所属知识库」归属信息，会牵出表结构变更与历史数据回填，
    # 当前经决策明确不做，避免被无意中重新引入。
    assert "onDocumentChartClick" not in view

    # 导出
    assert "buildMetricsCsv" in view
    assert "exportCsv" in view


def test_metrics_summary_endpoint_is_admin_only():
    """平台级运营视图只对管理员开放：此前仅校验登录即可读取全平台知识库名与调用量。"""
    endpoint = _source("app/api/portal/endpoints/ragflow.py")

    assert '@router.get("/metrics/summary", dependencies=[Depends(require_admin)])' in endpoint


def test_metrics_endpoint_merges_before_querying():
    """Redis → DB 归并必须先于查询。

    指标埋点写 Redis 是实时的，但本接口只读 DB；若把归并挪到 BackgroundTasks
    （响应发出后才执行），本次响应就会读到归并前的旧值，用户必须连刷两次才看到最新数据。
    """
    endpoint = _source("app/api/portal/endpoints/ragflow.py")
    route_start = endpoint.index('@router.get("/metrics/summary"')

    merge_index = endpoint.index(
        "await KnowledgeMetricsService.sync_redis_metrics_to_db()", route_start
    )
    query_index = endpoint.index(
        "data = await KnowledgeMetricsService.get_metrics_summary(", route_start
    )

    assert merge_index < query_index
    assert "background_tasks.add_task(KnowledgeMetricsService.sync_redis_metrics_to_db)" not in endpoint


def test_metrics_view_has_custom_range_and_comparison_and_freshness():
    view = _source(VIEW)

    assert "自定义" in view
    assert "type=\"date\"" in view
    assert "applyCustomRange" in view
    # 环比与数据新鲜度
    assert "computeDelta" in view
    assert "formatDataFreshness" in view
    assert "较上期" in _source(STAT_CARD)


def test_metrics_view_is_accessible():
    view = _source(VIEW)

    # 按钮组语义与按下状态
    assert 'role="group"' in view
    assert "aria-pressed" in view
    # 移动端只剩图标时仍有可访问名，刷新态可播报
    assert 'aria-label="刷新数据"' in view
    assert "aria-busy" in view
    # 图表提供文本替代
    assert ":aria-label=\"trendAriaLabel\"" in view
    assert "知识库${orderByLabel}排行榜" in view
    # 日期输入有 label
    assert 'for="metrics-start-date"' in view
    assert 'for="metrics-end-date"' in view


def test_metrics_view_drops_the_force_remount_hack():
    view = _source(VIEW)

    # 旧的 chartRenderKey 重建 + 全局 resize 派发已移除，改由响应式 option 驱动
    assert "chartRenderKey" not in view
    assert 'window.dispatchEvent(new Event("resize"))' not in view
    # 也不再用硬截断标签的方式压缩中文名
    assert "shortenLabel" not in view


def test_chart_utils_centralize_theme_sorting_and_colour_contrast():
    utils = _source(CHART_UTILS)

    for exported in (
        "buildTrendOption",
        "buildRankingOption",
        "computeDelta",
        "computeCitationRate",
        "formatCount",
        "buildMetricsCsv",
        "formatDataFreshness",
    ):
        assert f"export function {exported}" in utils, exported

    # 旧的低对比浅色必须被替换（白底对比度不足 3:1）
    assert "#93c5fd" not in utils
    assert "#c7d2fe" not in utils

    # 色觉障碍友好：启用 decal 纹理，并用虚线区分第二条趋势
    assert "decal" in utils
    assert "type: 'dashed'" in utils

    # 排行榜改为横向条形图，长中文名无需旋转即可完整展示
    assert "inverse: true" in utils
    assert "overflow: 'truncate'" in utils

    # 千分位格式化复用同一份 Intl 实例
    assert "const countFormatter = new Intl.NumberFormat('zh-CN')" in utils


def test_metrics_date_range_is_inclusive_without_off_by_one():
    utils_view = _source(VIEW)

    # 近 N 天按「含今天在内的 N 天」计算，不再多算一天
    assert "end.getDate() - (days - 1)" in utils_view
    assert "end.getDate() - 7" not in utils_view


def test_knowledge_base_management_accepts_drilldown_query():
    view = _source("frontend/src/views/KnowledgeBaseManagement.vue")

    assert "applyDeepLinkFromQuery" in view
    assert "route.query.dataset" in view
    assert "applyDeepLinkFromQuery()" in view


def test_tool_path_citation_numbering_is_turn_global():
    """工具路径的 `[ID:n]` 必须整轮全局唯一。

    若每次 search_knowledge_base 都从 1 重新编号，一轮内的多次检索会让模型看到重复
    编号，回答里的引用无法反查属于哪一批切片——统计出的引用量会被安到错误的文档上。
    """
    tool = _source("app/services/ai/tools/knowledge_tool.py")
    runner = _source("app/services/ai/runners/assistant_agent_runner.py")

    # 工具侧：编号改由台账发放并登记切片
    assert "get_or_create_knowledge_citation_ledger" in tool
    assert "citation_ledger.register(chunk)" in tool

    # 智能体侧：每轮建立台账，回答产出后再统计引用量
    assert "new_turn_knowledge_citation_ledger(ctx)" in runner
    assert "record_citation_hits_for_refs(entries, cited_ref_ids)" in runner


def test_search_hits_are_recorded_exactly_once_per_retrieval():
    """检索量只能在知识库工具里记一次。

    知识库执行器（KnowledgeAgentRunner）的自动预取本身就是调用 search_knowledge_base
    完成的：工具内记一次、runner 再按预取列表记一次，同一批切片就被累加两次，检索量
    虚高并连带压低引用率。这是已经踩过的坑，此处加护栏。
    """
    tool = _source("app/services/ai/tools/knowledge_tool.py")
    knowledge_runner = _source("app/services/ai/runners/knowledge_agent_runner.py")

    assert "record_search_hits(chunks)" in tool
    assert "record_search_hits(" not in knowledge_runner
    # 执行器的引用量统计优先走台账（可覆盖 ReAct 中的二次检索）
    assert "record_citation_hits_for_refs(" in knowledge_runner


def test_citation_metrics_are_not_gated_by_the_grounding_guard():
    """引用量不得由事实一致性网关门控。

    检索量由知识库工具在检索时记录，本就不受网关限制；若引用量只在 `passed_guard`
    为真时才记，被判定幻觉、重写后仍未通过的回答就会出现「有检索、零引用」，引用率被
    系统性压低甚至恒为 0——用户实测正是如此（回答里有 [ID:1]/[ID:3]，页面却显示 0）。
    网关判的是「有没有无依据表述」，不是「有没有引用」。
    """
    runner = _source("app/services/ai/runners/knowledge_agent_runner.py")
    lines = runner.splitlines()

    call_index = next(
        index
        for index, line in enumerate(lines)
        if "record_citation_hits_for_refs(" in line
    )
    # 向上回溯该埋点所属的最外层条件（8 空格缩进的 if）
    condition = next(
        line for line in reversed(lines[:call_index]) if line.startswith("        if ")
    )

    assert "passed_guard" not in condition
    assert "prefetched_citations_raw" in condition
