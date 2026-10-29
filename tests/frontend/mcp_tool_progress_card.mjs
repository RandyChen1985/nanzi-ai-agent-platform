/**
 * 契约测试：MCP 工具卡必须能显示「远端进度」。
 *
 * 背景：长跑的 MCP 工具（检索、多轮 agent_invoke）以前只有一个转圈点，用户无法
 * 判断它是在推进还是卡死；`agent_max_toolcall_timeout` 到点才变成超时卡片。
 *
 * 这里解析模板 AST 而不是搜源码字符串：`role="progressbar"` 出现两次不等于真的
 * 按 total 分支渲染，必须逐个断言绑定表达式、无 total 的不确定态、以及文本组合。
 */
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';

const require = createRequire(resolve('frontend/package.json'));
const { parse } = require('@vue/compiler-sfc');
const { baseParse } = require('@vue/compiler-dom');

const SFC = 'frontend/src/components/chat/ChatExecutionTimeline.vue';
const source = readFileSync(SFC, 'utf8');
const { descriptor } = parse(source, { filename: SFC });
const script = descriptor.scriptSetup?.content || '';
const template = descriptor.template.content;
const ast = baseParse(template);

const failures = [];
const check = (name, condition, detail = '') => {
  if (!condition) failures.push(detail ? `${name} —— ${detail}` : name);
};

const nodes = [];
const walk = (node) => {
  nodes.push(node);
  (node.children || []).forEach(walk);
};
walk(ast);

const staticAttr = (node, name) =>
  (node.props || []).find((p) => p.type === 6 && p.name === name)?.value?.content ?? '';
const directive = (node, name) =>
  (node.props || []).find((p) => p.type === 7 && p.name === name)?.exp?.content ?? '';
/** `:attr="expr"` 的表达式（baseParse 把它放在 arg/exp 上，不是 value） */
const boundAttr = (node, arg) =>
  (node.props || [])
    .find((p) => p.type === 7 && p.name === 'bind' && p.arg?.content === arg)?.exp?.content ?? '';

// --------------------------------------------------------------------------- //
// 1. 进度条存在，并且是真正的无障碍进度条
// --------------------------------------------------------------------------- //
const bar = nodes.find((node) => staticAttr(node, 'role') === 'progressbar');
check('工具卡内存在 role=progressbar 的进度条', Boolean(bar));
if (bar) {
  const valuenow = boundAttr(bar, 'aria-valuenow');
  check('进度条用 aria-valuenow 暴露百分比', valuenow.includes('toolProgressPercent'), valuenow);
  const barClass = boundAttr(bar, 'class');
  check(
    '无 total 时切成不确定态（呼吸条），不能假装 0%',
    barClass.includes('timeline-progress-indeterminate') && barClass.includes('toolProgressPercent(item) === null'),
    barClass,
  );
}

// 进度条只在「还没有结束」时出现：完成后由耗时/结果接管，避免残影
const barContainer = nodes.find((node) =>
  (node.children || []).some((child) => staticAttr(child, 'role') === 'progressbar'),
);
check('进度条外层有 v-if 守卫', Boolean(barContainer && directive(barContainer, 'if')), String(Boolean(barContainer)));
if (barContainer) {
  const guard = directive(barContainer, 'if');
  check("进度条仅在 pending 时渲染", guard.includes("pending"), guard);
  check('进度条绑定 item.progress', guard.includes('progress'), guard);
}

// --------------------------------------------------------------------------- //
// 2. 文本：有 total 显示 a/b + 远端文案，没有 total 只显示文案
// --------------------------------------------------------------------------- //
check(
  '进度文本由 toolProgressText 统一拼装',
  template.includes('toolProgressText(item)'),
);
const fnText = script.split('function toolProgressText')[1]?.split('\nfunction ')[0] || '';
check('toolProgressText 使用 item.progressMessage', fnText.includes('progressMessage'), fnText.slice(0, 400));
check('toolProgressText 在无 percent 时不拼 a/b', fnText.includes('percent === null'), fnText.slice(0, 400));

const fnPercent = script.split('function toolProgressPercent')[1]?.split('\nfunction ')[0] || '';
check('toolProgressPercent 用 progress/total 计算', fnPercent.includes('total') && fnPercent.includes('progress'), fnPercent.slice(0, 300));
check('toolProgressPercent 对 total<=0 返回 null', fnPercent.includes('total <= 0'), fnPercent.slice(0, 300));
check('toolProgressPercent 结果夹在 0..100', fnPercent.includes('Math.min') && fnPercent.includes('Math.max'), fnPercent.slice(0, 400));

const fnWidth = script.split('function toolProgressWidth')[1]?.split('\nfunction ')[0] || '';
check('不确定态给一个固定宽度用于呼吸动画', fnWidth.includes('14%'), fnWidth.slice(0, 300));

// --------------------------------------------------------------------------- //
// 3. 呼吸动画必须真的在样式里（否则不确定态就是一条静止的 14% 横条）
// --------------------------------------------------------------------------- //
const style = descriptor.styles.map((block) => block.content).join('\n');
check('样式里定义了不确定态动画', style.includes('timeline-progress-indeterminate'), 'styles');
check('动画用 keyframes 而不是 transition', style.includes('@keyframes timeline-progress'), 'styles');

// --------------------------------------------------------------------------- //
// 4. 数据链路：SSE 帧 → timeline 条目字段
// --------------------------------------------------------------------------- //
const handlers = readFileSync('frontend/src/utils/agentscopeSseHandlers.ts', 'utf8');
check('SSE 分发处理 tool_progress', handlers.includes('case "tool_progress"'), 'handlers');

const timeline = readFileSync('frontend/src/utils/processTimeline.ts', 'utf8');
const logItemType = timeline.split('export type ProcessTimelineLogItem')[1]?.split('};')[0] || '';
for (const field of ['progress', 'total', 'progressMessage']) {
  check(`ProcessTimelineLogItem 声明 ${field}`, logItemType.includes(field), logItemType.slice(0, 200));
}
check(
  '进度走 applyTimelineLogProgress（只更新已有卡片，绝不新建）',
  timeline.includes('export function applyTimelineLogProgress'),
);
const progressFn = timeline.split('export function applyTimelineLogProgress')[1]?.split('\n}\n')[0] || '';
check('找不到卡片时直接丢弃', progressFn.includes('if (!item) return false'), progressFn.slice(0, 400));
for (const field of ['progress', 'total', 'progressMessage']) {
  check(`applyTimelineLogProgress 写入 ${field}`, progressFn.includes(`item.${field}`), progressFn.slice(0, 600));
}
const upsert = timeline.split('export function upsertTimelineLog')[1]?.split('\n}\n')[0] || '';
check(
  '条目结束时清掉进度字段（否则会被快照落库）',
  upsert.includes('existing.progress = undefined') && upsert.includes('data.status !== "pending"'),
  upsert.slice(0, 1500),
);

if (failures.length) {
  console.error('FAIL: MCP 工具卡进度展示');
  failures.forEach((item) => console.error(` - ${item}`));
  process.exit(1);
}
console.log('OK: MCP 工具卡进度展示');
