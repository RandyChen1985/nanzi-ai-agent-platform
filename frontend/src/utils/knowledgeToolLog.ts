export type KnowledgeCitationChunk = {
  id?: string;
  doc_name?: string;
  document_name?: string;
  similarity?: number;
  content?: string;
};

export type KnowledgeToolLogView = {
  kind: "knowledge";
  summary?: string;
  citations: KnowledgeCitationChunk[];
  emptyMessage?: string;
};

/** 解析 search_knowledge_base 工具日志（JSON 或已格式化的纯文本） */
export function parseKnowledgeToolLog(text: string): KnowledgeToolLogView | null {
  if (!text?.trim()) return null;
  try {
    const data = JSON.parse(text);
    if (Array.isArray(data) && data.length > 0 && (data[0].doc_name || data[0].content)) {
      return { kind: "knowledge", citations: data };
    }
    if (data && typeof data === "object" && Array.isArray(data.citations)) {
      if (data.status === "empty") {
        return {
          kind: "knowledge",
          citations: [],
          emptyMessage: String(data.content || "未找到相关内容"),
        };
      }
      return {
        kind: "knowledge",
        summary: typeof data.content === "string" ? data.content : undefined,
        citations: data.citations,
      };
    }
  } catch {
    if (text.includes("【引用片段】") || text.includes("--- [ID:")) {
      return { kind: "knowledge", citations: [], summary: text };
    }
  }
  return null;
}

export function isKnowledgeToolLog(text: string): boolean {
  return parseKnowledgeToolLog(text) !== null;
}
