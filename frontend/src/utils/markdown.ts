import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css' // Base styles

const md: MarkdownIt = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  highlight: null
})

// Override fence rule to prevent double wrapping
md.renderer.rules.fence = function (tokens, idx, _options, _env, _self) {
  const token = tokens[idx]!;
  const info = token.info ? md.utils.unescapeAll(token.info).trim() : '';
  let langName = '';
  let highlighted: string;

  if (info) {
    langName = info.split(/\s+/g)[0] || '';
  }

  if (langName && hljs.getLanguage(langName)) {
    try {
      highlighted = hljs.highlight(token.content, { language: langName, ignoreIllegals: true }).value;
    } catch (_) {
      highlighted = md.utils.escapeHtml(token.content);
    }
  } else {
    highlighted = md.utils.escapeHtml(token.content);
  }

  // Custom wrapper structure
  return `<div class="code-block-wrapper"><pre class="hljs"><code>${highlighted}</code></pre><span class="code-copy-btn" title="复制代码"></span></div>`;
};

// Allow "quick:" protocol in links
const defaultValidateLink = md.validateLink;
md.validateLink = (url) => {
  const str = url.trim().toLowerCase();
  return defaultValidateLink(url) || str.startsWith('quick:');
};

// Custom rule to open links in new window
const defaultRender = md.renderer.rules.link_open || function(tokens, idx, options, _env, self) {
  return self.renderToken(tokens, idx, options);
};

md.renderer.rules.link_open = function (tokens, idx, options, _env, self) {
  const token = tokens[idx]!;
  if (!token.attrs) {
    token.attrs = [];
  }
  
  const hAttr = token.attrIndex('href');
  if (hAttr >= 0 && token.attrs!) {
    const href = token.attrs![hAttr]![1];
    // Check if it's an external link (starts with http or https)
    // OR a "quick:" protocol link
    if (/^https?:\/\//.test(href) || href.startsWith('quick:')) {
      const aIndex = token.attrIndex('target');
      if (aIndex < 0) {
        token.attrPush(['target', '_blank']);
      } else {
        token.attrs![aIndex]![1] = '_blank';
      }
      
      // Also add security rel attribute for external links
      if (/^https?:\/\//.test(href)) {
        const rIndex = token.attrIndex('rel');
        if (rIndex < 0) {
          token.attrPush(['rel', 'noopener noreferrer']);
        }
      }
      
      // Add special class for quick links
      if (href.startsWith('quick:')) {
        const classIndex = token.attrIndex('class');
        const className = 'quick-action-btn';
        if (classIndex < 0) {
          token.attrPush(['class', className]);
        } else {
          token.attrs![classIndex]![1] = className;
        }
        token.attrPush(['data-quick-link', 'true']);
      }
    }
  }
  return defaultRender(tokens, idx, options, _env, self);
};

const normalizePipeTables = (content: string) => {
  return content
    .split('\n')
    .map((line) => {
      const trimmed = line.trim();
      if (!trimmed.startsWith('|') || !trimmed.endsWith('|') || !trimmed.includes('||')) {
        return line;
      }

      const cells = trimmed
        .slice(1, -1)
        .split(/\s*\|\|\s*/)
        .map((cell) => cell.trim())
        .filter(Boolean);

      const firstSeparator = cells.findIndex((cell) => /^:?-{3,}:?$/.test(cell));
      if (firstSeparator <= 0) return line;

      const columnCount = firstSeparator;
      const separatorCount = cells
        .slice(firstSeparator, firstSeparator + columnCount)
        .filter((cell) => /^:?-{3,}:?$/.test(cell)).length;
      const bodyCells = cells.slice(firstSeparator + columnCount);
      if (separatorCount !== columnCount || bodyCells.length === 0 || bodyCells.length % columnCount !== 0) {
        return line;
      }

      const rows = [
        cells.slice(0, columnCount),
        cells.slice(firstSeparator, firstSeparator + columnCount),
      ];
      for (let i = 0; i < bodyCells.length; i += columnCount) {
        rows.push(bodyCells.slice(i, i + columnCount));
      }
      return rows.map((row) => `| ${row.join(' | ')} |`).join('\n');
    })
    .join('\n');
};

export const renderMarkdown = (content: string) => {
  return md.render(normalizePipeTables(content))
}
