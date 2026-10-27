/**
 * 消息正文里的本地文件路径 → 画布「打开」链接（纯字符串处理，零依赖）。
 *
 * 这段逻辑原内联在 `MessageRenderer.vue` 里，只能靠「源码里有没有某个字符串」的契约测试
 * 守着，于是 `.json` 被截成 `.js` 的线上问题（#272）一路漏到了用户眼前。抽成独立模块后，
 * 行为测试可以用 `node --experimental-strip-types` 直接加载本文件、跑真实正则。
 *
 * 改扩展名白名单前先读这两条约束：
 *
 * 1) 扩展名之后必须有**结束边界** `(?![A-Za-z0-9_-]|\.[A-Za-z0-9])`。
 *    少了它，`.../result.json` 会被 `js` 分支吃掉前两个字符，剩下的 `on` 漏在正文里，
 *    渲染成 `result.js[打开]on`——点「打开」请求的是一个不存在的 `.js` 路径（404）。
 *    边界里带上 `\.[A-Za-z0-9]` 是为 `report.json.bak` 这类多段后缀：宁可不出链接，也
 *    不要给出一个必然 404 的假路径（句末的英文句号仍然放行）。
 *
 * 2) 扩展名按**长 → 短**排列。有边界时引擎本来就会回溯到更长的分支，排序只是第二道保险、
 *    并让枚举本身可读；但 `.jsonl` / `.jsx` / `.tsx` 早先根本不在名单里，只加边界会把它们
 *    从「错的链接」变成「完全不出链接」，所以一并补齐。
 */

/** 已知文件扩展名，长 → 短。`docx?` 这类可选后缀同时覆盖 `.doc` 与 `.docx`。 */
const FILE_PATH_EXTENSIONS =
  'jsonl|json|html|yaml|docx?|xlsm|xlsx?|pptx?|csv|txt|sql|pdf|css|yml|log|env|md|jsx|js|tsx|ts|sh|py';

/**
 * 单级路径片段：不允许 `/`（切分目录）、`#`（`###HTML_TAG_PLACEHOLDER_n###` 占位符）、
 * 空白与引号/中英文标点（避免跨过属性值或句子边界）。
 */
const FILE_PATH_SEGMENT = String.raw`[^/#\s<>"'，。；：！？、]+`;

/**
 * 「至少一级目录 + 文件名 + 已知扩展名」的本地路径。
 *
 * 只认带目录的路径：`result.json` 这种裸文件名不在这里加「打开」链接（没有目录信息，
 * 后端无从解析；裸文件名另有 markdown 侧的处理，见 `markdownBareFilename.ts`）。
 */
const filePathRegex = new RegExp(
  String.raw`(?:\./|/)?(?:${FILE_PATH_SEGMENT}/)+${FILE_PATH_SEGMENT}\.(?:${FILE_PATH_EXTENSIONS})(?![A-Za-z0-9_-]|\.[A-Za-z0-9])`,
  'giu',
);

/** 平台内部地址（API / 静态资源）本来就能直接打开，不追加画布链接。 */
const INTERNAL_PATH_PATTERN = /^\/?(?:api|static|assets)\//i;

const appendOpenLinkToPath = (rawPathVal: string): string => {
  // 占位符理论上进不来（片段类排除了 `#`），保留兜底：历史版本曾把标签吃掉后残留。
  const pathVal = rawPathVal.replace(/###HTML_TAG_PLACEHOLDER_\d+###/g, '').trim();
  if (!pathVal) return rawPathVal;
  if (INTERNAL_PATH_PATTERN.test(pathVal)) return rawPathVal;
  const canvasUrl = `canvas://file?path=${encodeURIComponent(pathVal)}`;
  return `${pathVal}<a href="${canvasUrl}" class="inline-flex items-center text-blue-600 dark:text-blue-400 hover:underline font-bold ml-1.5 text-[10.5px]" title="点击在画布中打开文件" style="cursor: pointer;">[打开]</a>`;
};

/**
 * 给正文里出现的本地文件路径追加「打开」链接。
 *
 * 传入的必须是**已渲染的 HTML 片段**（`MessageRenderer` 在标签被替换成占位符之后调用），
 * 匹配到的原文一律原样保留，只在后面追加 `<a>`——所以路径本身不会被改写。
 */
export const injectOpenLinksForPaths = (text: string): string =>
  text.replace(filePathRegex, (pathVal) => appendOpenLinkToPath(pathVal));
