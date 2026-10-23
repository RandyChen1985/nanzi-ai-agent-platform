/**
 * 行为测试：MCP 服务卡片的发布状态必须能一眼区分「0 已发布」和「全部已发布」。
 *
 * 事故背景：列表卡片把 `{{ published_tool_count }} 已发布` 一律写成 text-green-600，
 * 于是 `8 工具 / 0 已发布` 和 `8 工具 / 8 已发布` 长得一样。绿色在界面里等于「正常」，
 * 用户会当成配置完成——但 0 发布意味着智能体在版本配置里连一个工具都勾不到。
 *
 * 为什么不用「源码里搜得到『未发布』」这种断言：tooltip 文案里也有这个词，搜得到
 * 不代表徽章真的按状态分支渲染。这里解析模板 AST，逐个节点断言 v-if 链与配色。
 */
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';

const require = createRequire(resolve('frontend/package.json'));
const { parse } = require('@vue/compiler-sfc');
const { baseParse } = require('@vue/compiler-dom');

const SFC = 'frontend/src/components/system/McpServerRegistry.vue';
const source = readFileSync(SFC, 'utf8');
const { descriptor } = parse(source, { filename: SFC });
const script = descriptor.scriptSetup?.content || '';
const ast = baseParse(descriptor.template.content);

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
  (node.props || []).find(
    (p) => p.type === 7 && (p.name === name || (p.arg && `${p.name}:${p.arg.content}` === name)),
  )?.exp?.content ?? '';
const hasClickStop = (node) =>
  (node.props || []).some(
    (p) =>
      p.type === 7 &&
      p.name === 'on' &&
      p.arg?.content === 'click' &&
      (p.modifiers || []).some((m) => m.content === 'stop'),
  );
const hasDirective = (node, name) =>
  (node.props || []).some((p) => p.type === 7 && p.name === name);
const textOf = (node) => {
  if (node.type === 2) return node.content || '';
  if (node.type === 5) return node.content?.content || '';
  return (node.children || []).map(textOf).join('');
};
const elements = nodes.filter((n) => n.type === 1);

// ── 1. 0 发布：琥珀徽章 + 说清后果，不能带绿色 ─────────────────────────────
const noneBadge = elements.find((n) =>
  directive(n, 'else-if').includes("serverPublishState(server) === 'none'"),
);
check('0 发布徽章存在', Boolean(noneBadge));
if (noneBadge) {
  const cls = staticAttr(noneBadge, 'class');
  check('0 发布徽章是琥珀色', cls.includes('text-amber-700'), cls);
  check('0 发布徽章不是绿色', !cls.includes('text-green-600'), cls);
  check('0 发布徽章点破后果', textOf(noneBadge).includes('未发布'), textOf(noneBadge));
  check(
    '0 发布徽章说明智能体不可见',
    textOf(noneBadge).includes('智能体不可见'),
    textOf(noneBadge),
  );
  check('0 发布徽章有 tooltip', Boolean(directive(noneBadge, 'tooltip')));
}

// ── 2. 部分发布：琥珀 + 分数，避免「有已发布」被当成全部可用 ────────────────
const partialBadge = elements.find((n) =>
  directive(n, 'else-if').includes("serverPublishState(server) === 'partial'"),
);
check('部分发布徽章存在', Boolean(partialBadge));
if (partialBadge) {
  const cls = staticAttr(partialBadge, 'class');
  check('部分发布徽章是琥珀色', cls.includes('text-amber-700'), cls);
  check('部分发布显示分数', textOf(partialBadge).includes('已发布'), textOf(partialBadge));
  check(
    '部分发布分数是「已发布 / 总数」',
    textOf(partialBadge).includes('published_tool_count') && textOf(partialBadge).includes('tool_count'),
    textOf(partialBadge),
  );
  check(
    '部分发布 tooltip 说明剩余数量',
    directive(partialBadge, 'tooltip').includes('unpublishedToolCount(server)'),
    directive(partialBadge, 'tooltip'),
  );
}

// ── 3. 全部发布：绿色只留给这个分支 ────────────────────────────────────────
const fullBadge = elements.find(
  (n) => hasDirective(n, 'else') && staticAttr(n, 'class').includes('text-green-600'),
);
check('全量发布徽章保留绿色', Boolean(fullBadge));
if (fullBadge) {
  check(
    '绿色徽章只报已发布数',
    textOf(fullBadge).includes('published_tool_count'),
    textOf(fullBadge),
  );
}

// 旧的「绿字一律」写法必须消失：它会让 0 发布也变绿
check(
  '不再用 enabled_status 直接决定绿色',
  !elements.some(
    (n) =>
      directive(n, 'if').trim() === 'server.enabled_status === 1' &&
      staticAttr(n, 'class').includes('font-bold text-green-600'),
  ),
);
check(
  '旧「工具 / 0 已发布」拼接已拆开',
  !descriptor.template.content.includes('{{ server.tool_count }} 工具 /'),
);

// ── 4. 提示行：不只提示，给一个直达动作 ────────────────────────────────────
const hint = elements.find((n) => {
  const cond = directive(n, 'if');
  return cond.includes("=== 'none'") && cond.includes("=== 'partial'") && textOf(n).includes('工具已同步');
});
check('0 发布卡片有说明行', Boolean(hint));
if (hint) {
  check(
    '说明行只在未发布/部分发布出现',
    directive(hint, 'if').includes("=== 'none'") && directive(hint, 'if').includes("=== 'partial'"),
    directive(hint, 'if'),
  );
}

const goPublish = elements.find(
  (n) => n.tag === 'button' && directive(n, 'on:click').includes('goPublishServerTools'),
);
check('存在「去发布」按钮', Boolean(goPublish));
if (goPublish) {
  check('去发布阻止冒泡', hasClickStop(goPublish));
  check('去发布受发布权限约束', directive(goPublish, 'if').includes('canPublishMcp'), directive(goPublish, 'if'));
  check('去发布文案可识别', textOf(goPublish).includes('去发布'), textOf(goPublish));
}

// ── 5. 脚本侧：状态机优先级 & 不替用户点发布 ───────────────────────────────
check('存在发布状态机', script.includes('const serverPublishState'));
const order = ['disabled', 'empty', 'none', 'partial', 'full'].map((state) =>
  script.indexOf(`return '${state}'`),
);
check('状态机覆盖五种状态', order.every((index) => index > 0));
check(
  '禁用优先于发布数',
  order.every((index, i) => i === 0 || index > order[i - 1]),
  JSON.stringify(order),
);
const goBody = script.split('const goPublishServerTools')[1]?.split('const clearSelectedServer')[0] || '';
check('去发布只跳转不批量发布', !goBody.includes('publishAllCurrentServerTools'));
check('去发布会滚动到工具面板', goBody.includes('scrollIntoView'));

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}
console.log('OK: MCP 服务卡片发布状态分级与「去发布」动作符合预期');
