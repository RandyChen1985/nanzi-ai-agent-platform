"""委派上限在管理端的可见性与接线契约。

配置化的目的是让运维能调，而不是把数字换个地方写死：设置页必须真的展示这两项、
走数字输入、并在后端保存时校验。任何一环漏了，管理员都只能看到默认值。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_system_config_page_lists_delegation_limits_in_agent_group():
    source = _read("frontend/src/views/SystemConfig.vue")

    assert "'delegation_max_batch_calls'" in source
    assert "'delegation_max_calls_per_agent'" in source

    # 必须是带上下限的 −/+ 步进控件，而不是拉满宽度的裸文本框：
    # 范围要与后端校验一致（batch 1-20、per-agent 1-10）。
    assert "DELEGATION_LIMIT_RANGES" in source
    assert "delegation_max_batch_calls: { min: 1, max: 20, fallback: 4, unit: '个' }" in source
    assert "delegation_max_calls_per_agent: { min: 1, max: 10, fallback: 2, unit: '次' }" in source
    assert 'v-else-if="DELEGATION_LIMIT_RANGES[item.key]"' in source
    assert "adjustDelegationLimit(item, -1)" in source
    assert "adjustDelegationLimit(item, 1)" in source
    assert "normalizeDelegationLimitInput(item)" in source
    # 加载时先把库里的空值/历史非法值收敛到合法范围
    assert "item.value = String(getDelegationLimitValue(item))" in source


def test_backend_validates_and_hot_updates_delegation_limits():
    config_service = _read("app/services/config_service.py")
    delegate_tool = _read("app/services/ai/tools/agent_delegate_tool.py")

    assert "DELEGATION_MAX_BATCH_CALLS_KEY" in config_service
    assert "set_max_batch_delegation_calls" in config_service
    assert "set_max_delegation_calls_per_agent" in config_service

    assert "def validate_max_batch_delegation_calls" in delegate_tool
    assert "def validate_max_delegation_calls_per_agent" in delegate_tool
    # 描述必须跟着上限变：模型读的是 description
    assert "_sync_delegation_tool_texts" in delegate_tool
    # 读不到配置时不能打挂委派
    assert "refresh_delegation_limits_from_config" in delegate_tool


def test_batch_tool_uses_configured_limit_at_runtime():
    source = _read("app/services/ai/tools/agent_delegate_tool.py")

    # 运行时校验用 getter，而不是模块级常量
    assert "max_calls = get_max_batch_delegation_calls()" in source
    assert "get_max_delegation_calls_per_agent()" in source
    # 配置上限错误要明确告诉模型当前值，否则它只会反复重试
    assert "批量委派一次最多" in source


def test_migrations_seed_both_keys_for_mysql_and_postgres():
    mysql = _read("db-prod/V204-add-delegation-limit-configs.sql")
    postgres = _read("db-prod-pg/V104-add-delegation-limit-configs.sql")

    for sql in (mysql, postgres):
        assert "delegation_max_batch_calls" in sql
        assert "delegation_max_calls_per_agent" in sql
        assert "'4'" in sql and "'2'" in sql

    # 可重跑：已存在时只纠正描述与分组，不覆盖运维调过的值
    assert "ON DUPLICATE KEY UPDATE" in mysql
    assert "ON CONFLICT" in postgres and "DO UPDATE" in postgres


def test_delegation_limit_template_reads_range_through_the_safe_helper():
    """模板里取 min/max/unit 必须走带 fallback 的 helper，不能裸下标。

    同文件已有 `getDelegationLimitRange()`（未知 key 回落 1-20），但 aria-label、
    单位与范围提示仍是裸 `DELEGATION_LIMIT_RANGES[item.key].min`。一旦后端新增一个
    限额 key 而这里忘配 range，模板取值即抛错，整页白屏——分支判定
    `v-else-if="DELEGATION_LIMIT_RANGES[item.key]"` 还在，所以只要没配就会走到。
    """
    source = _read("frontend/src/views/SystemConfig.vue")
    assert "DELEGATION_LIMIT_RANGES[item.key]." not in source
    assert "getDelegationLimitRange(item).unit" in source
    assert "getDelegationLimitMin(item)" in source
    assert "getDelegationLimitMax(item)" in source
