/**
 * 侧栏图标目录。
 *
 * 图标全部取自 `@heroicons/vue` 的 24px outline 集（MIT），不再手写 SVG path：
 * 手写既容易画歪，也没法保证风格统一。这里只做「菜单 key → 图标组件 + 中文名」的映射，
 * 后端 `app/services/menu_seed.py` 的 `ALLOWED_ICON_KEYS` 必须与之完全一致，
 * 由 `tests/frontend/test_menu_icon_catalog_contract.py` 对账：
 * 新增图标要同时改这两处，否则菜单管理页能选中、侧栏却渲染成兜底图标。
 *
 * key 一旦发布就不要改名：数据库里的菜单行按 key 存图标。
 */

import type { Component } from 'vue'
import {
  HomeIcon, Bars3Icon, Squares2X2Icon, EllipsisHorizontalIcon,
  RectangleStackIcon, SquaresPlusIcon, GlobeAltIcon, ChatBubbleLeftRightIcon,
  ChatBubbleOvalLeftIcon, DocumentTextIcon, ChatBubbleBottomCenterTextIcon, SparklesIcon,
  CpuChipIcon, BeakerIcon, PuzzlePieceIcon, BoltIcon,
  RocketLaunchIcon, CircleStackIcon, TableCellsIcon, ChartBarIcon,
  PresentationChartLineIcon, ChartPieIcon, DocumentChartBarIcon, ChartBarSquareIcon,
  FunnelIcon, ArrowsRightLeftIcon, BookOpenIcon, DocumentIcon,
  ClipboardDocumentListIcon, FolderOpenIcon, ArchiveBoxIcon, BookmarkIcon,
  TagIcon, UsersIcon, UserIcon, UserCircleIcon,
  BuildingOffice2Icon,
  UserGroupIcon, KeyIcon, LockClosedIcon, ShieldCheckIcon,
  ClipboardDocumentCheckIcon, IdentificationIcon, Cog6ToothIcon, WrenchScrewdriverIcon,
  ServerStackIcon, CloudIcon, CodeBracketIcon, CommandLineIcon,
  BugAntIcon, LanguageIcon, LinkIcon, ArrowTopRightOnSquareIcon,
  SignalIcon, QueueListIcon, ClockIcon, CalendarDaysIcon,
  PlayCircleIcon, PauseCircleIcon, StarIcon, FlagIcon,
  InformationCircleIcon, LifebuoyIcon, BellIcon, MegaphoneIcon,
} from '@heroicons/vue/24/outline'

export const MENU_ICON_COMPONENTS: Record<string, Component> = {
  dashboard: HomeIcon,
  menu: Bars3Icon,
  widget: Squares2X2Icon,
  ellipsis: EllipsisHorizontalIcon,
  rectangle_stack: RectangleStackIcon,
  squares_plus: SquaresPlusIcon,
  globe: GlobeAltIcon,
  chat: ChatBubbleLeftRightIcon,
  chat_bubble_left_right: ChatBubbleOvalLeftIcon,
  chat_logs: DocumentTextIcon,
  prompts: ChatBubbleBottomCenterTextIcon,
  agent_mgmt: SparklesIcon,
  agent_debug: CpuChipIcon,
  playground: BeakerIcon,
  skills: PuzzlePieceIcon,
  bolt: BoltIcon,
  rocket: RocketLaunchIcon,
  data_source: CircleStackIcon,
  metadata: TableCellsIcon,
  chart_bar: ChartBarIcon,
  chart_line: PresentationChartLineIcon,
  chart_pie: ChartPieIcon,
  document_chart: DocumentChartBarIcon,
  token_stats: ChartBarSquareIcon,
  funnel: FunnelIcon,
  arrows_right_left: ArrowsRightLeftIcon,
  knowledge_base: BookOpenIcon,
  document_text: DocumentIcon,
  clipboard_list: ClipboardDocumentListIcon,
  folder: FolderOpenIcon,
  memory: ArchiveBoxIcon,
  bookmark: BookmarkIcon,
  tag: TagIcon,
  org: BuildingOffice2Icon,
  users: UsersIcon,
  user: UserIcon,
  user_circle: UserCircleIcon,
  roles: UserGroupIcon,
  key: KeyIcon,
  lock: LockClosedIcon,
  shield: ShieldCheckIcon,
  audit: ClipboardDocumentCheckIcon,
  identification: IdentificationIcon,
  system: Cog6ToothIcon,
  mcp_tools: WrenchScrewdriverIcon,
  mcp_service: ServerStackIcon,
  cloud: CloudIcon,
  code_bracket: CodeBracketIcon,
  command_line: CommandLineIcon,
  bug: BugAntIcon,
  language: LanguageIcon,
  link: LinkIcon,
  external_link: ArrowTopRightOnSquareIcon,
  signal: SignalIcon,
  tasks: QueueListIcon,
  clock: ClockIcon,
  calendar: CalendarDaysIcon,
  play: PlayCircleIcon,
  pause: PauseCircleIcon,
  star: StarIcon,
  flag: FlagIcon,
  info: InformationCircleIcon,
  lifebuoy: LifebuoyIcon,
  bell: BellIcon,
  megaphone: MegaphoneIcon,
}

