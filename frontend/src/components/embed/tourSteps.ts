/**
 * EmbedChat 功能引导的步骤定义。
 *
 * 锚点统一用 `data-tour="..."`，不去碰元素 id / class：
 * 布局改版时只要锚点还在，引导不受影响。步骤顺序即播放顺序，
 * `step` 一律是 0 基索引，8 表示已完成。
 */

export type TourPlacement = 'auto' | 'top' | 'bottom'

export interface TourStep {
  /** 稳定标识，用于断点与埋点 */
  id: string
  /** 目标元素的 data-tour 值 */
  selector: string
  title: string
  body: string
  placement?: TourPlacement
  /** 该步需要先展开「上下文用量」弹层（沙箱区块） */
  opensContextPanel?: boolean
}

export const TOUR_VERSION = 1

export const TOUR_STEPS: TourStep[] = [
  {
    id: 'header-status',
    selector: '[data-tour="header-status"]',
    title: '智能体状态',
    body: '这里显示智能体是否在线；有会话在跑时会出现「进行中的会话」，点开可以切换查看。',
  },
  {
    id: 'history-toggle',
    selector: '[data-tour="history-toggle"]',
    title: '会话历史',
    body: '点这里展开或收起历史会话，可以搜索、切换和置顶；快捷键 ⌘H / Ctrl+H。',
  },
  {
    id: 'header-actions',
    selector: '[data-tour="header-actions"]',
    title: '全局工具',
    body: '快捷指令、帮助、设置、服务器浏览都在这里。设置里可以改主题、模型和功能开关。',
  },
  {
    id: 'welcome-cards',
    selector: '[data-tour="welcome-cards"]',
    title: '从这里开始',
    body: '不知道问什么，先点一张常用任务卡片；也可以直接在下面输入。',
  },
  {
    id: 'composer',
    selector: '[data-tour="composer"]',
    title: '输入你的问题',
    body: 'Enter 发送，Shift+Enter 换行。可以直接描述业务问题，不用写 SQL。',
  },
  {
    id: 'composer-plus',
    selector: '[data-tour="composer-plus"]',
    title: '挂载上下文',
    body: '点这里添加文件、技能、知识库、记忆或 MCP 工具，本轮对话会优先使用它们。',
  },
  {
    id: 'composer-pills',
    selector: '[data-tour="composer-pills"]',
    title: '状态浮标',
    body: '反幻觉校验和上下文用量都在这里；点开用量胶囊可以看 Token 构成与压缩记录。',
  },
  {
    id: 'sandbox-block',
    selector: '[data-tour="sandbox-block"]',
    title: '沙箱',
    body: '上下文弹层下半部分是沙箱：运行状态、终端入口、重启与停止；连续空闲会被自动回收。',
    opensContextPanel: true,
  },
  {
    id: 'composer-routing',
    selector: '[data-tour="composer-routing"]',
    title: '专家委派',
    body: '默认由主助手处理；点这里可以指定常驻专家，也可以在输入框用 @ 临时选专家或挂文件。',
  },
  {
    id: 'composer-approval',
    selector: '[data-tour="composer-approval"]',
    title: '模型与审批',
    body: '右侧可以切换模型；这里设置审批模式——高风险操作会先确认再执行。',
  },
]

/** 步数从数组派生，避免加步骤时忘了同步常量 */
export const TOUR_TOTAL_STEPS = TOUR_STEPS.length
