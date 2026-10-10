/**
 * 行为测试：裸文件名不得被 markdown-it 的 fuzzy linkify 当成域名渲染成链接。
 *
 * 背景（用户实测）：正文「文件信息」表格里的 `北京到上海高铁车次参考清单.md` 渲染成了链接，
 * 点开跳到 `http://xn--fhq3o80gm0a7ipwy22kx5dumb851i5m9abpnbvs.md` → DNS_PROBE_FINISHED_NXDOMAIN。
 * 根因：linkify-it 默认把所有 **2 字符 ccTLD** 都当 TLD（`tlds_2ch_src_re` 含
 * `m[acdeghklmnopqrstuvwxyz]` → `.md`），于是「名字.md」「脚本.py」「部署.sh」都被当成裸域名。
 *
 * 断言的是「用户能看到的结果」：渲染后没有 <a>、文本一字不少、真实域名仍可点。
 */
import { createRequire } from 'node:module';
import process from 'node:process';

import {
  isBareFilenameLinkText,
  registerBareFilenameUnlink,
} from '../../frontend/src/utils/markdownBareFilename.ts';
import { appendBrowserOpenActions } from '../../frontend/src/utils/messageBrowserLinks.ts';

const require = createRequire(new URL('../../frontend/', import.meta.url));
const MarkdownIt = require('markdown-it');

const failures = [];
const check = (name, condition, detail = '') => {
  if (!condition) failures.push(detail ? `${name} —— ${detail}` : name);
};

const md = new MarkdownIt({ html: true, linkify: true, breaks: true });
registerBareFilenameUnlink(md);
const render = (src) => md.renderInline(src);
const linked = (src) => render(src).includes('<a ');

// ---------------------------------------------------------------------------
// 1. 用户实际踩到的场景：中文文件名 + .md
// ---------------------------------------------------------------------------
const chineseMd = '北京到上海高铁车次参考清单.md';
check('中文名 .md 不再渲染成链接', !linked(chineseMd), render(chineseMd));
check('文件名文本必须一字不少', render(chineseMd) === chineseMd, render(chineseMd));

// ---------------------------------------------------------------------------
// 2. 同一根因的其它扩展名（.py/.sh 也是 ccTLD，.json/.txt 只是顺带）
// ---------------------------------------------------------------------------
for (const name of ['report.docx', 'script.py', 'deploy.sh', 'data.json', 'notes.txt', 'pkg.zip', 'a.ts']) {
  check(`${name} 不再渲染成链接`, !linked(name), render(name));
}

// ---------------------------------------------------------------------------
// 3. 真实域名 / 显式链接必须照旧可点（不能为了修 bug 把链接能力砍掉）
// ---------------------------------------------------------------------------
check('裸域名仍可点', linked('www.example.com'), render('www.example.com'));
check('裸域名 example.com 仍可点', linked('example.com'), render('example.com'));
check('带协议的同名地址仍可点', linked('https://example.com/a.md'), render('https://example.com/a.md'));
check('显式 markdown 链接不受影响', linked('[清单](https://example.com/a.md)'), render('[清单](https://example.com/a.md)'));
// 关键用例：显式链接的**文字恰好是文件名**时也必须保留（早期版本漏了 markup 判定，
// 会把用户手写的链接一起拆掉 —— 只有这个用例能抓住）
const explicitFilenameLink = '[北京到上海高铁车次参考清单.md](https://example.com/report)';
check('文字是文件名的显式链接仍可点', linked(explicitFilenameLink), render(explicitFilenameLink));
check('邮箱仍可点', linked('someone@example.md'), render('someone@example.md'));

// ---------------------------------------------------------------------------
// 4. 混排在句子里：只取消文件名，不动其它内容
// ---------------------------------------------------------------------------
const inline = '已保存为 Markdown 文件，文件名为 北京到上海高铁车次参考清单.md，请查收。';
check('句中文件名不被链接化', !linked(inline), render(inline));
check('句中文本保持原样', render(inline) === inline, render(inline));

const mixed = '参见 example.com 与 report.md';
check('句中真实域名仍可点', linked(mixed), render(mixed));
check('句中被链接的只有域名', (render(mixed).match(/<a /g) || []).length === 1, render(mixed));

// ---------------------------------------------------------------------------
// 4.5 用户最终看到的那段 HTML：渲染 + 「打开」按钮后处理
//     （「打开」按钮只挂在 <a href="http(s)://…"> 上，所以假链接触发的按钮必须一起消失）
// ---------------------------------------------------------------------------
const finalFilenameHtml = appendBrowserOpenActions(render(chineseMd));
check('最终 HTML 里文件名不是链接', !finalFilenameHtml.includes('<a '), finalFilenameHtml);
check('最终 HTML 里文件名不带「打开」按钮', !finalFilenameHtml.includes('data-open-browser-url'), finalFilenameHtml);
check(
  '最终 HTML 保留文件名原文',
  finalFilenameHtml.replace(/<[^>]*>/g, '') === chineseMd,
  finalFilenameHtml,
);
// 能力没被砍掉：真实域名仍会带上「打开」按钮
check(
  '真实域名仍带「打开」按钮',
  appendBrowserOpenActions(render('www.example.com')).includes('data-open-browser-url'),
);

// ---------------------------------------------------------------------------
// 5. 判定函数本身的边界（供插件与后续复用）
// ---------------------------------------------------------------------------
const shouldBeFilename = ['北京到上海高铁车次参考清单.md', 'report.docx', 'script.py', 'deploy.sh', 'a.json', 'x.zip'];
const shouldNotBeFilename = [
  'www.example.com',
  'example.com',
  'https://example.com/a.md',
  'path/to/report.md',
  'someone@example.com',
  'README',
  '1.5',
  'example.cn',
];
for (const value of shouldBeFilename) {
  check(`判定 ${value} 为文件名`, isBareFilenameLinkText(value) === true);
}
for (const value of shouldNotBeFilename) {
  check(`判定 ${value} 不是文件名`, isBareFilenameLinkText(value) === false);
}

if (failures.length) {
  console.error('markdown_bare_filename 行为测试失败：');
  for (const f of failures) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`markdown_bare_filename 行为测试通过（${chineseMd} 等裸文件名不再变链接，真实域名仍可点）`);
