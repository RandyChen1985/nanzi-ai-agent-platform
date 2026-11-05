/**
 * 行为测试：行级权限「条件文本 → 规则构建器」的反向解析。
 *
 * 事故背景：数据权限弹窗里每条规则只落库一个 condition 字符串，下面那排
 * 「目标表 / 字段 / 操作符 / 过滤值」是临时 UI 状态（`_builder_*`），保存时被
 * cleanObj 清掉，所以重新打开配置构建器永远是空的。现在打开时会尝试解析回填。
 *
 * 这里锁住两件事：能无损解析的必须拆对；解析不了的必须返回 null——
 * 回填错了比不回填更糟（用户会以为配的就是那个条件）。
 */
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const ROOT = resolve('.');
const require = createRequire(resolve(ROOT, 'frontend/package.json'));
const { buildSync } = require('esbuild');

const bundled = buildSync({
  entryPoints: [resolve(ROOT, 'frontend/src/utils/rowFilterCondition.ts')],
  bundle: true,
  format: 'cjs',
  platform: 'node',
  target: 'node18',
  write: false,
});
const mod = { exports: {} };
new Function('module', 'exports', bundled.outputFiles[0].text)(mod, mod.exports);
const { parseRowFilterCondition } = mod.exports;

const failures = [];
const check = (name, condition, detail = '') => {
  if (!condition) failures.push(detail ? `${name} —— ${detail}` : name);
};

const eq = (name, actual, expected) => {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  check(name, a === e, `实际=${a} 期望=${e}`);
};

// 正常回填：截图里的真实条件
eq('四段式等值', parseRowFilterCondition('ai_agent_access_logs.id = {user.id}'), {
  table: 'ai_agent_access_logs',
  field: 'id',
  op: '=',
  value: '{user.id}',
});

eq('反引号包裹标识符', parseRowFilterCondition('`ai_agent_access_logs`.`id` = {user.id}'), {
  table: 'ai_agent_access_logs',
  field: 'id',
  op: '=',
  value: '{user.id}',
});

eq('带库名前缀取最后两段', parseRowFilterCondition('demo.t1.col <> 3'), {
  table: 't1',
  field: 'col',
  op: '!=',
  value: '3',
});

eq('LIKE 保留引号原样', parseRowFilterCondition("t1.org_path LIKE '{user.org_path}%'"), {
  table: 't1',
  field: 'org_path',
  op: 'LIKE',
  value: "'{user.org_path}%'",
});

eq('IN 列表', parseRowFilterCondition("t1.region_code IN ('SH','BJ')"), {
  table: 't1',
  field: 'region_code',
  op: 'IN',
  value: "('SH','BJ')",
});

eq('值里带空格', parseRowFilterCondition("t1.name = 'hello world'"), {
  table: 't1',
  field: 'name',
  op: '=',
  value: "'hello world'",
});

// 解析不了的一律 null：宁可留空让用户手填
eq('缺少表名', parseRowFilterCondition('dept_code = {user.dept_code}'), null);
eq('比较符不在下拉里', parseRowFilterCondition('t1.col >= 3'), null);
eq('AND 组合', parseRowFilterCondition('t1.a = 1 AND t1.b = 2'), null);
eq('OR 组合', parseRowFilterCondition('t1.a = 1 OR t1.b = 2'), null);
eq('函数值', parseRowFilterCondition('t1.a = lower({user.name})'), null);
eq('子查询', parseRowFilterCondition('t1.a = (select max(id) from t2)'), null);
eq('IN 子查询', parseRowFilterCondition('t1.a IN (select id from t2)'), null);
eq('注释尾巴', parseRowFilterCondition('t1.a = 1 -- 备注'), null);
eq('纯常量', parseRowFilterCondition('1=1'), null);
eq('空字符串', parseRowFilterCondition('   '), null);
eq('非字符串', parseRowFilterCondition(undefined), null);

if (failures.length > 0) {
  console.error(`✗ ${failures.length} 项失败：\n- ${failures.join('\n- ')}`);
  process.exit(1);
}

console.log('✓ 行级权限条件反向解析行为测试通过');