export const MENU_ICON_KEYS = Object.keys(MENU_ICON_COMPONENTS).sort()

/**
 * 图标的中文名。
 *
 * 只服务菜单管理页的选择器：`chat_bubble_left_right` 这种 key 光看名字猜不出长什么样，
 * 配上中文名 + 实时预览才能一眼挑对。键集合必须与 `MENU_ICON_COMPONENTS` 完全一致。
 */
export const MENU_ICON_LABELS: Record<string, string> = {
  agent_debug: '智能体调试',
  agent_mgmt: '智能体',
  arrows_right_left: '同步',
  audit: '审计',
  bell: '通知',
  bolt: '自动化',
  bookmark: '收藏',
  bug: '问题',
  calendar: '日程',
  chart_bar: '柱状图',
  chart_line: '趋势',
  chart_pie: '占比',
  chat: '对话',
  chat_bubble_left_right: '对话气泡',
  chat_logs: '聊天记录',
  clipboard_list: '清单',
  clock: '时间',
  cloud: '云',
  code_bracket: '代码',
  command_line: '命令行',
  dashboard: '概览',
  data_source: '数据源',
  document_chart: '报表',
  document_text: '文档',
  ellipsis: '更多',
  external_link: '外链',
  flag: '标记',
  folder: '目录',
  funnel: '筛选',
  globe: '站点',
  identification: '身份',
  info: '说明',
  key: '密钥',
  knowledge_base: '知识库',
  language: '语言',
  lifebuoy: '帮助',
  link: '链接',
  lock: '权限锁',
  mcp_service: 'MCP 服务',
  mcp_tools: 'MCP 工具',
  megaphone: '公告',
  memory: '记忆',
  menu: '菜单',
  metadata: '元数据',
  pause: '暂停',
  play: '运行',
  playground: '调试台',
  prompts: '提示词',
  rectangle_stack: '集合',
  rocket: '发布',
  roles: '角色',
  shield: '安全',
  signal: '监控',
  skills: '技能',
  squares_plus: '应用',
  star: '推荐',
  system: '系统',
  tag: '标签',
  tasks: '任务',
  token_stats: '用量统计',
  user: '个人',
  user_circle: '用户头像',
  org: '组织',
  users: '用户',
  widget: '组件',
}

/**
 * 选择器里的分组。图标多起来以后平铺一屏找不着，按用途分组更省事；
 * 分组只影响菜单管理页的展示顺序，与侧栏无关。
 */
export const MENU_ICON_CATEGORIES: { title: string; keys: string[] }[] = [
  {
    title: '导航与总览',
    keys: ['dashboard', 'menu', 'widget', 'ellipsis', 'rectangle_stack', 'squares_plus', 'globe'],
  },
  {
    title: '对话与智能体',
    keys: ['chat', 'chat_bubble_left_right', 'chat_logs', 'prompts', 'agent_mgmt', 'agent_debug', 'playground', 'skills', 'bolt', 'rocket'],
  },
  {
    title: '数据与分析',
    keys: ['data_source', 'metadata', 'chart_bar', 'chart_line', 'chart_pie', 'document_chart', 'token_stats', 'funnel', 'arrows_right_left'],
  },
  {
    title: '知识与文档',
    keys: ['knowledge_base', 'document_text', 'clipboard_list', 'folder', 'memory', 'bookmark', 'tag'],
  },
  {
    title: '用户与权限',
    keys: ['org', 'users', 'user', 'user_circle', 'roles', 'key', 'lock', 'shield', 'audit', 'identification'],
  },
  {
    title: '系统与运维',
    keys: ['system', 'mcp_tools', 'mcp_service', 'cloud', 'code_bracket', 'command_line', 'bug', 'language', 'link', 'external_link', 'signal'],
  },
  {
    title: '任务与状态',
    keys: ['tasks', 'clock', 'calendar', 'play', 'pause', 'star', 'flag', 'info', 'lifebuoy', 'bell', 'megaphone'],
  },
]
