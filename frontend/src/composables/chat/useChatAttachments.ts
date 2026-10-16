import { getServerAttachmentPath, isImageAttachment } from "@/utils/attachmentImages";
import { forbiddenSkillReason, isSkillForbidden } from "@/utils/skillForbidden";

export const USER_MESSAGE_CONTEXT_DIVIDER = "\n\n---\n\n";

interface AttachmentSkillMeta {
  name?: string;
  description?: string;
  /** 后端 /api/portal/skills 下发的禁用标记（个人技能不带该字段） */
  forbidden?: boolean | null;
}

interface AttachmentMemoryMeta {
  last_active?: number;
  summary?: string;
}

export interface ChatAttachment {
  type?: string;
  url: string;
  filename: string;
  scope?: string;
  /** 禁用标记也可能直接挂在附件上（历史消息 / 旧缓存两条来源都要认） */
  forbidden?: boolean | null;
  skillMeta?: AttachmentSkillMeta;
  memoryMeta?: AttachmentMemoryMeta[];
}

interface ChatAttachmentOptions {
  buildKnowledgeBaseAttachmentHint: (datasetIdLine: string) => string;
  buildDatasetAttachmentHint?: (datasetIdLine: string) => string;
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
  buildDatasetAttachmentHint,
}: ChatAttachmentOptions) => {
  const buildImageAttachmentHint = (file: ChatAttachment, path: string) => {
    if (file.type === "local_file") {
      return `用户本轮已从服务器挂载图片：${file.filename}，该图片已作为视觉多模态输入随消息一并发送（源路径：${path}）。`;
    }
    return `用户本轮已上传图片：${file.filename}，该图片已作为视觉多模态输入随消息一并发送（托管路径：${path}）。`;
  };

  const buildSkillAttachmentHint = (file: ChatAttachment) => {
    const skillName = file.filename.replace(" (技能)", "");
    const meta = file.skillMeta;
    const metaParts: string[] = [];
    if (meta?.name) metaParts.push(`name: ${meta.name}`);
    if (meta?.description) metaParts.push(`description: ${meta.description}`);
    const metaText = metaParts.length > 0 ? metaParts.join(", ") : "";
    const skillId = file.url || "";
    const idText = skillId ? `（技能 ID：${skillId}）` : "";
    // 与后端 `app/utils/skill_metadata.build_skill_attachment_hint` 是同一份文案口径：
    // 不再给 SKILL.md 的绝对路径（原因见 attachmentImages.getServerAttachmentPath），
    // 技能附带的脚本/资源一律走 read_skill_instruction 的资源清单 + path 参数按需读取。
    let hint =
      `用户本轮已调用生态技能工作流：${skillName}${idText}。` +
      "其 SKILL.md 正文已随本轮上下文预载，本轮直接按预载指令执行，无需再读取技能文件；" +
      "如需读取该技能附带的脚本或资源（references/、scripts/ 等），请调用 read_skill_instruction " +
      '获取该技能的资源清单，再用 path 参数按需读取（例如 path="references/rules.md"）；' +
      "技能目录布局不保证可由技能 ID 推导，不要猜测固定路径。";
    if (metaText) {
      hint += `\nskills meta 为：${metaText}`;
    }
    return hint;
  };

  const buildForbiddenSkillAttachmentHint = (file: ChatAttachment) => {
    const skillName = file.filename.replace(" (技能)", "") || file.url;
    const reason = forbiddenSkillReason(
      isSkillForbidden(file.skillMeta) ? file.skillMeta : file,
      "使用",
    );
    // 与后端 utils/skill_metadata.sanitize_forbidden_skill_context 的措辞同源：
    // 明确「不可用、不可读」，且**不带任何绝对路径**——带路径就是在教模型去读被禁技能的正文。
    return `用户本轮挂载的技能「${skillName}」：${reason}；其技能描述文件不可读取，禁止尝试读取或检索该技能的任何文件。`;
  };

  const appendAttachmentContext = (content: string, files: ChatAttachment[]) => {
    if (files.length === 0) return content;

    const contextLines = files.map((file) => {
      if (file.type === "knowledge_base") {
        const datasetLine = `用户本轮已选择知识库，dataset_id：${file.url}。你必须在本轮回复前调用 search_knowledge_base 工具检索后再作答，不得跳过。dataset_ids 请传纯 ID 或单引号列表，例如 ['${file.url}']；禁止使用双引号 JSON 如 ["${file.url}"]。`;
        return buildKnowledgeBaseAttachmentHint(datasetLine);
      }
      if (file.type === "metadata_dataset") {
        const datasetLine = `用户本轮已选择数据集/数据源，dataset_id：${file.url}。本次提问为数据查询与分析，须优先由具数据查询能力的专家处理，且查询只能在已选数据集限定范围内进行。`;
        return buildDatasetAttachmentHint ? buildDatasetAttachmentHint(datasetLine) : datasetLine;
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
      if (file.type === "skill") {
        // 被禁技能：不给任何位置信息，改给明确的不可用说明（后端还会在写历史前再清洗一次）
        if (isSkillForbidden(file) || isSkillForbidden(file.skillMeta)) {
          return buildForbiddenSkillAttachmentHint(file);
        }
        return buildSkillAttachmentHint(file);
      }
      const path = getServerAttachmentPath(file);
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
    buildForbiddenSkillAttachmentHint,
  };
};
