import { getServerAttachmentPath, isImageAttachment } from "@/utils/attachmentImages";

export const USER_MESSAGE_CONTEXT_DIVIDER = "\n\n---\n\n";

interface AttachmentSkillMeta {
  name?: string;
  description?: string;
}

interface AttachmentMemoryMeta {
  last_active?: number;
  summary?: string;
}

export interface ChatAttachment {
  type?: string;
  url: string;
  filename: string;
  skillMeta?: AttachmentSkillMeta;
  memoryMeta?: AttachmentMemoryMeta[];
}

interface ChatAttachmentOptions {
  buildKnowledgeBaseAttachmentHint: (datasetIdLine: string) => string;
}

export const splitUserMessageContent = (text: string) => {
  const raw = text || "";
  const idx = raw.indexOf(USER_MESSAGE_CONTEXT_DIVIDER);
  if (idx === -1) {
    return { hasContext: false, userPart: raw, contextPart: "" };
  }
  return {
    hasContext: true,
    userPart: raw.slice(0, idx).trim(),
    contextPart: raw.slice(idx + USER_MESSAGE_CONTEXT_DIVIDER.length).trim(),
  };
};

export const useChatAttachments = ({
  buildKnowledgeBaseAttachmentHint,
}: ChatAttachmentOptions) => {
  const buildImageAttachmentHint = (file: ChatAttachment, path: string) => {
    if (file.type === "local_file") {
      return `用户本轮已从服务器挂载图片：${file.filename}，该图片已作为视觉多模态输入随消息一并发送（源路径：${path}）。`;
    }
    return `用户本轮已上传图片：${file.filename}，该图片已作为视觉多模态输入随消息一并发送（托管路径：${path}）。`;
  };

  const buildSkillAttachmentHint = (file: ChatAttachment, path: string) => {
    const skillName = file.filename.replace(" (技能)", "");
    const meta = file.skillMeta;
    const metaParts: string[] = [];
    if (meta?.name) metaParts.push(`name: ${meta.name}`);
    if (meta?.description) metaParts.push(`description: ${meta.description}`);
    const metaText = metaParts.length > 0 ? metaParts.join(", ") : "";
    let hint = `用户本轮已调用生态技能工作流：${skillName}，对应的物理描述文件绝对路径是：${path}。`;
    if (metaText) {
      hint += `\nskills meta 为：${metaText}`;
    }
    return hint;
  };

  const appendAttachmentContext = (content: string, files: ChatAttachment[]) => {
    if (files.length === 0) return content;

    const contextLines = files.map((file) => {
      if (file.type === "knowledge_base") {
        const datasetLine = `用户本轮已选择知识库，dataset_id：${file.url}。你必须在本轮回复前调用 search_knowledge_base 工具检索后再作答，不得跳过。dataset_ids 请传纯 ID 或单引号列表，例如 ['${file.url}']；禁止使用双引号 JSON 如 ["${file.url}"]。`;
        return buildKnowledgeBaseAttachmentHint(datasetLine);
      }
      if (file.type === "memory") {
        const meta = file.memoryMeta || [];
        const memoryContextLines = meta.map((memory, index) => {
          const dateStr = memory.last_active
            ? new Date(memory.last_active * 1000).toLocaleDateString("zh-CN")
            : "";
          const dateInfo = dateStr ? `【${dateStr}】` : "";
          return `${index + 1}. ${dateInfo}${memory.summary}`;
        });
        return `💡 以下引用的是历史记忆，供参考：\n\n${memoryContextLines.join("\n\n")}`;
      }
      const path = getServerAttachmentPath(file);
      if (file.type === "skill") {
        return buildSkillAttachmentHint(file, path);
      }
      if (isImageAttachment(file)) {
        return buildImageAttachmentHint(file, path);
      }
      if (file.type === "local_file") {
        return `用户本轮已挂载服务器本地文件：${file.filename}，其真实的绝对路径是：${path}。你可以直接通过系统级执行工具访问或读取此绝对路径的资料以解答用户的问题。`;
      }
      if (file.type === "local_dir") {
        return `用户本轮已挂载服务器本地目录：${file.filename}，其真实的绝对路径是：${path}。你可以直接通过系统级执行工具访问、遍历或检索此绝对路径目录下的资料以解答用户的问题。`;
      }
      return `用户本轮已上传文件附件：${file.filename}，其安全托管后的服务器绝对路径是：${path}。`;
    });

    const contextBlock = contextLines.filter(Boolean).join("\n\n");
    const userPart = (content || "").trim();
    if (!contextBlock) return userPart;
    if (!userPart) return `${USER_MESSAGE_CONTEXT_DIVIDER}${contextBlock}`;
    return `${userPart}${USER_MESSAGE_CONTEXT_DIVIDER}${contextBlock}`;
  };

  return {
    appendAttachmentContext,
    buildImageAttachmentHint,
    buildSkillAttachmentHint,
  };
};
