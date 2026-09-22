"""Redis 浏览器（系统诊断页）Key 业务分组的契约。

背景：该列表原来是把 SCAN 结果平铺展示，最多 5000 条，无法看出「哪些键属于哪个
业务」。现改为按业务命名空间分组、可折叠，并在组头汇总类型分布。

关键约束：前端是按**规则表顺序**用 `startsWith` 做首次匹配的，所以规则必须严格按
前缀长度降序排列 —— 否则 `memory:summary:` 会抢走 `memory:summary:daily:`，
`nanzi:` 之类裸前缀会抢走整个 `nanzi:*` 家族。这些断言就是守住这条排序不变量。

业务分组的划分依据来自对后端 Redis key 命名空间的调研（52 条前缀 → 12 个业务组），
详见 SystemConfig.vue 中 REDIS_KEY_GROUP_RULES 上方的注释。
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")


def _rules_block() -> str:
    source = _source()
    start = source.index("const REDIS_KEY_GROUP_RULES")
    end = source.index("\n]", start)
    return source[start:end]


def _rules() -> list[tuple[str, str]]:
    return re.findall(r"\{ prefix: '([^']+)', label: '([^']+)'", _rules_block())


def _group_of(name: str) -> str:
    """复刻前端的首次匹配语义。"""
    for prefix, label in _rules():
        if name.startswith(prefix):
            return label
    return "其它 Key"


def _browser_block() -> str:
    source = _source()
    return source[source.index("<!-- Tab: Redis Browser -->"):]


def test_rules_cover_all_known_namespaces_and_are_ordered_by_length_desc():
    rules = _rules()

    assert len(rules) >= 45, f"规则数异常偏少（{len(rules)}），可能有命名空间被漏掉"
    lengths = [len(prefix) for prefix, _ in rules]
    assert lengths == sorted(lengths, reverse=True), (
        "规则必须按前缀长度降序：匹配是顺序 startsWith，短前缀在前会遮蔽长前缀"
    )


def test_no_bare_namespace_fallback_rule():
    """过宽的前缀会吞掉更具体的命名空间，必须禁止。"""
    prefixes = {prefix for prefix, _ in _rules()}

    for bare in ("nanzi:", "memory:", "agent:", "auth:", "metadata:", "presence:", "ai:", "sys:"):
        assert bare not in prefixes, f"裸前缀 {bare} 会抢走更具体的命名空间"


def test_known_namespaces_map_to_expected_business_groups():
    mapping = dict(_rules())

    expected = {
        "metadata:dataset:": "元数据与向量索引",
        "conversation:": "会话记忆与上下文",
        "nanzi:agent:ltm:": "长期记忆与摘要",
        "memory:summary:daily:": "长期记忆与摘要",
        "nanzi:chat_request_idempotency:": "并发控制与后台任务",
        "lock:task_exec:": "并发控制与后台任务",
        "auth:user_sessions:": "认证与会话令牌",
        "presence:user:": "在线状态",
        "agent:workspace_recent_files:": "工作台偏好与菜单缓存",
        "kb:citation:": "知识库检索与引用",
        "nanzi:example:": "ChatBI 样例库",
        "ai:user-question:": "AI 交互态与工具缓存",
        "sys_config:": "配置缓存",
        "audit:log_queue": "审计与元数据同步",
    }
    for prefix, label in expected.items():
        assert mapping.get(prefix) == label, f"{prefix} 应归入「{label}」"


def test_production_key_samples_are_grouped_correctly():
    """样本取自生产库 SCAN 结果（含诊断页截图里的真实 Key）。"""
    cases = {
        "metadata:dataset:88:table:t_test_58816c": "元数据与向量索引",
        "metadata:dataset:54:table:ai_agent_execution_x": "元数据与向量索引",
        "nanzi:chat_request_idempotency:62f3737978080b6deeaad5488ac2b6246fbd5ea8c3c15967d25406fa2e2118a4": "并发控制与后台任务",
        "conversation:1:e1d475be-b0a1-4d35-9ff5-7025a78625d5:seq_counter": "会话记忆与上下文",
        "conversation:1:2977238b-1baa-4f81-b47d-c92af098ad73:reusable_result_v1:stack": "会话记忆与上下文",
        "conversation:9:test_conv_files_a604e923:history": "会话记忆与上下文",
        "conversation:1:child_session_subrun_a57297:d:data_result_stack_v1": "会话记忆与上下文",
        "conversation:1:821f53da-cdc3-4dcc-a978-783c6e6e6fd2:model_call_stats": "会话记忆与上下文",
        # 长前缀不得被短前缀抢走
        "memory:summary:daily:7:2026-09-22": "长期记忆与摘要",
        "memory:summary:7:42": "长期记忆与摘要",
        "nanzi:agent:ltm:7:42": "长期记忆与摘要",
        "agent:dataset_navigation_recent_questions:7": "工作台偏好与菜单缓存",
        "agent:dataset_navigation:cache_generation": "工作台偏好与菜单缓存",
        "nanzi:skills:stats:daily:7:2026-09-22": "AI 交互态与工具缓存",
        "nanzi:skills:stats:total": "AI 交互态与工具缓存",
        "metadata_sync:events:7": "审计与元数据同步",
    }
    for key, expected in cases.items():
        assert _group_of(key) == expected, f"{key} 应归入「{expected}」，实际得到「{_group_of(key)}」"


def test_unknown_namespaces_fall_back_to_other_group():
    """历史版本残留或临时调试键不能丢，要落到「其它 Key」并原样展示。"""
    assert _group_of("some:legacy:key:from:old:version") == "其它 Key"

    block = _browser_block()
    assert "'其它 Key'" in block or "其它 Key" in _rules_block() or "其它 Key" in _source(), (
        "需要「其它 Key」兜底分组"
    )


def test_group_ui_supports_collapse_and_bulk_toggle():
    block = _browser_block()

    assert "redisKeyGroups" in block, "列表应按分组渲染"
    assert "isRedisGroupExpanded(group.id)" in block
    assert "toggleRedisGroup(group.id)" in block
    assert "toggleAllRedisGroups" in block
    assert "全部展开" in block and "全部收起" in block
    assert "group.typeSummary" in block, "组头应汇总该组的类型分布"
    # 单组时无需折叠（避免「只搜到一条还要再点一下」）
    assert "redisKeyGroups.value.length <= 1" in _source()


def test_grouping_state_resets_after_each_scan():
    """重新扫描后应回到「全部收起」，避免上一次的展开态串到新结果上。"""
    source = _source()
    fetch = source[source.index("const fetchRedisKeys"):source.index("const fetchRedisKeyDetail")]

    assert "redisKeys.value = res.data.keys || []" in fetch
    assert "expandedRedisGroupIds.value = []" in fetch
