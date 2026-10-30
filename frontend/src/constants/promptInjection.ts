import type { AgentType } from '../api/agent';

/**
 * 智能体提示词编辑器的「平台注入说明」数据源。
 *
 * 这里只描述平台会自动拼进 system prompt 的内容，**不对外暴露占位符机制**：
 * 数据集目录一类的动态内容由后端静默注入，作者不需要在提示词里写任何 token。
 *
 * 新增注入块时同步更新本文件与 `architech/design/chat/PROMPT_LAYERS.md`，
 * 避免说明面板和运行时行为漂移。
 */

export type InjectionPillKind = 'always' | 'cond';

export interface InjectionItem {
  /** 注入块的展示名，如 `<USER_PROFILE>` */
  name: string;
  /** 右侧状态标签：恒定注入 / 条件注入 */
  pill?: { label: string; kind: InjectionPillKind };
  /** 说明正文；反引号包裹的片段按代码样式渲染 */
  desc: string;
  /** 仅在这些智能体类型下出现；不填表示三种类型都出现 */
  types?: AgentType[];
}

export interface InjectionGroup {
  key: string;
  title: string;
  /** 收起时显示的一行摘要 */
  summary: string;
  items: InjectionItem[];
}

export const INJECTION_GROUPS: InjectionGroup[] = [
  {
    key: 'platform',
    title: '平台规则 · 每轮必带',
    summary: '全局守则四段 + 按本工具追加的动态能力段',
    items: [
      {
        name: '[品牌名 · 全局守则]',
        pill: { label: '恒定', kind: 'always' },
        desc: '安全、保密、反幻觉、语言与表达、图示规范、工具调用通则。四段固定文案，任何智能体都带，且永远排在最前。',
      },
      {
        name: '工具与敏感操作段',
        pill: { label: '随工具变化', kind: 'cond' },
        desc: '按本轮绑定的工具追加：可用工具清单、敏感工具确认规则、MCP 别名、快速切换建议。',
      },
      {
        name: '记忆与时间对照',
        pill: { label: '恒定', kind: 'always' },
        desc: '告诉模型哪些信息该查记忆、哪些信息该看时间锚点，避免它自己编一套。',
      },
    ],
  },
  {
    key: 'identity',
    title: '身份与授权 · 每轮必带',
    summary: '<USER_PROFILE> 只读画像 + 可访问资源目录',
    items: [
      {
        name: '<USER_PROFILE>',
        pill: { label: '恒定', kind: 'always' },
        desc: '只读画像：User ID、账号、姓名、邮箱、部门与部门编码、角色、账号状态、创建时间、备注。自由文本截断到 300 字，只能用于消歧与称呼，不参与鉴权。',
      },
      {
        name: '可访问资源目录',
        pill: { label: '恒定', kind: 'always' },
        desc: '当前用户有权访问的知识库与数据集名称、数量，与你正在编辑的这个智能体是否绑定无关。',
      },
    ],
  },
  {
    key: 'memory',
    title: '记忆 · 按轮次类型裁剪',
    summary: 'LTM / 跨会话检索提示 / 预加载摘要',
    items: [
      {
        name: '[Memory Profile]',
        pill: { label: '所有轮次', kind: 'always' },
        desc: 'Redis 里的长期记忆：稳定事实与偏好，让模型不必反复追问口径。',
      },
      {
        name: '[跨会话记忆检索]',
        pill: { label: '通用 / 上下文轮次', kind: 'cond' },
        desc: '提示何时该调用记忆检索工具；数据查询与知识库轮次不注入，避免与检索流程冲突。',
      },
      {
        name: '[System Preloaded Memories]',
        pill: { label: '命中回忆意图', kind: 'cond' },
        desc: '预先取好的每日摘要与会话摘要。典型场景：「上次我们聊了啥」「昨天做了什么」。',
      },
    ],
  },
  {
    key: 'skills',
    title: '技能',
    summary: '已加载技能摘要 / 未挂载时的发现提示',
    items: [
      {
        name: '[Active Skills Loaded]',
        pill: { label: '已挂载技能', kind: 'cond' },
        desc: '只给 frontmatter 摘要，不含 SKILL.md 全文；模型必须先 `read_skill_instruction` 才能执行。',
      },
      {
        name: '[Skill Discovery Hint]',
        pill: { label: '未挂载任何技能', kind: 'cond' },
        desc: '告知技能库目录与发现方式，避免模型凭空回答或声称自己具备某能力。',
      },
    ],
  },
  {
    key: 'routing',
    title: '路由与协作',
    summary: '路由快照 / 子智能体通讯录 / 任务约束',
    items: [
      {
        name: '本轮执行上下文',
        pill: { label: '有路由快照', kind: 'cond' },
        desc: '请求来源、建议能力、语义意图、与上一轮的关系、可复用结果决策、知识库目录匹配范围；并声明「不是权限凭证」。',
      },
      {
        name: '子智能体通讯录',
        pill: { label: '主助手 / 绑了 sub_agent_call', kind: 'cond' },
        desc: '可委派智能体的标识、职责与能力，供 `sub_agent_call` 传 `agent_name` 使用。',
      },
      {
        name: '<task_constraints>',
        pill: { label: '定时 / 订阅任务', kind: 'cond' },
        desc: '任务创建者设定的约束，位于 system 末段；除安全规则与平台强制项外，冲突时以它为准。',
      },
    ],
  },
  {
    key: 'runtime',
    title: '运行时',
    summary: '历史边界 / 注入上下文 / 时间锚点 / 工作区路径',
    items: [
      {
        name: '历史边界提示',
        pill: { label: '恒定', kind: 'always' },
        desc: '区分「历史只是背景」与「最后一条消息才是任务」，防止模型复跑旧任务。',
      },
      {
        name: '# Session Runtime Context',
        pill: { label: 'Embed / 调试注入', kind: 'cond' },
        desc: '独立 system 消息，携带 `injected_context` 与设备类型；移动端会自动追加「禁止宽表格、多分段」等排版规则。',
      },
      {
        name: '【当前时间锚点】',
        pill: { label: '含相对时间问题', kind: 'cond' },
        desc: '今天 / 近 7 天 / 本月 / 上季度换算成确定日期，避免臆测年份月份。',
      },
      {
        name: '工作区与沙箱路径',
        pill: { label: '有文件 / Shell 工具', kind: 'cond' },
        desc: '会话目录、文档目录、只读公共目录的真实路径，以及沙箱挂载边界与临时文件落点。',
      },
    ],
  },
  {
    key: 'executor',
    title: '执行器专属',
    summary: '数据集目录 / SQL 护栏 / 知识库检索结果',
    items: [
      {
        name: '数据集目录',
        pill: { label: '自动带上', kind: 'always' },
        desc: '按**当前用户权限**生成的数据集清单（名称、描述、表名与术语），平台会自动拼在这段提示词的最前面，**无需在提示词里做任何事**。绑定固定数据集的智能体不注入全量目录，避免模型去查未授权数据集。',
        types: ['CHATBI'],
      },
      {
        name: 'SQL 护栏与计划',
        pill: { label: '数据查询轮次', kind: 'cond' },
        desc: '全局护栏、分页语法、是否强制先出 SQL 计划；`[DATA_QUERY_STATE]` 声明本轮是否需要新数据、能否复用上一轮结果、下一步允许的动作。',
        types: ['CHATBI'],
      },
      {
        name: '【知识库检索结果】',
        pill: { label: '知识库轮次', kind: 'cond' },
        desc: '预检索内容 + 强制 `[ID:n]` 引用规则；引用序号缺失或编造不存在的序号即视为违规。',
        types: ['KNOWLEDGE_BASE'],
      },
    ],
  },
];

