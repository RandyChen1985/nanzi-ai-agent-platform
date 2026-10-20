/**
 * 行为测试：MCP 服务列表「添加」入口必须按创建权限收，而不是点到底才 403。
 *
 * 事故背景：平台 MCP 区那颗分裂按钮的容器条件是 `canCreateMcp || canEditMcp`，
 * 但左侧主按钮「添加」没有 v-if。于是只有 edit 码的角色（内置 Echo、改服务配置）
 * 也能打开新建向导，一路填到「确认并完成添加」，最后由 POST /servers 返回 403。
 *
 * 为什么不用「源码里搜得到 v-if="canCreateMcp"」这种断言：下拉项里就有这个字符串，
 * 主按钮没绑也照样通过——必须落到按钮节点本身，所以这里解析模板 AST 逐个节点断言。
 */
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';

const require = createRequire(resolve('frontend/package.json'));
const { parse } = require('@vue/compiler-sfc');
const { baseParse } = require('@vue/compiler-dom');

const SFC = 'frontend/src/components/system/McpServerRegistry.vue';
const { descriptor } = parse(readFileSync(SFC, 'utf8'), { filename: SFC });
const ast = baseParse(descriptor.template.content);

const failures = [];
const check = (name, condition, detail = '') => {
  if (!condition) failures.push(detail ? `${name} —— ${detail}` : name);
};

/** 节点上的静态属性名（type 6）。 */
const attrNames = (node) => (node.props || []).filter((p) => p.type === 6).map((p) => p.name);
/** 节点上的指令表达式（type 7），返回 { 指令名: 表达式 }。 */
const directives = (node) => Object.fromEntries(
  (node.props || [])
    .filter((p) => p.type === 7)
    .map((p) => [p.arg ? `${p.name}:${p.arg.content}` : p.name, p.exp?.content ?? '']),
);
const textOf = (node) => (node.children || [])
  .map((child) => [child, ...(child.children || [])]
    .map((item) => (typeof item.content === 'string' ? item.content : item.content?.content || ''))
    .join(''))
  .join('')
  .trim();

const findByDirective = (node, name, includes) => {
  if (!node) return null;
  if (node.type === 1) {
    const directivesMap = directives(node);
    if (directivesMap[name] && (!includes || directivesMap[name].includes(includes))) return node;
  }
  for (const child of node.children || []) {
    const hit = findByDirective(child, name, includes);
    if (hit) return hit;
  }
  return null;
};

// ---------------------------------------------------------------------------
// 1. 分裂按钮容器：个人区不得落进这个分支（Echo 只支持平台区，点了会静默 return）
// ---------------------------------------------------------------------------
const container = findByDirective(ast, 'else-if', 'canCreateMcp || canEditMcp');
check('找到分裂按钮容器', Boolean(container));
if (container) {
  const condition = directives(container)['else-if'];
  check(
    '容器条件排除了个人区',
    condition.includes("props.scope !== 'personal'"),
    condition,
  );

  // -------------------------------------------------------------------------
  // 2. 左侧主按钮「添加」必须绑定 canCreateMcp
  // -------------------------------------------------------------------------
  const mainButton = (container.children || [])
    .filter((child) => child.type === 1 && child.tag === 'button')[0];
  check('找到主按钮', Boolean(mainButton));
  if (mainButton) {
    const buttonDirectives = directives(mainButton);
    check(
      '主按钮按 canCreateMcp 隐藏',
      buttonDirectives.if === 'canCreateMcp',
      JSON.stringify(buttonDirectives),
    );
    // 隐藏后圆角要跟着变，否则「只有一个按钮」时左右圆角对不上
    check(
      '主按钮圆角随编辑权限变化',
      String(buttonDirectives['bind:class'] || '').includes('canEditMcp'),
      String(buttonDirectives['bind:class'] || ''),
    );
  }

  // -------------------------------------------------------------------------
  // 3. 右侧箭头只给 canEditMcp（否则是空下拉）
  // -------------------------------------------------------------------------
  const arrowButton = (container.children || [])
    .filter((child) => child.type === 1 && child.tag === 'button')[1];
  check('找到箭头按钮', Boolean(arrowButton));
  if (arrowButton) {
    const buttonDirectives = directives(arrowButton);
    check(
      '箭头按钮按 canEditMcp 隐藏',
      buttonDirectives.if === 'canEditMcp',
      JSON.stringify(buttonDirectives),
    );
  }
}

// ---------------------------------------------------------------------------
// 4. 向导提交按钮：无创建权限时必须 disabled（编辑仍可保存）
// ---------------------------------------------------------------------------
let submitButton = null;
const walk = (node) => {
  if (node?.type === 1 && node.tag === 'button' && textOf(node).includes('确认并完成添加')) {
    submitButton = node;
  }
  (node?.children || []).forEach(walk);
};
walk(ast);
check('找到「确认并完成添加」按钮', Boolean(submitButton));
if (submitButton) {
  const disabled = directives(submitButton)['bind:disabled'] || '';
  check(
    '提交按钮按 canCreateMcp 禁用（编辑不受影响）',
    disabled.includes('canCreateMcp') && disabled.includes('isEditing'),
    disabled,
  );
  check(
    '提交按钮禁用后不可再点击的样式',
    attrNames(submitButton).includes('class'),
    JSON.stringify(attrNames(submitButton)),
  );
}

// ---------------------------------------------------------------------------
// 5. 页面级引导入口走 openAddModal，函数里必须自己兜权限（按钮不是唯一入口）
// ---------------------------------------------------------------------------
const source = readFileSync(SFC, 'utf8');
check(
  'openAddModal 内部有 canCreateMcp 守卫',
  /const openAddModal[\s\S]{0,320}if \(!canCreateMcp\.value\)/.test(source),
);
check(
  'addServer 提交前有 canCreateMcp 守卫',
  /const addServer[\s\S]{0,320}if \(!isEditing\.value && !canCreateMcp\.value\)/.test(source),
);

if (failures.length) {
  console.error('mcp_split_button_permissions 行为测试失败：');
  for (const failure of failures) console.error(` - ${failure}`);
  process.exit(1);
}
console.log('mcp_split_button_permissions 行为测试通过（添加/箭头/提交三处均按权限码收敛）');
