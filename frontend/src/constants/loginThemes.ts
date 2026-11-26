export type LoginThemeId = 'f1' | 'f2' | 'f3' | 'f4' | 'g' | 'h' | 'i' | 'j'

export type LoginThemeGroup = 'astrolabe' | 'editorial'

export type LoginPanelVariant =
  | 'split-mist'     // F1：穿界雾白侧舱（透明叠在 Canvas 亮半区）
  | 'optical-bezel'  // F2：双层光学玻璃外鞘 + 冰雾冷白内芯
  | 'titanium-card'  // F3：低眩光钛银灰亮卡
  | 'daylight-card'  // F4：全亮色清透白卡
  | 'ink-wing'       // G：暖象牙白磨砂侧翼
  | 'lab-card'       // H：冷白高对比顶部黑线卡
  | 'fluted-wing'    // I：长虹压花玻璃羽化过渡侧翼
  | 'hud-bezel'      // J：翡翠微光外鞘 + 暖灰岩低眩光卡

export type LoginSubmitVariant = 'navy' | 'blue' | 'ink' | 'hud'

export interface LoginThemeMeta {
  id: LoginThemeId
  code: string
  name: string
  group: LoginThemeGroup
  groupLabel: string
  tagline: string
  desc: string
  isLightStage: boolean
  panelVariant: LoginPanelVariant
  submitVariant: LoginSubmitVariant
  eyebrow: string
  headlineLines: [string, string]
  description: string
  footerItems: string[]
  /** 个人中心微缩预览卡用色板 */
  preview: {
    bg: string
    accent: string
    secondary: string
    panelBg: string
    isLight: boolean
  }
}

export const DEFAULT_LOGIN_THEME: LoginThemeId = 'f1'

export const LOGIN_THEME_STORAGE_KEY = 'nanzi_login_theme'

export const VALID_LOGIN_THEMES: readonly LoginThemeId[] = [
  'f1',
  'f2',
  'f3',
  'f4',
  'g',
  'h',
  'i',
  'j',
] as const