export type StackTag = 'stable' | 'dynamic' | 'you' | 'boundary';

export interface StackItem {
  label: string;
  tag: StackTag;
  types?: AgentType[];
}

/** 拼装顺序：越靠前约束力越强；缓存边界以下属于每轮变化的动态内容。 */
export const PROMPT_STACK: StackItem[] = [
  { label: '[品牌名 · 全局守则] + 动态能力段', tag: 'stable' },
  { label: '<USER_PROFILE> 用户画像', tag: 'stable' },
  { label: '你在这里编辑的内容（数据集目录会静默拼在它最前）', tag: 'you' },
  { label: '缓存边界（cache reorder 开启时）', tag: 'boundary' },
  { label: '本轮执行上下文 · 可访问资源目录', tag: 'dynamic' },
  { label: '记忆三件套（LTM / 跨会话 / 预加载）', tag: 'dynamic' },
  { label: '技能摘要 / 发现提示', tag: 'dynamic' },
  { label: '执行器护栏 · [DATA_QUERY_STATE]', tag: 'dynamic', types: ['CHATBI'] },
  { label: '知识库预检索结果与引用规则', tag: 'dynamic', types: ['KNOWLEDGE_BASE'] },
  { label: '<task_constraints> 任务约束（末段最高优先级）', tag: 'dynamic' },
];

export const STACK_FOOTNOTE =
  '顺序即约束力：越靠前越权威（平台规则 → 用户画像 → 你的内容）；记忆、技能、路由快照只算辅助事实。你写的内容落在 stable 前缀末尾，改动它会击穿前缀缓存，请尽量保持首段稳定。';
