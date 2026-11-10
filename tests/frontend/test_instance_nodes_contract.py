"""「系统诊断 → 实例」的静态契约。

背景（用户需求）：分布式部署时多个同构实例共用一个 Redis，运维要在一个页面上看到
全部节点。用户明确过三条边界，这里逐条钉住：

1. **不区分角色**：实例是同构、负载均衡的，唯一区别是「谁跑定时调度」，
   由环境变量 ``TASK_SCHEDULER_ENABLED`` 决定，页面只展示、**不提供在线切换**；
2. **中间件也要体现**：数据库类型/版本（MySQL 或 PG）、Redis 版本要在列表上可见；
3. **别名可编辑**：显示名默认 hostname，允许在页面上加别名。

另外钉住「口径不混」：QPS/P95/并发是本节点进程内指标，在线用户数是共享 Redis 的
全平台值，页面上必须区分标注，否则运维会把集群数当成单机数看。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def panel() -> str:
    return _read("frontend/src/components/system/InstanceNodesPanel.vue")


@pytest.fixture(scope="module")
def system_config() -> str:
    return _read("frontend/src/views/SystemConfig.vue")


# --------------------------------------------------------------------------- #
# 入口：二级左栏
# --------------------------------------------------------------------------- #
def test_diagnostics_tab_has_secondary_sidebar(system_config):
    """一级 tab 里「系统诊断」保留，内部再分「诊断 / 实例」两个二级项。"""
    assert "const diagSection = ref<'diag' | 'instances'>('diag')" in system_config
    assert "InstanceNodesPanel" in system_config
    assert "@click=\"diagSection = 'diag'\"" in system_config
    assert "@click=\"diagSection = 'instances'\"" in system_config


def test_existing_diagnostics_content_is_kept_not_replaced(system_config):
    """「诊断」页签仍是原来那一整套（诊断控制台 / Redis 浏览器），不能被新页面挤掉。"""
    assert "diagSection === 'diag'" in system_config
    assert "diagSubTab" in system_config


def test_switching_sections_keeps_diagnostics_state(system_config):
    """用 v-show 而不是 v-if：切到「实例」再切回来，诊断日志不能丢。"""
    assert "v-show=\"diagSection === 'diag'\"" in system_config
    assert "v-show=\"diagSection === 'instances'\"" in system_config


# --------------------------------------------------------------------------- #
# 主 / 从：只读，不允许在线切换
# --------------------------------------------------------------------------- #
def test_primary_flag_is_display_only(panel):
    assert "TASK_SCHEDULER_ENABLED" in panel
    assert "主节点" in panel and "从节点" in panel


def test_panel_has_no_primary_switch_endpoint(panel):
    """没有「切换主从」的写接口：页面改不了进程里的调度开关，只能重启时改环境变量。"""
    for forbidden in ("/primary`", "/promote", "/role`", "setPrimary", "togglePrimary"):
        assert forbidden not in panel


def test_alias_is_the_only_write_operation(panel):
    assert panel.count("axios.put(") == 1
    assert "${encodeURIComponent(target.host)}/alias`" in panel
    assert panel.count("axios.post(") == 0
    assert panel.count("axios.delete(") == 0


# --------------------------------------------------------------------------- #
# 中间件与版本
# --------------------------------------------------------------------------- #
def test_stack_versions_are_surfaced(panel):
    """MySQL/PG 类型与版本、Redis 版本都要在列表上看得见，而不是只藏在详情里。"""
    assert "item.stack.db_type" in panel
    assert "item.stack.db_version" in panel
    assert "item.stack.redis_version" in panel
    assert "item.version.app" in panel


def test_runtime_kind_is_labelled_in_chinese(panel):
    """k8s / docker / 主机进程要翻成人话，不能把 runtime_kind 原样吐给运维。"""
    assert "Kubernetes Pod" in panel
    assert "Docker 容器" in panel
    assert "主机进程" in panel


# --------------------------------------------------------------------------- #
# 口径：本节点 vs 全平台
# --------------------------------------------------------------------------- #
def test_node_local_and_cluster_metrics_are_labelled(panel):
    assert "本节点" in panel
    assert "在线用户（集群）" in panel
    assert "对话流进行中" in panel


def test_page_explains_where_numbers_are_stored(panel):
    """运维要能自己判断数据来源，键名与保留策略写在页面上。"""
    assert "nanzi:instance:{host}" in panel
    assert "nanzi:instances:index" in panel
    assert "nanzi:instance:alias:{host}" in panel


def test_single_node_card_spans_full_width(panel):
    """只有一台机器时卡片要铺满整行。

    固定 `xl:grid-cols-2` 会让单机部署在右边空出一大片——本项目默认就是单副本部署，
    那是绝大多数人第一眼看到的形态。
    """
    assert ":class=\"filtered.length > 1 ? 'xl:grid-cols-2' : ''\"" in panel
    assert "grid grid-cols-1 gap-3 p-3 xl:grid-cols-2" not in panel


# --------------------------------------------------------------------------- #
# 轮询与清理
# --------------------------------------------------------------------------- #
def test_polling_is_15s_and_cleaned_up(panel):
    assert "15000" in panel
    assert "onUnmounted" in panel
    assert "clearInterval" in panel


def test_offline_nodes_stop_counting_uptime(panel):
    """进程退出后运行时长必须停住，不能每秒往上加。"""
    assert "进程已退出" in panel
    assert "已退出" in panel


# --------------------------------------------------------------------------- #
# 后端契约
# --------------------------------------------------------------------------- #
def test_backend_endpoints_require_admin():
    """心跳里有内网 IP、镜像 tag、数据库版本，只能是管理员视角。"""
    source = _read("app/api/portal/endpoints/instances.py")
    assert source.count("require_admin") >= 3
    assert "require_permission" not in source


def test_router_is_registered_under_system():
    source = _read("app/api/portal/api.py")
    assert 'instances.router, prefix="/system/instances"' in source


def test_heartbeat_intervals_match_frontend_copy():
    """前端口径文案来自后端返回的 intervals，常量本身也要与设计一致。"""
    from app.services import instance_registry

    assert instance_registry.HEARTBEAT_INTERVAL_SECONDS == 15
    assert instance_registry.INSTANCE_TTL_SECONDS == 45
    assert instance_registry.SERIES_SAMPLE_INTERVAL_SECONDS == 60
    assert instance_registry.SERIES_TTL_SECONDS == 24 * 3600
    assert instance_registry.SPARK_POINTS == 60
