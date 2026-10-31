const INTERNAL_CONTEXT_BLOCKS = [
  "backend_tool_run_summary",
  "backend_tool_result_context",
  "backend_injected_attachments",
  "system_injected_attachments",
  "USER_PROFILE",
  "AUTH_CONTEXT",
  "vision_sidecar",
  "function_calls",
  "think",
  "thought",
  "reasoning",
  "redacted_reasoning",
  "historical_context",
  "历史上下文",
] as const;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 一次 O(n) 扫描判断文本里是否真的出现内部块起始标签。
 *
 * 原实现对 15 个标签名各跑两条 `[\s\S]*?` 全文正则（完整块 + 未闭合块），一次清洗
 * 要扫全文 30 遍。而时间线的工具结果（子代理桥接的 MCP 返回）动辄几十 KB，并且
 * 每次重渲染都会对每个节点的 details 调一次——委派场景下这就是主线程被吃掉的原因。
 * 绝大多数文本根本不含这些标签，先用一条合并正则挡掉，命中不了直接返回。
 */
const INTERNAL_BLOCK_QUICK_TEST = new RegExp(
  `<\\s*(?:${INTERNAL_CONTEXT_BLOCKS.map(escapeRegExp).join("|")})(?=[\\s>])`,
  "i",
);

function stripInternalXmlBlocks(content: string, removeUnclosed: boolean): string {
  let text = content;
  if (!INTERNAL_BLOCK_QUICK_TEST.test(text)) return text;
  for (const name of INTERNAL_CONTEXT_BLOCKS) {
    const escapedName = escapeRegExp(name);
    const completeBlock = new RegExp(
      `<\\s*${escapedName}(?=[\\s>])[^>]*>[\\s\\S]*?<\\s*\\/\\s*${escapedName}\\s*>`,
      "gi",
    );
    text = text.replace(completeBlock, "");
    if (removeUnclosed) {
      const unclosedBlock = new RegExp(
        `<\\s*${escapedName}(?=[\\s>])[^>]*>[\\s\\S]*$`,
        "gi",
      );
      text = text.replace(unclosedBlock, "");
    }
  }
  return text;
}

/**
 * 清理模型可见、但不应出现在用户回答里的内部上下文标记。
 *
 * 该函数同时服务于流式正文和历史正文：对未闭合的内部块直接清理到文本末尾，
 * 避免 SSE 把开始标签和结束标签拆成两个 chunk 时出现半截内部内容。
 */
export function stripInternalContextBlocks(
  content: string,
  options: { removeUnclosed?: boolean; normalizeWhitespace?: boolean } = {},
): string {
  if (!content) return "";
  const removeUnclosed = options.removeUnclosed !== false;
  const normalizeWhitespace = options.normalizeWhitespace !== false;
  let text = stripInternalXmlBlocks(String(content), removeUnclosed);

  // normalize_messages_for_llm 包装的系统上下文区块，模型偶尔会原样复述。
  // 同样是先判有无注释起始标记，没命中就不跑这两条跨行正则。
  if (text.includes("<!--")) {
    text = text.replace(
      /<!--[ \t]*SYSTEM_BLOCK_START:[\s\S]*?-->[\s\S]*?<!--[ \t]*SYSTEM_BLOCK_END:[\s\S]*?-->/gi,
      "",
    );
    if (removeUnclosed) {
      text = text.replace(/<!--[ \t]*SYSTEM_BLOCK_START:[\s\S]*?-->[\s\S]*$/gi, "");
    }
  }

  // 这些是模型上下文边界/身份提示，不是用户回答内容。
  // 只有出现「[」或「〔」等标记时才需要逐行匹配，否则整段跳过。
  if (text.includes("[") || text.includes("〔") || text.includes("以下是更早轮次对话的要点")) {
    text = text
      .replace(/^[ \t]*\[本回复由智能体「[^」\r\n]*」生成\][ \t]*\r?\n?/gim, "")
      .replace(/^[ \t]*\[早前对话摘录\][ \t]*\r?\n?/gim, "")
      .replace(/^[ \t]*\[上一轮可复用工具结果\][ \t]*\r?\n?/gim, "")
      .replace(/^[ \t]*〔更早轮次对话要点〕[ \t]*\r?\n?/gim, "")
      .replace(/^[ \t]*以下是更早轮次对话的要点.*\r?\n?/gim, "");
  }

  if (!normalizeWhitespace) return text;

  return text
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** 剥离流式正文中的推理块 / XML 工具块，避免整段被前端丢弃。 */
export function sanitizeStreamContent(content: string): string {
  // 流式 chunk 可能只包含开始标签；保留原始半截内容，交给完整回答展示层
  // 在累计全文上处理，避免下一 chunk 的内部正文失去“正在内部块内”的上下文。
  // 这里不能 trim：Markdown 标题、表格和 fenced code block 的换行可能正好
  // 位于两个 SSE chunk 的边界，逐片 trim 会把它们粘成普通文本。
  return stripInternalContextBlocks(content, {
    removeUnclosed: false,
    normalizeWhitespace: false,
  });
}
