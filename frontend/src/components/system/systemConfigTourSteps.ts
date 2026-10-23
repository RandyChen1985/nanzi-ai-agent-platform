/**
 * 系统配置功能引导的步骤定义。
 *
 * 顺序刻意按「参数配置」左侧分组导航走（常规 → 上下文 → 模型 → 元数据 →
 * 知识库 → 沙箱 → 邮件），让引导本身就在教「左边这列是分组导航」。
 *
 * `tab` / `group` 不是锚点，而是"进入这一步之前要先切到哪里"：
 * 系统配置的主 tab 与配置分组都是按需渲染的，不切过去锚点根本不存在。
 */

export type SysTourTab =
  | 'models'
  | 'tools'
  | 'configs'
  | 'branding'
  | 'diagnostics'
  | 'logs'
  | 'langfuse'

export interface SystemConfigTourStep {
  /** 稳定标识，用于断点与排查 */
  id: string
  /** 目标元素的 data-tour 值 */
  selector: string
  title: string
  body: string
  /** 进入该步前切换到的主 tab */
  tab?: SysTourTab
  /** 进入该步前切换到的参数配置分组 */
  group?: string
}

/** 文案改版时 +1：全局完成态版本不等即视为未完成，全员重播一次 */
export const SYSTEM_CONFIG_TOUR_VERSION = 1

export const SYSTEM_CONFIG_TOUR_STEPS: SystemConfigTourStep[] = [
  {
    id: 'sys-tabs',
    selector: '[data-tour="sys-tabs"]',
    title: '七个模块',
    body: '系统配置分七块。这次只细讲「模型管理」和「参数配置」，其余几块等用到再看。',
    tab: 'models',
  },
  {
    id: 'model-add',
    selector: '[data-tour="model-add"]',
    title: '先配模型',
    body: '新部署第一件事：点这里添加至少一个模型并启用，否则对话和 Embed 都没法用。',
  },
  {
    id: 'model-row-actions',
    selector: '[data-tour="model-row-actions"]',
    title: '测试连接',
    body: '保存后点「测试连接」，跑通再启用。密钥或地址错了，这里会显示完整请求过程。',
  },
  {
    id: 'config-toolbar',
    selector: '[data-tour="config-toolbar"]',
    title: '改完要保存',
    body: '所有参数改完点这里保存（⌘S / Ctrl+S）。左侧分组上的黄点表示该组有未保存的改动。',
    tab: 'configs',
  },
  {
    id: 'config-app_public_url',
    selector: '[data-tour="config-app_public_url"]',
    title: '对外访问地址',
    body: '填平台的公网地址（协议 + 域名 + 前缀）。下载链接、重置密码邮件、MCP 公网地址、报表深链都由它拼出来。',
    group: 'general',
  },
  {
    id: 'config-agent_context_max_tokens',
    selector: '[data-tour="config-agent_context_max_tokens"]',
    title: '上下文预算',
    body: '发给模型的上下文上限，默认 64k。超了就压缩早期对话或截断，按你的模型窗口调。',
    group: 'agent_context',
  },
  {
    id: 'config-llm_model_name',
    selector: '[data-tour="config-llm_model_name"]',
    title: '默认模型',
    body: '全局默认模型。选一个已启用、且在你的网络环境里能连通的。',
    group: 'agent',
  },
  {
    id: 'config-multimodal_model_name',
    selector: '[data-tour="config-multimodal_model_name"]',
    title: '多模态模型（可选）',
    body: '当前模型不支持识图时，用这个模型把图片解析成文字。不配也能用，只是传图片会提示不支持。',
    group: 'agent',
  },
  {
    id: 'config-embed_api_url',
    selector: '[data-tour="config-embed_api_url"]',
    title: '向量服务地址',
    body: '元数据描述与记忆摘要的向量计算都走这里。可点上方「从模型管理加载」自动填。',
    group: 'agent',
  },
  {
    id: 'config-metadata_provider',
    selector: '[data-tour="config-metadata_provider"]',
    title: '元数据来源',
    body: 'local 用本地手工字典；ragflow 走语义检索自动取描述。只有用了 RAGFlow 才选后者。',
    group: 'metadata',
  },
  {
    id: 'config-knowledge_base_enabled',
    selector: '[data-tour="config-knowledge_base_enabled"]',
    title: '知识库总开关（可选）',
    body: '用知识库才打开。关掉后下面的 RAGFlow 参数会隐藏，知识库管理与检索工具也一起禁用。',
    group: 'knowledge',
  },
  {
    id: 'config-sandbox_policy',
    selector: '[data-tour="config-sandbox_policy"]',
    title: '沙箱策略',
    body: 'local 直接在宿主机跑；docker / k8s 隔离执行；ssh 连远程机；e2b 走云端。按部署形态选，下面的参数会跟着变。',
    group: 'sandbox',
  },
  {
    id: 'config-email_service_enabled',
    selector: '[data-tour="config-email_service_enabled"]',
    title: '系统邮件（可选）',
    body: '打开后配置 SMTP，用来发审核通知、重置密码等系统邮件。不配不影响主流程。',
    group: 'email',
  },
]

export const SYSTEM_CONFIG_TOUR_TOTAL = SYSTEM_CONFIG_TOUR_STEPS.length
