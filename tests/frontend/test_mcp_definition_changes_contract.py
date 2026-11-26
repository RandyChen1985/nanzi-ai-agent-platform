"""MCP 工具定义自动探测与变更确认契约。

为什么这组测试值得存在：

* **探测绝不能等于应用**。``sync_tools()`` 会整批覆盖本地工具定义、清凭据槽、标记
  远端已消失的工具。如果巡检复用这条路径，「每天凌晨看一眼远端」就会变成「每天凌晨
  自动把所有变更上线」。所以这里锁死：探测侧只读，写库只发生在显式的 apply。
* **首次同步不能误报**。``definition_hash`` 为空时代表「本地还没有基线」，这时把远端
  当成「全新定义」去 diff，上线当天会给每个服务每个工具都报一条变更，用户第一次打开
  这个页签就看到几百条噪音，之后再也不会看第二眼。
* **忽略要真能止噪**。忽略只改状态而不推进 ``definition_hash`` 的话，第二天凌晨同一个
  工具会被重新报一遍——人已经确认过「知道这件事」，系统还在天天问。
* 配色是功能的一部分：amber 在这个页面已经被「未发布 / 远端已删除」占用，绿色是
  「已发布」，红色是错误。定义变更一律 violet，否则同一屏里颜色会有两种含义。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE_SERVICE = ROOT / "app" / "services" / "mcp" / "definition_probe.py"
DIFF = ROOT / "app" / "services" / "mcp" / "definition_diff.py"
ENDPOINTS = ROOT / "app" / "api" / "portal" / "endpoints" / "mcp_changes.py"
MCP_CLIENT = ROOT / "app" / "services" / "ai" / "tools" / "mcp_client.py"
SCHEDULER = ROOT / "app" / "services" / "ai" / "scheduler_service.py"
MIGRATION_MYSQL = ROOT / "db-prod" / "V223-mcp-definition-change-tracking.sql"
MIGRATION_PG = ROOT / "db-prod-pg" / "V123-mcp-definition-change-tracking.sql"
REGISTRY = ROOT / "frontend" / "src" / "components" / "system" / "McpServerRegistry.vue"
DRAWER = ROOT / "frontend" / "src" / "components" / "mcp" / "McpDefinitionChangesDrawer.vue"
TAB = ROOT / "frontend" / "src" / "components" / "mcp" / "McpDefinitionChangesTab.vue"
MANAGEMENT = ROOT / "frontend" / "src" / "views" / "McpManagement.vue"
DETAIL = ROOT / "frontend" / "src" / "components" / "mcp" / "McpDefinitionChangeDetail.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_two_migration_dialects_share_the_same_columns() -> None:
    """MySQL 与 PG 两套迁移必须落同样的列，漏一边会让另一个库在探测时 500。"""
    mysql = _read(MIGRATION_MYSQL)
    pg = _read(MIGRATION_PG)
    for column in (
        "last_probe_at",
        "last_probe_status",
        "probe_failure_count",
        "last_probe_error",
        "definition_hash",
        "definition_changed_at",
    ):
        assert column in mysql, f"MySQL 迁移缺少 {column}"
        assert column in pg, f"PG 迁移缺少 {column}"
    assert "sys_mcp_tool_definition_changes" in mysql
    assert "sys_mcp_tool_definition_changes" in pg


def test_probe_never_routes_through_sync_tools() -> None:
    """探测过程只读：变更算完之后由 apply 单独调 sync_tools。"""
    source = _read(PROBE_SERVICE)
    probe_body = source.split("def probe_server(", 1)[1].split("def probe_servers(", 1)[0]
    # 只禁「调用」：错误文案与注释里提到 sync_tools 是解释性说明，不算违规
    assert "sync_tools(" not in probe_body, "probe_server 内不得写库同步工具定义"
    # apply 才允许调同步
    assert "sync_tools" in source.split("async def apply_changes(", 1)[1]


def test_first_probe_records_baseline_without_reporting_changes() -> None:
    """没有基线指纹时只记基线，不产生变更记录。"""
    source = _read(PROBE_SERVICE)
    assert "baseline_tools" in source
    # 空指纹必须早退到 baseline 分支，而不是继续算差异
    assert "_row_definition_hash" in source


def test_dismiss_advances_the_baseline_hash() -> None:
    """忽略要推进 definition_hash，否则同一差异每天重报。"""
    source = _read(PROBE_SERVICE)
    dismiss_body = source.split("async def dismiss_changes(", 1)[1]
    assert "definition_hash" in dismiss_body


def test_severity_split_keeps_breaking_kinds_honest() -> None:
    """删参数 / 改类型 / 可选转必填才算破坏性；新增工具不该被标成破坏性。"""
    source = _read(DIFF)
    assert "BREAKING_KINDS" in source
    for kind in ("param_removed", "param_type"):
        assert kind in source
    assert "SEVERITY_BY_KIND" in source


def test_platform_schema_extensions_are_stripped_before_diff() -> None:
    """平台自己的 ``x-nanzi-*`` 扩展键不算远端定义变化，否则每晚都会假报。"""
    source = _read(DIFF)
    assert "PLATFORM_SCHEMA_EXTENSION_PREFIX" in source
    assert "x-nanzi-" in source


def test_changes_endpoint_requires_sync_permission() -> None:
    """应用/探测都会改动全平台工具定义，统一按 sync 码校验。"""
    source = _read(ENDPOINTS)
    assert "element:mcp_management:sync" in source
    for route in ("/changes", "/probe", "/changes/apply", "/changes/dismiss"):
        assert route in source


def test_sync_tools_writes_hash_and_clears_pending() -> None:
    """同步成功即「已确认远端定义」：写指纹，并把该服务的待确认变更收掉。"""
    source = _read(MCP_CLIENT)
    assert "definition_hash" in source
    assert "McpToolDefinitionChange" in source
    assert '"applied"' in source


def test_nightly_probe_is_registered_with_a_redis_lock() -> None:
    """夜间巡检要有分布式锁，多实例部署时不能每个实例各跑一遍。"""
    source = _read(SCHEDULER)
    assert "system_mcp_definition_probe" in source
    assert "lock:system_mcp_definition_probe" in source


def test_registry_surfaces_changes_without_stealing_publish_colors() -> None:
    """服务卡片上的变更徽标用 violet，且与发布状态徽标并列而非替换。"""
    registry = _read(REGISTRY)
    assert "pending_change_count" in registry
    assert "border-violet-200 bg-violet-50" in registry
    # 工具行：定义变更徽章独立于发布状态三态
    assert "定义已变更" in registry
    assert "hasChangedTool(tool)" in registry


def test_drawer_explains_whole_service_sync_in_confirm() -> None:
    """应用是按服务整批同步，确认框必须说清「未勾选也会一起生效」。"""
    drawer = _read(DRAWER)
    assert "整批同步" in drawer
    assert "未勾选" in drawer


def test_drawer_and_tab_share_one_diff_renderer() -> None:
    """差异渲染只有一份：两处各写一份，改字段时必然有一边落后。"""
    for path in (DRAWER, TAB):
        assert "McpDefinitionChangeDetail" in _read(path)


def test_management_tab_is_admin_only_and_violet() -> None:
    """定义变更页签只给管理员，颜色沿用 violet。"""
    management = _read(MANAGEMENT)
    assert "activeScope === 'changes'" in management
    assert "McpDefinitionChangesTab" in management
    assert "border-violet-600" in management


def test_probe_has_a_per_server_timeout() -> None:
    """单服务探测必须有超时。

    ``list_remote_tools`` 自身没有超时保护，远端「TCP 通了但不回 tools/list」会一直挂着；
    而夜间巡检是 ``max_instances=1``——一次永不返回，之后每天那一趟都会被 APScheduler
    跳过，探测功能等于停摆到进程重启。
    """
    source = _read(PROBE_SERVICE)
    assert "PROBE_TIMEOUT_SECONDS" in source
    assert "asyncio.wait_for(" in source


def test_dismissed_added_and_removed_are_not_re_reported() -> None:
    """忽略过的 tool_added / tool_removed 不能每天重报。

    这两类没有「远端定义」可以推进指纹（新增的工具本地没有缓存行、删除的工具远端没有
    定义），所以必须另存一份「人已忽略」的记忆，否则确认过的待办第二天原样回来。
    """
    source = _read(PROBE_SERVICE)
    assert "_load_dismissed_change_keys" in source
    assert '"dismissed"' in source
    assert '("tool_added", "tool_removed")' in source


def test_visibility_filter_happens_in_sql() -> None:
    """归属裁剪必须在 SQL 里，不能查完再在内存里删——否则 total 变成「本页剩下几条」。"""
    service = _read(PROBE_SERVICE)
    endpoint = _read(ENDPOINTS)
    assert "visible_server_ids" in service
    assert "visible_server_ids=None if visible is None else sorted(visible)" in endpoint
    assert 'total = len(items)' not in endpoint


def test_drawer_actions_require_sync_permission() -> None:
    """应用/忽略是写操作：没权限时按钮不出现，而不是点了才 403。"""
    drawer = _read(DRAWER)
    assert "element:mcp_management:sync" in drawer
    assert 'v-if="canManage"' in drawer
    assert "只读：你没有 MCP 同步权限" in drawer


def test_registry_hides_change_badges_without_permission() -> None:
    """没有 sync 权限（或只读用户）看不到变更徽标与提示条：他们既不能应用也不能忽略。

    只读用户（平台 MCP 页签现在对登录用户可见）同样不该看到这些入口：点进变更抽屉
    会打 admin-only 的 ``/changes`` 并 403，所以条件里额外叠了 ``canSave``。
    """
    registry = _read(REGISTRY)
    assert 'v-if="canSave && canSyncMcp && Number(server.pending_change_count || 0) > 0"' in registry
    assert 'v-if="canSave && canSyncMcp && !definitionHintDismissed && totalPendingChanges > 0"' in registry
    assert 'v-if="canSave && canSyncMcp && tool.is_available !== false && hasChangedTool(tool)"' in registry


def test_tool_badge_chain_is_not_interrupted_by_change_badge() -> None:
    """四态徽标链（服务禁用/远端已删除/已发布/待发布）后面才挂变更徽章。

    中间插一个 ``v-if`` 会把后面的 ``v-else-if="tool.is_published"`` 和 ``v-else``
    接到这个按钮上：变更徽章一出现，发布状态就消失；服务禁用或远端已删除的工具又会
    同时显示矛盾的发布状态。Vue 对此不报错，是纯语义错误。
    """
    registry = _read(REGISTRY)
    published_at = registry.index('<span v-else-if="tool.is_published"')
    else_at = registry.index('<span v-else class="rounded border border-gray-200 bg-gray-100')
    change_at = registry.index('>定义已变更</button>')
    assert published_at < else_at < change_at, "变更徽章必须排在 v-else 之后"


def test_kind_filter_uses_repeated_keys_not_brackets() -> None:
    """数组参数要序列化成 ``kind=a&kind=b``；axios 默认会加方括号，FastAPI 收不到。"""
    tab = _read(TAB)
    assert "paramsSerializer: { indexes: null }" in tab
    assert "serverOptions" in tab, "下拉要并入个人服务，否则那些变更选不到"


def test_apply_result_refreshes_the_tool_list() -> None:
    """应用=整服务 sync_tools：工具定义真变了，右栏列表必须跟着刷新。"""
    registry = _read(REGISTRY)
    start = registry.index("const onDefinitionChangesUpdated = () => {")
    block = registry[start:start + 400]
    assert "fetchTools(id)" in block


def test_baseline_fingerprint_is_written_back() -> None:
    """存量工具第一次探测必须把基线指纹落库，否则每次探测都重建基线、永远报不出变更。"""
    service = _read(PROBE_SERVICE)
    assert "baseline_updates" in service
    assert "values(definition_hash=hash_value)" in service


def test_change_dedupe_key_includes_param() -> None:
    """同一工具两个参数各改一次：键必须带 param，否则会互相覆盖并留下僵尸待办。"""
    service = _read(PROBE_SERVICE)
    assert "def _change_key(" in service
    assert "def _row_param(" in service
    assert "existing_by_key.setdefault(" in service


def test_new_changes_counts_only_newly_created() -> None:
    """``new_changes`` 只算新落库的：否则一条没处理的差异会每天触发一次告警站内信。"""
    service = _read(PROBE_SERVICE)
    assert "new_counter" in service
    assert 'result["new_changes"] = new_counter[0] if new_counter else 0' in service


def test_hot_probe_skips_user_assertion_services() -> None:
    """按用户身份返回的工具子集不能拿去和全量缓存比——会把无权使用的工具判成已删除。"""
    client = _read(ROOT / "app" / "services" / "ai" / "tools" / "mcp_client.py")
    start = client.index("async def _run_hot_probe")
    block = client[start:client.index("_HOT_PROBE_TASKS: set", start)]
    assert "user_assertion_enabled" in block


def test_detail_renders_every_snapshot_field() -> None:
    """后端采集哪些字段，详情就必须展示哪些。

    漏一个的后果不是「少显示一行」，而是「后端判定为变更、点进来表格里一行标红的
    都没有」——用户会以为这条待办是空的。`anyOf`/`oneOf` 还是对象数组，直接 join
    会渲染成 `[object Object]、[object Object]`。
    """
    detail = _read(DETAIL)
    for key in ("anyOf", "oneOf", "const", "format", "minimum", "maximum", "items"):
        assert f"key: '{key}'" in detail, f"详情缺少 {key} 字段"
    assert "function formatItem(" in detail
    assert "value.join('、')" not in detail, "对象数组不能直接 join"


def test_dashboard_counts_come_from_the_server_not_from_items() -> None:
    """统计卡的数字必须由服务端 SQL 算。

    拿 `items` 过滤有两个坑：① `items` 带 `limit`（默认 200），待办一多就少算；
    ② 切到「已应用 / 已忽略」分段后 `items` 里没有 pending 行，卡片直接显示 0，
    而标题写着「破坏性待确认」——同排第一张卡是全局口径，两张卡对不上。
    """
    tab = _read(TAB)
    endpoint = _read(ENDPOINTS)
    assert 'breaking_pending' in endpoint
    assert '"breaking": 0' not in tab
    assert "counts.value.breaking_pending" in tab
    # 不允许再用 items 过滤出「待确认破坏性」总数
    assert "items.value.filter((i) => i.status === 'pending' && i.severity === 'breaking')" not in tab
