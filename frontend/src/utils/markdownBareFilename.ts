/**
 * 撤销 markdown 的「裸文件名被当成域名」自动链接。
 *
 * 根因：markdown-it 的 linkify 走 linkify-it，而 linkify-it 默认把**所有 2 字符 ccTLD**
 * 都当成合法顶级域（它内部把 2 字符 TLD 编成正则 `m[acdeghklmnopqrstuvwxyz]`，其中就有
 * `.md`）。于是正文里的 `北京到上海高铁车次参考清单.md` 被渲染成
 * `<a href="http://xn--fhq3o80gm0a7ipwy22kx5dumb851i5m9abpnbvs.md">`——点开就是
 * `DNS_PROBE_FINISHED_NXDOMAIN`。`.py`（巴拉圭）、`.sh`（圣赫勒拿）、`.pl`、`.rs` 同理。
 *
 * 为什么不去改 linkify-it 的 TLD 列表：① 它没有「移除单个 TLD」的公开 API，
 * `tlds(list, false)` 是**整体替换**（实测连 `www.example.com` 都不再链接）；
 * ② 把 2 字符 ccTLD 一刀切会让 `.cn`/`.io`/`.ai` 这些真实域名失去链接能力。
 * 所以这里只在**已经 linkify 出来的链接**上再判一次：候选文本像文件名就还原成纯文本。
 * 显式链接（`[文本](url)`）的 `markup` 不是 `linkify`，天然不受影响。
 *
 * 本模块零依赖（只用显式带 `.ts` 后缀的相对导入），这样行为测试可以用
 * `node --experimental-strip-types` 直接加载它跑真实 markdown-it。
 */
import {
  DOCUMENT_VIEWER_EXTENSIONS,
  IMAGE_EXTENSIONS,
  OFFICE_EXTENSIONS,
  TEXT_EXTENSIONS,
  getWorkspaceFileExtension,
} from './documentPreviewFormats.ts';

/**
 * 「`名字.ext` 是文件名而非域名」的扩展名集合。
 *
 * 判断依据只能是扩展名：`example.com` 既可能是域名也可能是文件名，而 `.com` 不在
 * 文件类型集合里 → 仍按域名处理（保留链接能力）；`.md`/`.py`/`.sh` 是已知文件类型
 * → 按文件名处理（这正是用户踩到的场景）。
 */
const FILENAME_EXTENSIONS: ReadonlySet<string> = new Set([
  ...TEXT_EXTENSIONS,
  ...DOCUMENT_VIEWER_EXTENSIONS,
  ...IMAGE_EXTENSIONS,
  ...OFFICE_EXTENSIONS,
  '.zip', '.rar', '.7z', '.tar', '.gz', '.tgz', '.bz2', '.xz',
  '.svg', '.bmp', '.tif', '.tiff', '.ico', '.heic', '.avif',
  '.ppt', '.pptx', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.pdf',
]);

/** 带协议的 URL（http://、mailto:、canvas://…）或协议相对地址，绝不是"裸文件名"。 */
const SCHEME_PATTERN = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i;

/**
 * 这段文本是不是「一个裸文件名」？
 *
 * 只认单个令牌：不含空白、路径分隔符或 `@`（所以 `path/to/a.md`、`someone@b.md` 不算），
 * 且扩展名属于已知文件类型。`www.example.com`、`example.com`、`https://…` 一律不算。
 */
export const isBareFilenameLinkText = (text: string): boolean => {
  const value = String(text ?? '').trim();
  if (!value) return false;
  if (SCHEME_PATTERN.test(value)) return false;
  if (/[\s/\\@]/.test(value)) return false;
  const extension = getWorkspaceFileExtension(value);
  return Boolean(extension) && FILENAME_EXTENSIONS.has(extension);
};

/** markdown-it 的最小结构类型（结构化声明，避免本模块依赖 markdown-it 包）。 */
interface MarkdownToken {
  type: string;
  markup?: string;
  content?: string;
  children?: MarkdownToken[] | null;
}

interface MarkdownState {
  tokens: MarkdownToken[];
}

interface MarkdownLike {
  core: {
    ruler: {
      push: (name: string, rule: (state: MarkdownState) => void) => unknown;
    };
  };
}

/**
 * 注册 core 规则：把「被 linkify 成域名的裸文件名」还原为纯文本。
 *
 * 只处理 `link_open(markup === 'linkify') + text + link_close` 这个三元组，
 * 文本原样保留；显式 markdown 链接、邮箱链接、带协议的 URL 都不受影响。
 */
export const registerBareFilenameUnlink = (md: MarkdownLike): void => {
  md.core.ruler.push('bare_filename_unlink', (state: MarkdownState) => {
    for (const blockToken of state.tokens || []) {
      const children = blockToken.children;
      if (!children || !children.length) continue;
      for (let index = 0; index < children.length; index += 1) {
        const openToken = children[index];
        if (!openToken || openToken.type !== 'link_open' || openToken.markup !== 'linkify') continue;
        const textToken = children[index + 1];
        const closeToken = children[index + 2];
        if (!textToken || !closeToken) continue;
        if (textToken.type !== 'text' || closeToken.type !== 'link_close') continue;
        if (!isBareFilenameLinkText(textToken.content || '')) continue;
        children.splice(index, 3, { type: 'text', content: textToken.content });
      }
    }
  });
};
