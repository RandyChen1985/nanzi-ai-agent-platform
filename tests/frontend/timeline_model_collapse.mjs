/**
 * 行为测试：模型调用（category=model）默认折叠成一行汇总，不再逐条占主时间线。
 *
 * 背景（用户截图）：一次问答里 11 次模型调用被 push 到时间线数组末尾、12 个工具
 * 却都挂在旁白 children 下，于是界面呈现成「工具全在上、模型调用全在下」，正在跑的
 * Bash 被压在最底下——看着像时间倒挂。修法是渲染层过滤：模型调用收敛为底部一行
 * 「N 次模型调用 · 合计 Xs」，明细走「查看明细」。
 *
 * 这里直接 `node --experimental-strip-types` 加载真实 `processTimeline.ts`（不复制逻辑），
 * 断言用户能看到的取舍：完成的模型调用消失、进行中的那条保留、fallback 提示必须留下。
 */
import process from 'node:process';

import {
  collectModelCallLogs,
  isModelFallbackNotice,
  stripModelCallLogs,
  summarizeModelCalls,
} from '../../frontend/src/utils/processTimeline.ts';

const failures = [];
const check = (name, condition, detail = '') => {
  if (!condition) failures.push(detail ? `${name} —— ${detail}` : name);
};

const tool = (id, dur) => ({
  kind: 'log',
  id,
  title: '工具完成: Bash',
  details: '',
  status: 'success',
  category: 'tool',
  execution_time_ms: dur,
  children: [],
});
const model = (id, dur, status = 'success') => ({
  kind: 'log',
  id,
  title: '模型调用: DeepSeek-V4-Flash',
  details: '',
  status,
  category: 'model',
  execution_time_ms: dur,
  children: [],
});
const narration = (id, children = []) => ({
  kind: 'text',
  id,
  textKind: 'narration',
  content: '我先实际拉取该仓库的真实结构与元数据，再基于结果分析。',
  pending: false,
  children,
});

const buildItems = () => [
  { kind: 'text', id: 'reasoning', textKind: 'reasoning', content: '思考', pending: false },
  narration('n1', [tool('t1', 3700), tool('t2', 31700)]),
  model('m1', 8500),
  model('m2', 1400),
  model('m3', null, 'pending'),
  { ...model('m4', 0), title: '⚠️ 已切换 fallback 模型', status: 'warning' },
];

/* ---- 1. 完成的模型调用被移除，工具不被误伤 ---- */
const items = buildItems();
const stripped = stripModelCallLogs(items);

check('完成的模型调用不再出现在主时间线', !stripped.some((it) => it.id === 'm1' || it.id === 'm2'));
check('思考与旁白保留', stripped.some((it) => it.id === 'reasoning') && stripped.some((it) => it.id === 'n1'));
check(
  '旁白下的工具调用一个不少',
  stripped.find((it) => it.id === 'n1')?.children?.length === 2,
  `实际 ${stripped.find((it) => it.id === 'n1')?.children?.length}`,
);

/* ---- 2. 进行中的模型调用必须留着，否则界面上看起来卡死 ---- */
check('进行中的模型调用保留一行', stripped.some((it) => it.id === 'm3'));

/* ---- 3. fallback 换模提示是给用户看的事件，不能被折叠 ---- */
check('fallback 提示不被折叠', stripped.some((it) => it.id === 'm4'));
check('fallback 识别不看 category，只看 warning/标题', isModelFallbackNotice({ ...model('x', 0), status: 'warning' }));
check('普通模型调用不算 fallback', !isModelFallbackNotice(model('y', 10)));

/* ---- 4. 不改动入参（同一个数组会被快照复用） ---- */
check('原数组未被就地修改', items.some((it) => it.id === 'm1'));

/* ---- 5. 汇总口径：次数 / 合计耗时 / 进行中 ---- */
const summary = summarizeModelCalls(items);
check('汇总次数为 4（含 fallback 与进行中）', summary.count === 4, `实际 ${summary.count}`);
check('汇总耗时为 8.5s+1.4s', summary.totalMs === 9900, `实际 ${summary.totalMs}`);
check('汇总标记进行中', summary.pending === true);

/* ---- 6. 嵌套在 children 里的模型调用同样被收集/移除 ---- */
const nested = [
  narration('n2', [tool('t9', 1200), model('m9', 2200)]),
  { kind: 'text', id: 'n3', textKind: 'narration', content: '继续', pending: false, children: [model('m10', 800)] },
];
check('嵌套模型调用被收集到', collectModelCallLogs(nested).length === 2);
check('嵌套模型调用被移除', stripModelCallLogs(nested).every((it) => (it.children || []).every((c) => c.category !== 'model')));

/* ---- 7. 显式 keepPending=false 时才允许连进行中一起收 ---- */
check(
  'keepPending=false 收起进行中',
  !stripModelCallLogs(buildItems(), { keepPending: false }).some((it) => it.id === 'm3'),
);

/* ---- 8. 没有模型调用的时间线原样返回 ---- */
const plain = [narration('n4', [tool('t10', 500)])];
check('无模型调用时结构不变', stripModelCallLogs(plain).length === 1 && summarizeModelCalls(plain).count === 0);

if (failures.length) {
  console.error(`行为测试失败 ${failures.length} 项：`);
  for (const failure of failures) console.error(` - ${failure}`);
  process.exit(1);
}

console.log('行为测试通过：模型调用折叠（完成的收起 / 进行中保留 / fallback 保留 / 汇总口径正确）');