export const LOGIN_THEMES: readonly LoginThemeMeta[] = [
  {
    id: 'f1',
    code: 'F1',
    name: '双面穿界星盘',
    group: 'astrolabe',
    groupLabel: '精密星盘系',
    tagline: '星盘刻度穿透昼夜两面，默认推荐',
    desc: '深邃天文游标刻度直接穿过暗亮分界线，在右侧雾白登录区化作淡灰制图弧线同步旋转。',
    isLightStage: false,
    panelVariant: 'split-mist',
    submitVariant: 'navy',
    eyebrow: 'AUTONOMOUS AGENT OBSERVATORY',
    headlineLines: ['精密运转的', '企业智能体中枢'],
    description: '在可验证的数据链路与受控权限边界内，让智能体如天文仪器般稳定协作。',
    footerItems: ['AGENTSCOPE RUNTIME', 'MCP · RAG · CHATBI'],
    preview: {
      bg: 'linear-gradient(135deg, #070c17 0%, #0d1a33 100%)',
      accent: '#38bdf8',
      secondary: '#93c5fd',
      panelBg: '#eff3f9',
      isLight: false,
    },
  },
  {
    id: 'f2',
    code: 'F2',
    name: '冰雾外鞘星盘',
    group: 'astrolabe',
    groupLabel: '精密星盘系',
    tagline: '双层光学玻璃外鞘 + 冷雾磨砂卡',
    desc: '经典深空星盘配半透明光学玻璃外鞘与冰雾磨砂内芯，像精密仪盘里镶嵌的冷光屏。',
    isLightStage: false,
    panelVariant: 'optical-bezel',
    submitVariant: 'navy',
    eyebrow: 'OPTICAL ASTROLABE CONSOLE',
    headlineLines: ['把复杂调度，', '收束进一枚精密仪盘'],
    description: '统一接入模型路由、企业知识库与安全凭据中心，让每一次对话落到真实业务。',
    footerItems: ['MULTI-AGENT ORCHESTRATION', 'ENTERPRISE RBAC'],
    preview: {
      bg: 'linear-gradient(135deg, #070b14 0%, #10203a 100%)',
      accent: '#38bdf8',
      secondary: '#60a5fa',
      panelBg: '#e8eff7',
      isLight: false,
    },
  },
  {
    id: 'f3',
    code: 'F3',
    name: '引力卫星星环',
    group: 'astrolabe',
    groupLabel: '精密星盘系',
    tagline: '四轨能力卫星公转 + 钛银雾灰卡',
    desc: 'Router、ChatBI、RAG、MCP 四颗能力卫星绕核公转可点选锁星，配低眩光钛银灰登录卡。',
    isLightStage: false,
    panelVariant: 'titanium-card',
    submitVariant: 'navy',
    eyebrow: 'ORBITAL TELEMETRY NETWORK',
    headlineLines: ['四轨协同，', '驱动真实业务闭环'],
    description: '多智能体编排、自然语言查数、溯源知识库与 MCP 工具链协同运转。',
    footerItems: ['ORBITAL MODULES', '4 SATELLITES ONLINE'],
    preview: {
      bg: 'linear-gradient(135deg, #080d17 0%, #101e36 100%)',
      accent: '#38bdf8',
      secondary: '#7dd3fc',
      panelBg: '#e2e9f3',
      isLight: false,
    },
  },
  {
    id: 'f4',
    code: 'F4',
    name: '白盘天文台',
    group: 'astrolabe',
    groupLabel: '精密星盘系',
    tagline: '全亮色瑞士精密机械表盘',
    desc: '清透浅冷灰画布上旋转钢蓝精密游标刻度，全亮色统一通透，与工作台无缝衔接。',
    isLightStage: true,
    panelVariant: 'daylight-card',
    submitVariant: 'blue',
    eyebrow: 'DAYLIGHT CHRONOMETER EDITION',
    headlineLines: ['如精密时计般', '稳定可控的智能体中枢'],
    description: '以瑞士白盘机械表盘的精密克制重塑科技感，面向真实生产工作流的清爽入口。',
    footerItems: ['DAYLIGHT ASTROLABE', 'MCP · RAG · CHATBI'],
    preview: {
      bg: 'linear-gradient(135deg, #edf2f8 0%, #e1e9f4 100%)',
      accent: '#2563eb',
      secondary: '#64748b',
      panelBg: '#ffffff',
      isLight: true,
    },
  },
  {
    id: 'g',
    code: 'G',
    name: '东方山脊光影',
    group: 'editorial',
    groupLabel: '多元流派系',
    tagline: '宣纸暖灰 + 建筑水墨山脊流场',
    desc: '暖白宣纸底色与五层淡墨算力山脊起伏呼吸，克制静谧、如现代建筑美术馆画册。',
    isLightStage: true,
    panelVariant: 'ink-wing',
    submitVariant: 'ink',
    eyebrow: 'CALM ARCHITECTURE · 静谧算力',
    headlineLines: ['大道至简，', '让智能融入日常工作'],
    description: '退去浮躁霓虹与噪点，以建筑空间般的留白与层次承载多智能体协同与知识洞察。',
    footerItems: ['QUIET INTELLIGENCE', 'AGENTS · KNOWLEDGE · TOOLS'],
    preview: {
      bg: 'linear-gradient(135deg, #f7f5f0 0%, #e9e4d8 100%)',
      accent: '#364152',
      secondary: '#d9b382',
      panelBg: '#fbfaf7',
      isLight: true,
    },
  },
  {
    id: 'h',
    code: 'H',
    name: '物理波纹干涉',
    group: 'editorial',
    groupLabel: '多元流派系',
    tagline: '全亮色双波源实时摩尔纹干涉场',
    desc: '鼠标驱动双波源实时叠加出精密物理摩尔纹，纯靠数学几何美感呈现极客科技感。',
    isLightStage: true,
    panelVariant: 'lab-card',
    submitVariant: 'ink',
    eyebrow: 'DUAL-SOURCE MOIRÉ · 物理干涉场',
    headlineLines: ['当意图与工具', '同频共振'],
    description: '移动鼠标实时改变第二波源相位，以纯粹的物理几何美感表达智能体与业务系统的共振。',
    footerItems: ['WAVE INTERFERENCE', 'REALTIME PHASE SYNC'],
    preview: {
      bg: 'linear-gradient(135deg, #f1f4f8 0%, #e5ebf3 100%)',
      accent: '#2563eb',
      secondary: '#0f172a',
      panelBg: '#ffffff',
      isLight: true,
    },
  },
  {
    id: 'i',
    code: 'I',
    name: '长虹压花玻璃',
    group: 'editorial',
    groupLabel: '多元流派系',
    tagline: '美术馆竖纹玻璃折射 + 羽化过渡',
    desc: '流动极光色柱透过建筑长虹压花玻璃折射出丝绸棱纹，经羽化带柔和过渡到雾白登录区。',
    isLightStage: false,
    panelVariant: 'fluted-wing',
    submitVariant: 'navy',
    eyebrow: 'FLUTED ARCHITECTURAL GLASS',
    headlineLines: ['光影穿过秩序，', '连接每一项智能能力'],
    description: '灵感源自现代美术馆长虹压花玻璃幕墙，柔和光影经由柱面棱镜过滤后归于清晰秩序。',
    footerItems: ['OPTICAL PRISM', 'REEDED GLASS EDITION'],
    preview: {
      bg: 'linear-gradient(135deg, #0d1b38 0%, #0ea5e9 65%, #1e3a8a 100%)',
      accent: '#38bdf8',
      secondary: '#f59e0b',
      panelBg: '#f4f7fc',
      isLight: false,
    },
  },
  {
    id: 'j',
    code: 'J',
    name: '航电精密地平仪',
    group: 'editorial',
    groupLabel: '多元流派系',
    tagline: '翡翠绿航电 HUD + 暖灰岩低眩光卡',
    desc: '跳出蓝紫体系，采用深橄榄黑航空姿态地平仪与航向罗盘，配翡翠微光外鞘与暖灰岩卡。',
    isLightStage: false,
    panelVariant: 'hud-bezel',
    submitVariant: 'hud',
    eyebrow: 'AVIONICS PRECISION DECK',
    headlineLines: ['全链路稳态掌控，', '面向生产级智能体'],
    description: '以航空级稳态与可审计标准构建企业智能体底座，全链路权限隔离、安全可控。',
    footerItems: ['FLIGHT-GRADE STABILITY', 'FULL AUDIT TRAIL'],
    preview: {
      bg: 'linear-gradient(135deg, #0b110f 0%, #132820 100%)',
      accent: '#34d399',
      secondary: '#f59e0b',
      panelBg: '#ecefeb',
      isLight: false,
    },
  },
] as const

export const LOGIN_THEME_MAP: Record<LoginThemeId, LoginThemeMeta> = LOGIN_THEMES.reduce(
  (acc, item) => {
    acc[item.id] = item
    return acc
  },
  {} as Record<LoginThemeId, LoginThemeMeta>,
)

export function isLoginThemeId(value: unknown): value is LoginThemeId {
  return typeof value === 'string' && (VALID_LOGIN_THEMES as readonly string[]).includes(value)
}

export function normalizeLoginTheme(value: unknown): LoginThemeId {
  if (typeof value !== 'string') return DEFAULT_LOGIN_THEME
  const normalized = value.trim().toLowerCase()
  return isLoginThemeId(normalized) ? normalized : DEFAULT_LOGIN_THEME
}
