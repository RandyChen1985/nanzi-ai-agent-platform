/**
 * 技能广场的数据结构。
 *
 * 数据来自后端 `app/services/ai/skill_gallery/catalog.json`（代码资产），
 * 与平台技能（`types/skill.ts`）不是一回事：这里是**只读介绍**，没有文件、
 * 没有启用状态、也不落库。安装靠复制提示词交给会话里的智能体执行。
 */
export interface SkillGalleryCard {
  id: string
  name: string
  slug: string
  summary: string
  /**
   * 能力要点。列表接口为了瘦身不下发（详情才有），因此这里是可选的。
   */
  highlights?: string[]
  category: string
  tags: string[]
  icon: string
  official: boolean
  author: string
  /** 以下来源/许可字段只在详情接口下发，列表为了瘦身裁掉了 */
  repository?: string
  repository_url?: string
  source_url?: string
  license?: string
  version: string
  /** 非交互安装命令（两个 -y 缺一不可，否则沙箱里会挂起等输入） */
  install_command: string
  /** 给智能体用的整段提示词：安装到个人技能库 */
  install_prompt: string
  /**
   * 给智能体用的整段提示词：安装到平台全局技能库。
   *
   * 仅持有 `element:skills:admin` 的用户才会拿到；其余用户后端直接不返回该字段
   * （提示词里写着「我确认有管理员权限」，不能交给无权限的人），因此这里是可选的。
   */
  install_prompt_global?: string
  updated_at?: string
  intro_doc?: string
  /**
   * 已安装状态：个人库 / 平台库 / 未安装。
   *
   * 只按技能 ID（目录名）比对，判定的是「有没有」，不是「是不是最新版」。
   * 同一 ID 两处都有时后端返回 personal——个人覆盖公共是平台既有优先级。
   */
  installed: SkillInstalledScope
  /**
   * 库内版本：读已装技能 SKILL.md 的 frontmatter `version`。
   *
   * 第三方技能大多不写 version，此时为空串——前端显示「未知」，
   * 不拿目录名冒充版本号。未安装时同样为空串。
   */
  installed_version?: string
}

export type SkillInstalledScope = 'personal' | 'global' | null

export interface SkillGalleryDetail extends SkillGalleryCard {
  /** 详情页介绍正文（Markdown 源串，我们自己撰写，不是第三方 SKILL.md 原文） */
  intro: string
}

export interface SkillGalleryList {
  skills: SkillGalleryCard[]
  categories: string[]
  /** 目录口径说明：只做索引介绍、不复制第三方正文 */
  source_note: string
}
