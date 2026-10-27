/**
 * 行为测试：正文里的本地文件路径必须**整段原样**变成「打开」链接（真实正则，非源码断言）。
 *
 * 背景（#272，用户实测）：`.../ai_schedule_output/result.json` 被渲染成
 * `result.js[打开]on`——扩展名白名单里 `js` 排在 `json` 前面，且正则尾部没有结束边界，
 * 于是 `.json` 的前两个字符被 `js` 分支吃掉、剩下的 `on` 漏在正文里；点「打开」请求的是
 * 不存在的 `.js` 路径，后端 404。
 *
 * 这里直接 `node --experimental-strip-types` 加载 `frontend/src/utils/messageFilePathLinks.ts`
 * 跑真实代码（不复制逻辑），断言的是用户能看到的结果：路径一字不少、链接里的 path 参数
 * 就是原路径、不残留被截断的尾巴。
 */
import process from 'node:process';

import { injectOpenLinksForPaths } from '../../frontend/src/utils/messageFilePathLinks.ts';

const failures = [];
const check = (name, condition, detail = '') => {
  if (!condition) failures.push(detail ? `${name} —— ${detail}` : name);
};

/** 去掉标签后的可见文本（相当于用户看到的正文）。 */
const textOf = (html) => html.replace(/<[^>]*>/g, '');

/** 取出第一段「打开」链接里的 `path` 参数（已解码）。 */
const linkedPathOf = (html) => {
  const match = html.match(/href="canvas:\/\/file\?path=([^"]+)"/);
  if (!match) return null;
  return decodeURIComponent(match[1].replace(/&amp;/g, '&'));
};

const openLinkCount = (html) => (html.match(/\[打开\]/g) || []).length;

/**
 * 单个路径的核心断言：整段保留 + 只加一个链接 + path 参数与原文完全一致 + 无截断残留。
 */
const checkPath = (path) => {
  const html = injectOpenLinksForPaths(path);
  check(`${path} 整段保留`, textOf(html) === `${path}[打开]`, textOf(html));
  check(`${path} 只加一个链接`, openLinkCount(html) === 1, html);
  check(`${path} 链接 path 参数等于原路径`, linkedPathOf(html) === path, String(linkedPathOf(html)));
};

// ---------------------------------------------------------------------------
// 1. 用户实际踩到的场景：.json 被截成 .js
// ---------------------------------------------------------------------------
const userCase = '/app/data/agent_workspaces/admin__1/sessions/session-id/ai_schedule_output/result.json';
checkPath(userCase);
const userCaseHtml = injectOpenLinksForPaths(userCase);
check('不再出现 .js[打开]on', !userCaseHtml.includes('.js[打开]'), userCaseHtml);
check('可见文本以完整文件名结尾', textOf(userCaseHtml).endsWith('ai_schedule_output/result.json[打开]'), textOf(userCaseHtml));

// ---------------------------------------------------------------------------
// 2. 同一根因的「前缀扩展名」全家族（issue 列出的 + 名单里原本缺失的）
// ---------------------------------------------------------------------------
for (const name of [
  'result.json', 'result.jsonl',
  'mod.js', 'mod.jsx',
  'types.ts', 'types.tsx',
  'book.xls', 'book.xlsx', 'book.xlsm',
  'note.md', 'conf.yaml', 'conf.yml',
  'report.docx', 'deck.pptx', 'page.html', 'query.sql',
]) {
  checkPath(`/data/agent_workspaces/ws/${name}`);
}

// ---------------------------------------------------------------------------
// 3. 边界：不制造「指向不存在文件」的链接，也不吞掉句末标点
// ---------------------------------------------------------------------------
const dotBak = '/data/report.json.bak';
check('多段后缀 .json.bak 不出链接', injectOpenLinksForPaths(dotBak) === dotBak, injectOpenLinksForPaths(dotBak));

const dotBackup = '/data/report.json.backup1';
check('多段后缀 .json.backup1 不出链接', injectOpenLinksForPaths(dotBackup) === dotBackup, injectOpenLinksForPaths(dotBackup));

