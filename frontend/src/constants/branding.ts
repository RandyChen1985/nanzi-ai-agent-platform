export const DEFAULT_PRODUCT_NAME = 'NanZi·智能体平台'
export const DEFAULT_LOGIN_SUBTITLE = 'NanZi Intelligent Agent Platform'
export const DEFAULT_ICON_URL = '/favicon.png'
export const DEFAULT_REPO_URL = 'https://github.com/RandyChen1985/nanzi-ai-agent-platform'
export const DEFAULT_AGENT_NAME = 'NanZi · AI'

export interface PublicBranding {
  enabled: boolean
  product_name: string
  login_subtitle: string
  icon_url: string
  hide_login_sso: boolean
  hide_version_link: boolean
  contact_markdown: string
  copyright_text: string
  default_agent_name: string
}

export const DEFAULT_BRANDING: PublicBranding = {
  enabled: false,
  product_name: DEFAULT_PRODUCT_NAME,
  login_subtitle: DEFAULT_LOGIN_SUBTITLE,
  icon_url: DEFAULT_ICON_URL,
  hide_login_sso: false,
  hide_version_link: false,
  contact_markdown: '',
  copyright_text: '',
  default_agent_name: DEFAULT_AGENT_NAME,
}
