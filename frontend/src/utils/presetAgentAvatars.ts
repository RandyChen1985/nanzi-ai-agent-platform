/**
 * 预设 AI 智能体头像列表
 * 提供不同设计风格的现代化矢量头像（机器人、智慧光芒、极客科技、睿智学者等），
 * 并覆盖常见业务场景（研发 / 设计 / 写作 / 翻译 / 法务 / 财务 / 医疗 / 运维 / 营销 / 行政）。
 *
 * 注意：所有预设必须使用**短的静态资源路径**，禁止使用内联 `data:` URI
 * （把 SVG 源码 URL 编码后塞进链接）那种超长串。预设值会被写入 Redis 全局键并在
 * 全员会话中作为 `<img src>` 使用，而服务端对头像字段有长度上限
 * （见下方 MAX_AGENT_AVATAR_URL_LENGTH）：历史上有两个预设的内联数据链接超过
 * 2048 字符，点击后直接被后端 422 拒绝。
 *
 * 资源放在 `public/agent-avatars/` 而非 `src/assets/`：Vite 默认会把小于
 * `assetsInlineLimit`(4KB) 的资源内联成 base64 data URI，那样等于把问题换个形式
 * 带回来；public 目录下的文件按原样拷贝，URL 短且稳定。
 */
import defaultAgentAvatarUrl from "@/assets/nanzi-agent-avatar.svg";

/**
 * 头像 URL 长度上限，必须与后端 `AgentAvatarUpdate.avatar` 的 `max_length` 保持一致
 * （`app/api/portal/endpoints/portal_prefs.py`）。超长时在前端直接拦截并给出明确提示，
 * 避免用户只看到一条 422 校验错误。
 */
export const MAX_AGENT_AVATAR_URL_LENGTH = 2048;

export interface PresetAgentAvatar {
  id: string;
  name: string;
  url: string;
  isDefault?: boolean;
}

export const PRESET_AGENT_AVATARS: PresetAgentAvatar[] = [
  {
    id: "default",
    name: "官方默认",
    url: defaultAgentAvatarUrl,
    isDefault: true,
  },
  {
    id: "robot",
    name: "智能小机",
    url: "/agent-avatars/nanzi-agent-avatar-robot.svg",
  },
  {
    id: "spark",
    name: "智慧星火",
    url: "/agent-avatars/nanzi-agent-avatar-spark.svg",
  },
  {
    id: "cyber",
    name: "赛博科技",
    url: "/agent-avatars/nanzi-agent-avatar-cyber.svg",
  },
  {
    id: "scholar",
    name: "博识专家",
    url: "/agent-avatars/nanzi-agent-avatar-scholar.svg",
  },
  {
    id: "service",
    name: "智能客服",
    url: "/agent-avatars/nanzi-agent-avatar-service.svg",
  },
  {
    id: "analytics",
    name: "数据分析",
    url: "/agent-avatars/nanzi-agent-avatar-analytics.svg",
  },
  {
    id: "shield",
    name: "安全守护",
    url: "/agent-avatars/nanzi-agent-avatar-shield.svg",
  },
  {
    id: "code",
    name: "代码开发",
    url: "/agent-avatars/nanzi-agent-avatar-code.svg",
  },
  {
    id: "creative",
    name: "创意设计",
    url: "/agent-avatars/nanzi-agent-avatar-creative.svg",
  },
  {
    id: "writer",
    name: "文案写作",
    url: "/agent-avatars/nanzi-agent-avatar-writer.svg",
  },
  {
    id: "translate",
    name: "多语翻译",
    url: "/agent-avatars/nanzi-agent-avatar-translate.svg",
  },
  {
    id: "legal",
    name: "法务合规",
    url: "/agent-avatars/nanzi-agent-avatar-legal.svg",
  },
  {
    id: "finance",
    name: "财务金融",
    url: "/agent-avatars/nanzi-agent-avatar-finance.svg",
  },
  {
    id: "medical",
    name: "医疗健康",
    url: "/agent-avatars/nanzi-agent-avatar-medical.svg",
  },
  {
    id: "devops",
    name: "运维监控",
    url: "/agent-avatars/nanzi-agent-avatar-devops.svg",
  },
  {
    id: "marketing",
    name: "营销增长",
    url: "/agent-avatars/nanzi-agent-avatar-marketing.svg",
  },
  {
    id: "assistant",
    name: "日程助理",
    url: "/agent-avatars/nanzi-agent-avatar-assistant.svg",
  },
];

/** 头像 URL 是否超出服务端可接受长度（粘贴超长外链/内联 data URI 时提前拦截）。 */
export function isAgentAvatarUrlTooLong(url: string): boolean {
  return String(url || "").trim().length > MAX_AGENT_AVATAR_URL_LENGTH;
}

/** 预设头像资源所在的公共目录（供测试与文档引用，避免路径漂移）。 */
export const PRESET_AGENT_AVATAR_DIR = "/agent-avatars";
