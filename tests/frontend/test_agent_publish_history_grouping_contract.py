"""契约：发布历史按自然日分组 + 组头版本号 + 折叠记忆。

锁定三件事：
1. 抽屉按自然日分组，默认只展开最近一天，组头可点折叠、工具条可全部展开/收起；
2. 组头带「当天涉及的版本号」（升序去重、最多 3 个、无版本的日期不出现空位）；
3. 折叠状态按智能体记在 sessionStorage，折叠只隐内容不销毁 DOM（内容快照状态不丢）。

日期标签（今天/昨天/同年/跨年）、分组排序、`at` 缺失兜底与版本汇总都由 node 真跑纯函数覆盖——
这些边界靠 UI 手测太容易漏。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
DRAWER = ROOT / "frontend" / "src" / "components" / "agent" / "AgentSystemHistoryDrawer.vue"
HELPERS = ROOT / "frontend" / "src" / "utils" / "agentPublishHistory.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_drawer_groups_history_by_day_with_collapsible_date_headers():
    src = _read(DRAWER)
    helpers = _read(HELPERS)

    # 纯函数抽到 utils，组件只做渲染
    assert "from '../../utils/agentPublishHistory'" in src
    assert "groupEntriesByDay(entries.value)" in src
    assert "export const groupEntriesByDay = <T extends PublishHistoryGroupable>(" in helpers
    assert "export const formatPublishDayLabel = (date: Date, now: Date = new Date()): string" in helpers
    assert "export const collectVersionNumbers = <T extends PublishHistoryGroupable>(" in helpers

    # 组头整行可点折叠，且暴露 aria-expanded
    assert ':aria-expanded="!isCollapsed(group.key)"' in src
    assert '@click="toggleDay(group.key)"' in src
    assert "const toggleDay = (key: string) => {" in src

    # 每组一条独立时间线：v-show 而非 v-if，收起后快照展开状态不丢
    assert 'v-show="!isCollapsed(group.key)"' in src
    assert 'v-for="group in dayGroups"' in src

    # 工具条提供全部展开/收起
    assert '@click="setAllDaysCollapsed(false)"' in src
    assert '@click="setAllDaysCollapsed(true)"' in src
    assert "const setAllDaysCollapsed = (collapse: boolean) => {" in src


def test_date_header_carries_version_numbers_and_collapsed_hint():
    src = _read(DRAWER)
    helpers = _read(HELPERS)

    # 组头版本号来自纯函数汇总，模板不再自己算
    assert "versions: collectVersionNumbers(group.items)" in src
    assert 'v-for="version in group.versions.shown"' in src
    assert 'v-if="group.versions.more > 0"' in src
    assert "{{ group.label }}" in src
    assert "{{ group.weekday }}" in src

    # 只统计带版本号的条目：启用状态变更 / 变更权属人 / 分享类没有版本
    assert ".filter((value) => Number.isFinite(value) && value > 0)" in helpers
    assert "unique.slice(0, safeLimit)" in helpers

    # 折叠时补一句「最近一条是 X」，收起状态也能扫读
    assert 'v-if="isCollapsed(group.key)"' in src
    assert "最近 {{ eventTone(group.items[0]).label }}" in src


def test_default_only_latest_day_expanded_and_collapse_state_is_remembered():
    src = _read(DRAWER)

    # 默认只展开最近一天：历史最多 100 条，全展开仍要长滚动
    assert "const applyDefaultCollapse = () => {" in src
    assert "if (index > 0) next[group.key] = true" in src
    assert "applyDefaultCollapse()" in src

    # 折叠记忆按智能体维度存 sessionStorage，读写都容错（隐私模式下不报错）
    assert "COLLAPSED_STORAGE_PREFIX = 'nanzi:agent-publish-history:collapsed-days'" in src
    assert "sessionStorage.getItem(storageKey())" in src
    assert "sessionStorage.setItem(storageKey(), JSON.stringify(collapsedDays.value))" in src
    assert src.count("} catch {") >= 2

    # 只存 true：否则「全部展开」后再折叠会残留旧键导致失忆
    assert "if (value === true) restored[key] = true" in src
    assert "delete next[key]" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_publish_history_helpers_behaviour_via_node():
    script = """
import assert from 'node:assert/strict'
import {
  collectVersionNumbers,
  formatPublishDayLabel,
  groupEntriesByDay,
  publishDayKey,
} from './frontend/src/utils/agentPublishHistory.ts'

const WEEK = ['周日','周一','周二','周三','周四','周五','周六']
const now = new Date('2026-11-27T14:10:00')

// 1. 日期标签：今天 / 昨天 / 同年不带年 / 跨年必须带年
assert.equal(formatPublishDayLabel(new Date('2026-11-27T09:00:00'), now), '今天')
assert.equal(formatPublishDayLabel(new Date('2026-11-26T23:59:00'), now), '昨天')
assert.equal(formatPublishDayLabel(new Date('2026-11-24T16:00:00'), now), '11月24日')
assert.equal(formatPublishDayLabel(new Date('2025-12-30T10:00:00'), now), '2025年12月30日')

// 2. 分组：日期倒序、组内按时间倒序、at 缺失/非法落到末尾「时间未知」而不丢弃
const groups = groupEntriesByDay([
  { id: 'a', at: '2026-11-24T16:05:40', version_number: 3 },
  { id: 'b', at: '2026-11-27T13:57:18', version_number: 6 },
  { id: 'c', at: '2026-11-24T17:23:47', version_number: 5 },
  { id: 'd', at: null },
  { id: 'e', at: 'not-a-date' },
], now)
assert.deepEqual(groups.map((group) => group.key), ['2026-11-27', '2026-11-24', 'unknown'])
assert.deepEqual(groups[0].items.map((item) => item.id), ['b'])
assert.deepEqual(groups[1].items.map((item) => item.id), ['c', 'a'])
assert.deepEqual(groups[2].items.map((item) => item.id).sort(), ['d', 'e'])
assert.equal(groups[0].label, '今天')
assert.equal(groups[1].label, '11月24日')
assert.equal(groups[1].weekday, WEEK[new Date('2026-11-24T12:00:00').getDay()])
assert.equal(groups[2].label, '时间未知')
assert.equal(groups[2].weekday, '')

// 3. 自然日键取本地日期，不用 UTC（凌晨条目不能被挪到前一天）
assert.equal(publishDayKey(new Date('2026-01-01T00:30:00')), '2026-01-01')
assert.equal(publishDayKey(new Date('2026-12-31T23:30:00')), '2026-12-31')

// 4. 版本汇总：去重升序、最多 3 个、多余给 +N、无版本/非正数不出现
assert.deepEqual(
  collectVersionNumbers([{ version_number: 5 }, { version_number: 3 }, { version_number: 5 }, {}]),
  { shown: [3, 5], more: 0 },
)
assert.deepEqual(
  collectVersionNumbers([1, 2, 3, 4, 5].map((value) => ({ version_number: value }))),
  { shown: [1, 2, 3], more: 2 },
)
assert.deepEqual(collectVersionNumbers([{ version_number: null }, {}]), { shown: [], more: 0 })
assert.deepEqual(
  collectVersionNumbers([{ version_number: 0 }, { version_number: -1 }]),
  { shown: [], more: 0 },
)

console.log('AGENT_PUBLISH_HISTORY_NODE_OK')
"""
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "AGENT_PUBLISH_HISTORY_NODE_OK" in proc.stdout