const sentence = '结果已写入 /data/output/result.json.';
const sentenceHtml = injectOpenLinksForPaths(sentence);
check('英文句末句号不进入路径', linkedPathOf(sentenceHtml) === '/data/output/result.json', sentenceHtml);
check('英文句末句号仍留在正文', textOf(sentenceHtml) === '结果已写入 /data/output/result.json[打开].', textOf(sentenceHtml));

const chinesePunct = '已保存到 /data/output/result.json。请查收。';
const chineseHtml = injectOpenLinksForPaths(chinesePunct);
check('中文句号不进入路径', linkedPathOf(chineseHtml) === '/data/output/result.json', chineseHtml);
check('中文句号后文本保持原样', textOf(chineseHtml).endsWith('。请查收。'), textOf(chineseHtml));

const asciiComma = 'files: /data/a.json, /data/b.jsx';
const asciiCommaHtml = injectOpenLinksForPaths(asciiComma);
check('逗号分隔的两个路径都成链', openLinkCount(asciiCommaHtml) === 2, asciiCommaHtml);
check('逗号分隔的第一个路径完整', linkedPathOf(asciiCommaHtml) === '/data/a.json', asciiCommaHtml);
check('逗号分隔的第二个路径完整', asciiCommaHtml.includes('path=%2Fdata%2Fb.jsx'), asciiCommaHtml);

// ---------------------------------------------------------------------------
// 4. 不该加链接的三类：裸文件名、平台内部路径、无扩展名
// ---------------------------------------------------------------------------
for (const untouched of ['result.json', '20261020_xxx.json', '/api/v1/chat/fs/preview?path=x', '/static/uploads/a.json']) {
  check(`${untouched} 不追加链接`, !injectOpenLinksForPaths(untouched).includes('[打开]'), injectOpenLinksForPaths(untouched));
}

// ---------------------------------------------------------------------------
// 5. 大小写原样保留（正则带 i，但路径本身不能被改写）
// ---------------------------------------------------------------------------
const upper = '/data/output/RESULT.JSON';
checkPath(upper);

// ---------------------------------------------------------------------------
// 6. 与 MessageRenderer 的真实调用顺序一致：标签先被占位符替换，再注入链接
//    （含 <code> 兜底那一趟）
// ---------------------------------------------------------------------------
const withPlaceholder = '/data/output/result.json###HTML_TAG_PLACEHOLDER_7###';
const placeholderHtml = injectOpenLinksForPaths(withPlaceholder);
check(
  '占位符紧邻路径时路径不被吃掉',
  placeholderHtml.startsWith('/data/output/result.json<a href="canvas://file?path=%2Fdata%2Foutput%2Fresult.json"'),
  placeholderHtml,
);
check('占位符原样保留', placeholderHtml.includes('###HTML_TAG_PLACEHOLDER_7###'), placeholderHtml);

const codeHtml = injectOpenLinksForPaths('<code>/data/output/result.json</code>');
check(
  'code 内的路径完整成链',
  codeHtml.includes('<code>/data/output/result.json<a href="canvas://file?path=%2Fdata%2Foutput%2Fresult.json"'),
  codeHtml,
);

// ---------------------------------------------------------------------------
// 7. 一句话里同扩展名出现两次：各成一条链接，互不吞并
// ---------------------------------------------------------------------------
const twice = '/tmp/a/one.json 与 /tmp/b/two.json';
const twiceHtml = injectOpenLinksForPaths(twice);
check('同扩展名两次都成链', openLinkCount(twiceHtml) === 2, twiceHtml);
check('同扩展名第一条路径完整', twiceHtml.includes('path=%2Ftmp%2Fa%2Fone.json'), twiceHtml);
check('同扩展名第二条路径完整', twiceHtml.includes('path=%2Ftmp%2Fb%2Ftwo.json'), twiceHtml);

if (failures.length) {
  console.error('message_file_path_links 行为测试失败：');
  for (const failure of failures) console.error(` - ${failure}`);
  process.exit(1);
}
console.log('message_file_path_links 行为测试通过（.json/.jsonl/.jsx/.tsx/.xlsm 等前缀扩展名全段保留，链接 path 参数与原文一致）');
