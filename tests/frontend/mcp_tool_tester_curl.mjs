/**
 * 工具测试台的 curl 分页：用户要的是「复制一条能跑的请求」，不是一堆说明文字。
 *
 * 这里守住四件事：
 * 1. 分页真的存在，并且是 tablist 里的一个 tab（无障碍与键盘可用）；
 * 2. 代码块可复制，且展示的是后端生成的片段（含 initialize + tools/call 两步）；
 * 3. 片段不缓存：参数改过之后重新进来必须是新命令；
 * 4. 生成失败要把原因显示出来，而不是留一块空白。
 */
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';

const require = createRequire(resolve('frontend/package.json'));
const { parse } = require('@vue/compiler-dom');

const source = readFileSync('frontend/src/components/system/McpToolTester.vue', 'utf8');

const problems = [];
const check = (label, ok, detail = '') => {
  if (!ok) {
    problems.push(`${label}${detail ? ` —— ${detail}` : ''}`);
  }
};

const template = source.split('<template>')[1].split('</template>')[0];
const script = source.split('<script setup lang="ts">')[1].split('</script>')[0];

const ast = parse(template);
const nodes = [];
const walk = (node) => {
  nodes.push(node);
  for (const child of node.children || []) walk(child);
};
walk(ast);

const staticAttr = (node, name) =>
  (node.props || []).find((p) => p.type === 6 && p.name === name)?.value?.content ?? '';
/** `@click="expr"` */
const onEvent = (node, name) =>
  (node.props || []).find((p) => p.type === 7 && p.name === 'on' && p.arg?.content === name)?.exp
    ?.content ?? '';
/** 收集节点下的文本与插值表达式（`{{ x }}` 在 AST 里是 type 5 而非文本）。 */
const textOf = (node) => {
  let out = '';
  const collect = (current) => {
    if (current.type === 2) out += current.content;
    else if (current.type === 5) out += current.content?.content ?? '';
    for (const child of current.children || []) collect(child);
  };
  collect(node);
  return out.trim();
};

// --------------------------------------------------------------------------- //
// 1. 分页存在
// --------------------------------------------------------------------------- //
const tabs = nodes.filter((node) => staticAttr(node, 'role') === 'tab');
const tabLabels = tabs.map(textOf);
check('测试台有 curl 分页', tabLabels.some((label) => /curl/i.test(label)), tabLabels.join(' | '));

const curlTab = tabs.find((node) => /curl/i.test(textOf(node)));
if (curlTab) {
  check(
    'curl 分页是 tablist 里的真 tab（带 aria-selected）',
    staticAttr(curlTab, 'aria-selected').includes('activeTab') ||
      (curlTab.props || []).some((p) => p.name === 'bind' && p.arg?.content === 'aria-selected'),
    JSON.stringify(curlTab.props?.map((p) => p.name)),
  );
  check('点击 curl 分页会切换 activeTab', onEvent(curlTab, 'click').includes("'curl'"), onEvent(curlTab, 'click'));
}

// --------------------------------------------------------------------------- //
// 2. 代码块 + 复制
// --------------------------------------------------------------------------- //
const curlBlock = nodes.find((node) => {
  const text = textOf(node);
  return text.includes('curlSnippet.curl') || text.includes('curlSnippet?.curl');
});
check('curl 片段渲染在代码块里', Boolean(curlBlock));

const copyButton = nodes.find((node) => onEvent(node, 'click') === 'handleCopyCurl');
check('curl 片段有复制按钮', Boolean(copyButton));
check(
  '复制按钮有无障碍标签',
  Boolean(staticAttr(copyButton, 'aria-label')) || Boolean(staticAttr(copyButton, 'title')),
);

// --------------------------------------------------------------------------- //
// 3. 生成逻辑
// --------------------------------------------------------------------------- //
check('切到 curl 分页时拉取片段', /watch\(\s*activeTab/.test(script) && script.includes("tab === 'curl'"));
check(
  '每次进入都重新拉取（不缓存过期命令）',
  !/if \(tab === 'curl' && !curlSnippet\.value\)/.test(script),
);
check('请求后端 curl 接口', script.includes('/curl`'), '后端路径缺失');
check('带上已填参数', script.includes('curlArguments'), '参数没有传给后端');
check('生成失败要展示原因', template.includes('curlError'), '缺少错误提示');

// --------------------------------------------------------------------------- //
// 4. 状态复位
// --------------------------------------------------------------------------- //
check(
  '切换工具时清掉上一份片段',
  /curlSnippet\.value = null/.test(script),
  '换工具后可能看到上一个工具的命令',
);

if (problems.length) {
  console.error('FAIL: 工具测试台 curl 分页');
  for (const problem of problems) console.error(` - ${problem}`);
  process.exit(1);
}
console.log('OK: 工具测试台 curl 分页');
