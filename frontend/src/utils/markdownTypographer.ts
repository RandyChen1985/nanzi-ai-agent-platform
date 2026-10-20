/**
 * 保留 `+-` 原文的 typographer 规则（覆盖 markdown-it 内置的 `replacements`）。
 *
 * 背景：`markdown-it` 打开 `typographer: true` 后，会把正文里的 `+-` 替换成 `±`
 * （官方实现 `lib/rules_core/replacements.mjs`）。这属于排版糖，但平台里 AI 回答经常
 * 出现设备名/型号/编号、命令行片段、ASCII 图，替换后用户看到的内容和原文不一致，
 * 是**内容损坏**而不是美化。实测：EmbedChat 里设备名 `2000+-` 被显示成 `2000±`。
 *
 * 处理方式：用本规则**整体替换** core 链上的 `replacements`，替换顺序与官方逐条对齐，
 * 只去掉 `\+- → ±` 这一条。其余排版增强继续保留：
 *   `--` → `–`、`---` → `—`、`...` → `…`、`(c)/(r)/(tm)`、连写标点折叠。
 *
 * 生效范围与官方一致：只改 `text` token —— 行内代码、围栏代码块、autolink URL
 * 里的文本本来就不受影响；数学公式由 KaTeX 处理，与本规则无关。
 *
 * 本模块零运行时依赖（只有结构化类型声明，避免测试环境去解析 markdown-it 的类型入口），
 * 行为测试可用 `node --experimental-strip-types` 直接加载并跑真实 markdown-it。
 */

/**
 * 与官方 `RARE_RE` 相同，但**不含** `\+-`。
 *
 * 这是关键：官方用它做快速预判（命中才逐条替换）。去掉 `\+-` 后，纯 `+-` 文本
 * 不会进入替换分支，原样输出；而 `--`、`...`、`????`、`!!!!`、`,,` 仍然命中。
 */
const RARE_RE = /\.\.|\?\?\?\?|!!!!|,,|--/;

/** 与官方 `SCOPED_ABBR_TEST_RE` 一致。 */
const SCOPED_ABBR_TEST_RE = /\((c|tm|r)\)/i;
const SCOPED_ABBR_RE = /\((c|tm|r)\)/gi;
const SCOPED_ABBR: Record<string, string> = { c: '©', r: '®', tm: '™' };

/** markdown-it 的最小结构类型（结构化声明，避免本模块依赖 markdown-it 包）。 */
interface MarkdownToken {
  type: string;
  content?: string;
  info?: string;
  children?: MarkdownToken[] | null;
}

interface MarkdownState {
  tokens: MarkdownToken[];
  md?: { options?: { typographer?: boolean } };
}

interface MarkdownLike {
  core: {
    ruler: {
      at: (name: string, rule: (state: MarkdownState) => void) => unknown;
    };
  };
}

/** 官方 `replace_scoped`：`(c)` / `(r)` / `(tm)` → © / ® / ™（autolink 内部跳过）。 */
const replaceScopedAbbreviations = (inlineTokens: MarkdownToken[]): void => {
  let insideAutolink = 0;

  for (let index = inlineTokens.length - 1; index >= 0; index -= 1) {
    const token = inlineTokens[index]!;

    if (token.type === 'text' && !insideAutolink) {
      token.content = (token.content || '').replace(
        SCOPED_ABBR_RE,
        (match, name: string) => SCOPED_ABBR[name.toLowerCase()] ?? match,
      );
    }

    if (token.type === 'link_open' && token.info === 'auto') {
      insideAutolink -= 1;
    }

    if (token.type === 'link_close' && token.info === 'auto') {
      insideAutolink += 1;
    }
  }
};

/**
 * 官方 `replace_rare`，去掉 `.replace(/\+-/g, '±')`，其余替换顺序保持不变
 * （顺序有影响：先折叠 `...`，再用 `([?!])…` 回退，最后处理破折号）。
 */
const replaceRareSequences = (inlineTokens: MarkdownToken[]): void => {
  let insideAutolink = 0;

  for (let index = inlineTokens.length - 1; index >= 0; index -= 1) {
    const token = inlineTokens[index]!;

    if (token.type === 'text' && !insideAutolink) {
      const content = token.content || '';
      if (RARE_RE.test(content)) {
        token.content = content
          // .., ..., ....... -> …
          // but ?..... & !..... -> ?.. & !..
          .replace(/\.{2,}/g, '…').replace(/([?!])…/g, '$1..')
          .replace(/([?!]){4,}/g, '$1$1$1').replace(/,{2,}/g, ',')
          // em-dash
          .replace(/(^|[^-])---(?=[^-]|$)/gm, '$1\u2014')
          // en-dash
          .replace(/(^|\s)--(?=\s|$)/gm, '$1\u2013')
          .replace(/(^|[^-\s])--(?=[^-\s]|$)/gm, '$1\u2013');
      }
    }

    if (token.type === 'link_open' && token.info === 'auto') {
      insideAutolink -= 1;
    }

    if (token.type === 'link_close' && token.info === 'auto') {
      insideAutolink += 1;
    }
  }
};

/**
 * 把实例的 typographer 替换规则换成「不改 `+-`」的版本。
 *
 * 调用方仍需保持 `typographer: true`（本规则复用了官方开关判断，关掉时什么都不做）。
 * 规则名 `replacements` 是 markdown-it 公开文档给出的替换入口，
 * 见官方 Ruler 注释中的示例。
 */
export const registerTypographerWithoutPlusMinus = (md: MarkdownLike): void => {
  md.core.ruler.at('replacements', (state: MarkdownState) => {
    if (!state.md?.options?.typographer) return;

    for (let blockIndex = state.tokens.length - 1; blockIndex >= 0; blockIndex -= 1) {
      const blockToken = state.tokens[blockIndex];
      if (!blockToken || blockToken.type !== 'inline') continue;

      const children = blockToken.children;
      if (!children || !children.length) continue;

      const content = blockToken.content || '';
      if (SCOPED_ABBR_TEST_RE.test(content)) replaceScopedAbbreviations(children);
      if (RARE_RE.test(content)) replaceRareSequences(children);
    }
  });
};
