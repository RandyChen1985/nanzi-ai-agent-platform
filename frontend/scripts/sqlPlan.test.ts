import assert from 'node:assert/strict';
import {
  dedupeSqlPlanPayload,
  isSqlPlanPlaceholder,
  parseSqlPlan,
} from '../src/utils/sqlPlan.ts';

const sample = JSON.stringify({
  goal: '查询所有机房的列表信息，包括名称、位置、状态等',
  tables: ['未找到机房相关表'],
  fields: ['未找到'],
  metrics: ['未找到'],
  filters: ['无'],
  time_range: '无',
  grain: '明细',
  joins: ['无'],
  risk_notes: ['当前平台数据集中无机房相关表，无法执行查询'],
});

const parsed = parseSqlPlan(sample);
assert.equal(parsed.ok, true);
if (parsed.ok) {
  assert.equal(parsed.data.goal?.includes('机房'), true);
  assert.equal(parsed.data.tables?.[0], '未找到机房相关表');
}

assert.equal(isSqlPlanPlaceholder('无'), true);
assert.equal(isSqlPlanPlaceholder('未找到机房相关表'), true);
assert.equal(isSqlPlanPlaceholder('datacenter'), false);

const deduped = dedupeSqlPlanPayload(`{ "goal": "机房" }`);
assert.equal(deduped, '{ "goal": "机房" }');

const sqlPlanRegex = /(?:<sql_plan>([\s\S]*?)<\/sql_plan>)/gi;
const content = `<sql_plan>${sample}</sql_plan>\n\n<sql_plan>${sample}</sql_plan>`;
const matches = [...content.matchAll(sqlPlanRegex)];
assert.equal(matches.length, 2);
assert.equal(dedupeSqlPlanPayload(matches[0]![1]), dedupeSqlPlanPayload(matches[1]![1]));

console.log('sqlPlan tests passed');
