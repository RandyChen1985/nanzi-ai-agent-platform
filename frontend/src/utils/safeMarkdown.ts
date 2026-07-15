import MarkdownIt from "markdown-it";

const safeMarkdownPreview = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: true,
});

const defaultLinkOpen = safeMarkdownPreview.renderer.rules.link_open
  || ((tokens, index, options, _env, self) => self.renderToken(tokens, index, options));

safeMarkdownPreview.renderer.rules.link_open = (tokens, index, options, env, self) => {
  const token = tokens[index];
  if (!token) return "";
  const href = token.attrGet("href") || "";
  if (/^https?:\/\//i.test(href)) {
    token.attrSet("target", "_blank");
    token.attrSet("rel", "noopener noreferrer");
  }
  return defaultLinkOpen(tokens, index, options, env, self);
};

export const renderSafeMarkdownPreview = (content: string) =>
  safeMarkdownPreview.render(String(content || ""));
