/**
 * 行为测试：typographer 不得改写 `+-`（设备名「2000+-」曾被渲染成「2000±」）。
 *
 * 背景（用户实测）：AI 回答里的设备名 `2000+-` 在 EmbedChat 界面显示成 `2000±`。
 * 根因是 markdown-it 开了 `typographer: true`，内置 `replacements` 规则会把
 * `+-` 替换成 `±`（同批还有 `--`→`–`、`...`→`…` 等）。设备名/型号/编号被改，
 * 属于内容损坏。
 *
 * 断言的是「用户能看到的结果」：`+-` 一字不动，其余排版增强照旧生效。
 */
import { createRequire } from 'node:module';
import process from 'node:process';

import { registerTypographerWithoutPlusMinus } from '../../frontend/src/utils/markdownTypographer.ts';

const require = createRequire(new URL('../../frontend/', import.meta.url));
const MarkdownIt = require('markdown-it');

const failures = [];
const check = (name, condition, detail = '') => {
  if (!condition) failures.push(detail ? `${name} —— ${detail}` : name);
};

/** 与 `frontend/src/utils/markdown.ts` 中的实例配置保持一致（除 highlight/css 外）。 */
const createMd = () => {
  const md = new MarkdownIt({ html: true, linkify: true, typographer: true });
  registerTypographerWithoutPlusMinus(md);
  return md;
};

const md = createMd();
const render = (src) => md.render(src);
const renderInline = (src) => md.renderInline(src);
const textOf = (html) => html.replace(/<[^>]*>/g, '').trim();

// ---------------------------------------------------------------------------
// 1. 用户实际踩到的场景：设备名以 `+-` 结尾
// ---------------------------------------------------------------------------
check('设备名 2000+- 保持原样', textOf(render('2000+-')) === '2000+-', render('2000+-'));
check('设备名里不得出现 ±', !render('2000+-').includes('±'), render('2000+-'));

const sentence = '设备 2000+- 已下线，请检查。';
check('句中设备名保持原样', textOf(render(sentence)) === sentence, render(sentence));

// ---------------------------------------------------------------------------
// 2. 同一根因的其它承载位置：表格单元格、列表项、加粗、内联 HTML
// ---------------------------------------------------------------------------
const table = '| 设备 | 状态 |\n|---|---|\n| 2000+- | 离线 |';
const tableHtml = render(table);
check('表格单元格里的 2000+- 保持原样', tableHtml.includes('2000+-'), tableHtml);
check('表格里不得出现 ±', !tableHtml.includes('±'), tableHtml);

const listItem = render('- 设备 2000+-');
check('列表项里的 2000+- 保持原样', listItem.includes('2000+-'), listItem);

const bold = renderInline('**2000+-**');
check('加粗里的 2000+- 保持原样', textOf(bold) === '2000+-', bold);

const inlineHtml = renderInline('<span>2000+-</span>');
check('内联 HTML 文本里的 2000+- 保持原样', textOf(inlineHtml) === '2000+-', inlineHtml);

// ---------------------------------------------------------------------------
// 3. 连写 / 句首 / 与其它符号相邻（原先会被吃成 ±±）
// ---------------------------------------------------------------------------
check('连写 +-+- 保持原样', textOf(renderInline('+-+-')) === '+-+-', renderInline('+-+-'));
check('句首 +- 保持原样', textOf(renderInline('+- 表示增减')) === '+- 表示增减', renderInline('+- 表示增减'));
check('正负号习惯写法 25 +- 1 保持原样', textOf(renderInline('25 +- 1')) === '25 +- 1', renderInline('25 +- 1'));

// ---------------------------------------------------------------------------
// 4. 代码里的 `+-` 本来就原样（不能被这次改动影响）
// ---------------------------------------------------------------------------
check('行内代码保持原样', renderInline('`2000+-`').includes('<code>2000+-</code>'), renderInline('`2000+-`'));
check('围栏代码块保持原样', render('```\n2000+-\n```').includes('2000+-'), render('```\n2000+-\n```'));

// ---------------------------------------------------------------------------
// 5. 其它排版增强必须还在（不能为了修 `+-` 把 typographer 整体砍掉）
// ---------------------------------------------------------------------------
check('en dash 仍生效', textOf(renderInline('a -- b')) === 'a \u2013 b', renderInline('a -- b'));
check('em dash 仍生效', textOf(renderInline('x---y')) === 'x\u2014y', renderInline('x---y'));
check('省略号仍生效', textOf(renderInline('等等...')) === '等等…', renderInline('等等...'));
check('(c) 仍生效', textOf(renderInline('(c)')) === '©', renderInline('(c)'));
check('(tm) 仍生效', textOf(renderInline('(tm)')) === '™', renderInline('(tm)'));
check('弯引号仍生效', textOf(renderInline('"hi"')) === '“hi”', renderInline('"hi"'));
check('命令行的 --verbose 不被改写', textOf(renderInline('npm run build --verbose')) === 'npm run build --verbose', renderInline('npm run build --verbose'));

// ---------------------------------------------------------------------------
// 6. 必须确认「没接线」时测试会红：对照组用原生 markdown-it
// ---------------------------------------------------------------------------
const native = new MarkdownIt({ html: true, linkify: true, typographer: true });
check(
  '对照组：原生 markdown-it 仍会把 +- 改成 ±',
  textOf(native.render('2000+-')).includes('±'),
  native.render('2000+-'),
);

if (failures.length) {
  console.error('markdown_plus_minus 行为测试失败：');
  for (const failure of failures) console.error(` - ${failure}`);
  process.exit(1);
}
console.log('markdown_plus_minus 行为测试通过（+- 保持原样，其余 typographer 增强不变）');
