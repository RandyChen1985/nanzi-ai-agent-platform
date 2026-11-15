/**
 * 凭据模板常量 —— 「录入期渲染骨架」。
 *
 * 只影响录入表单长什么样、列表上怎么显示，不入库：库里存的是
 * `value_format = 'json'` 加模板标识 `value_template`，再加用户填的 values。
 * 因此模板升级不会追溯老记录 —— 老记录按它当时的标识展示，字段对不上就当未知
 * 字段渲染；后端 parse 时也不校验字段 schema，字段增删不会让老记录读不出来。
 *
 * `secret` 标记是列表摘要的唯一依据：整段值本来就已加密，标了 secret 只是把该
 * 字段名放进 `secret_fields`（后端据它把字段从摘要里排除）。敏感字段名在这里
 * 声明、摘要由后端拼，所以漏标一个 secret，等于让密码进 `value_summary` 列。
 *
 * 刻意放前端常量而不是后端下发：改模板需要发版，换来的是模板定义与渲染组件
 * 同源、随代码一起 review，不用多一个新接口加一层缓存。
 */

export type CredentialFieldType = 'text' | 'password' | 'number' | 'url' | 'select' | 'textarea' | 'switch'

export interface CredentialFieldOption { value: string; label: string }

export interface CredentialField {
  key: string
  label: string
  type: CredentialFieldType
  required?: boolean
  /** 敏感字段：整段仍加密，不得进入列表摘要 */
  secret?: boolean
  /** 折叠在「高级字段」里 */
  advanced?: boolean
  /** 独占整行 */
  full?: boolean
  placeholder?: string
  hint?: string
  /** 仅作占位提示用，绝不预填 */
  example?: string | number | boolean
  options?: CredentialFieldOption[]
  /** 条件显示（依赖其它字段的值） */
  showIf?: (values: Record<string, unknown>) => boolean
  /** 联动：当 when 指向的字段取到 map 的键时，自动填本字段 */
  auto?: { when: string; map: Record<string, number> }
}

export interface CredentialTemplate {
  id: string
  group: string
  icon: string
  name: string
  desc: string
  fields: CredentialField[]
  /** 自定义 JSON：无固定字段，直接编辑对象 */
  raw?: boolean
}

export const CREDENTIAL_TEMPLATES: CredentialTemplate[] = [
  /* ── 邮件 ─────────────────────────────────────────── */
  {
    id: 'smtp', group: '邮件', icon: '✉', name: '邮箱 SMTP 发送',
    desc: '用第三方邮箱发告警、日报、通知邮件',
    fields: [
      { key: 'host', label: 'SMTP 服务器', type: 'text', required: true, example: 'smtp.exmail.qq.com', placeholder: 'smtp.exmail.qq.com', hint: '腾讯企业邮 smtp.exmail.qq.com；163 是 smtp.163.com' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 465, auto: { when: 'security', map: { ssl: 465, starttls: 587, none: 25 } } },
      { key: 'security', label: '加密方式', type: 'select', options: [{ value: 'ssl', label: 'SSL/TLS' }, { value: 'starttls', label: 'STARTTLS' }, { value: 'none', label: '不加密' }], example: 'ssl' },
      { key: 'username', label: '登录账号', type: 'text', required: true, example: 'alert-bot@nanzi.ai' },
      { key: 'password', label: '密码 / 授权码', type: 'password', required: true, secret: true, example: 'Whq7mT2026smtp', hint: 'QQ / 163 要用「授权码」，不是登录密码' },
      { key: 'from_name', label: '发件人显示名', type: 'text', example: '南孜平台告警', advanced: true },
      { key: 'default_to', label: '默认收件人', type: 'text', example: 'ops@nanzi.ai', advanced: true, hint: '多个用逗号分隔；留空则在工具参数里指定' }
    ]
  },
  {
    id: 'imap', group: '邮件', icon: '📥', name: '邮箱 IMAP 收信',
    desc: '读取收件箱：邮件转工单、附件落库',
    fields: [
      { key: 'host', label: 'IMAP 服务器', type: 'text', required: true, example: 'imap.exmail.qq.com' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 993 },
      { key: 'security', label: '加密方式', type: 'select', options: [{ value: 'ssl', label: 'SSL/TLS' }, { value: 'starttls', label: 'STARTTLS' }], example: 'ssl' },
      { key: 'username', label: '登录账号', type: 'text', required: true, example: 'support@nanzi.ai' },
      { key: 'password', label: '密码 / 授权码', type: 'password', required: true, secret: true, example: 'Zx81kLm#2026' },
      { key: 'mailbox', label: '邮箱文件夹', type: 'text', example: 'INBOX', advanced: true },
      { key: 'unread_only', label: '只拉未读', type: 'switch', example: true, advanced: true }
    ]
  },

  /* ── 文件传输 ─────────────────────────────────────── */
  {
    id: 'sftp', group: '文件传输', icon: '📁', name: 'SFTP 文件传输',
    desc: '对接银行/客户的对账文件交换目录',
    fields: [
      { key: 'host', label: '主机', type: 'text', required: true, example: 'sftp.partner-bank.cn' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 22 },
      { key: 'username', label: '用户名', type: 'text', required: true, example: 'nanzi_ops' },
      { key: 'auth_type', label: '认证方式', type: 'select', options: [{ value: 'password', label: '密码' }, { value: 'key', label: '私钥' }], example: 'key' },
      { key: 'password', label: '密码', type: 'password', required: true, secret: true, example: '', showIf: (values) => values.auth_type === 'password', hint: '选了「密码」才会出现这个字段' },
      { key: 'private_key', label: '私钥（PEM 整段粘贴）', type: 'textarea', secret: true, full: true, required: true, showIf: (values) => values.auth_type === 'key',
        example: '-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\nQyNTUxOQAAACBq7Xk9Qm2vT3hZ0lP4sN8yR1cV6wJ5aK0dEfGhIjKlMnOpQrStUvWxYzA\n-----END OPENSSH PRIVATE KEY-----' },
      { key: 'passphrase', label: '私钥口令', type: 'password', secret: true, showIf: (values) => values.auth_type === 'key', advanced: true },
      { key: 'base_dir', label: '默认目录', type: 'text', example: '/outbound/nanzi', advanced: true }
    ]
  },
  {
    id: 'ftp', group: '文件传输', icon: '🗂', name: 'FTP（含 FTPS）',
    desc: '老系统常见的文件投递通道',
    fields: [
      { key: 'host', label: '主机', type: 'text', required: true, example: 'ftp.legacy-erp.local' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 21 },
      { key: 'username', label: '用户名', type: 'text', required: true, example: 'erp_sync' },
      { key: 'password', label: '密码', type: 'password', required: true, secret: true, example: 'Erp#2026sync' },
      { key: 'security', label: '加密', type: 'select', options: [{ value: 'ftp', label: '明文 FTP' }, { value: 'ftps', label: 'FTPS（显式 TLS）' }], example: 'ftps' },
      { key: 'passive', label: '被动模式', type: 'switch', example: true, advanced: true },
      { key: 'encoding', label: '文件名编码', type: 'select', options: [{ value: 'utf-8', label: 'UTF-8' }, { value: 'gbk', label: 'GBK' }], example: 'gbk', advanced: true }
    ]
  },

  /* ── 数据库 ───────────────────────────────────────── */
  {
    id: 'mysql', group: '数据库', icon: '🐬', name: 'MySQL / MariaDB',
    desc: '业务库只读账号，供数据类工具查询',
    fields: [
      { key: 'host', label: '主机', type: 'text', required: true, example: '10.20.8.31' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 3306 },
      { key: 'database', label: '库名', type: 'text', required: true, example: 'reporting' },
      { key: 'username', label: '用户名', type: 'text', required: true, example: 'ro_agent' },
      { key: 'password', label: '密码', type: 'password', required: true, secret: true, example: 'Ro!Ag3nt2026' },
      { key: 'charset', label: '字符集', type: 'select', options: [{ value: 'utf8mb4', label: 'UTF-8 (utf8mb4)' }, { value: 'utf8', label: 'UTF-8 (utf8)' }], example: 'utf8mb4', advanced: true },
      { key: 'ssl_mode', label: 'SSL 模式', type: 'select', options: [{ value: 'preferred', label: 'preferred' }, { value: 'required', label: 'required' }, { value: 'disabled', label: 'disabled' }], example: 'preferred', advanced: true }
    ]
  },
  {
    id: 'postgres', group: '数据库', icon: '🐘', name: 'PostgreSQL',
    desc: '数仓 / 元数据库连接',
    fields: [
      { key: 'host', label: '主机', type: 'text', required: true, example: 'pg-prod.nanzi.ai' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 5432 },
      { key: 'database', label: '数据库', type: 'text', required: true, example: 'analytics' },
      { key: 'username', label: '用户名', type: 'text', required: true, example: 'bi_reader' },
      { key: 'password', label: '密码', type: 'password', required: true, secret: true, example: 'bi#Reader2026' },
      { key: 'schema', label: '默认 schema', type: 'text', example: 'public', advanced: true },
      { key: 'sslmode', label: 'SSL 模式', type: 'select', options: [{ value: 'prefer', label: 'prefer' }, { value: 'require', label: 'require' }, { value: 'disable', label: 'disable' }], example: 'require', advanced: true }
    ]
  },
  {
    id: 'redis', group: '数据库', icon: '🧱', name: 'Redis',
    desc: '缓存 / 队列 / 限流计数',
    fields: [
      { key: 'host', label: '主机', type: 'text', required: true, example: 'redis-prod.nanzi.ai' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 6379 },
      { key: 'db', label: 'DB 编号', type: 'number', example: 0 },
      { key: 'password', label: '密码', type: 'password', required: true, secret: true, example: 'R3dis!Cache' },
      { key: 'username', label: 'ACL 用户名', type: 'text', example: 'agent_ro', advanced: true },
      { key: 'tls', label: '启用 TLS', type: 'switch', example: false, advanced: true }
    ]
  },
  {
    id: 'mongodb', group: '数据库', icon: '🍃', name: 'MongoDB',
    desc: '文档库，读写业务集合',
    fields: [
      { key: 'host', label: '主机', type: 'text', required: true, example: 'mongo-1.nanzi.ai' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 27017 },
      { key: 'database', label: '数据库', type: 'text', required: true, example: 'crawler' },
      { key: 'username', label: '用户名', type: 'text', required: true, example: 'crawler_rw' },
      { key: 'password', label: '密码', type: 'password', required: true, secret: true, example: 'Cr4wl#2026' },
      { key: 'auth_source', label: '认证库', type: 'text', example: 'admin', advanced: true },
      { key: 'replica_set', label: '副本集名', type: 'text', example: 'rs0', advanced: true }
    ]
  },
  {
    id: 'es', group: '数据库', icon: '🔍', name: 'Elasticsearch / OpenSearch',
    desc: '日志检索、全文搜索索引',
    fields: [
      { key: 'host', label: '主机', type: 'text', required: true, example: 'es-search.nanzi.ai' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 9200 },
      { key: 'username', label: '用户名', type: 'text', example: 'es_reader' },
      { key: 'password', label: '密码', type: 'password', secret: true, example: 'Es@Read2026' },
      { key: 'index_prefix', label: '默认索引前缀', type: 'text', example: 'logs-*', advanced: true },
      { key: 'verify_tls', label: '校验证书', type: 'switch', example: true, advanced: true }
    ]
  },

  /* ── 云与存储 ─────────────────────────────────────── */
  {
    id: 'aws', group: '云与存储', icon: '☁', name: 'AWS IAM / S3',
    desc: 'S3 读写、Lambda 调用、Bedrock',
    fields: [
      { key: 'access_key_id', label: 'Access Key ID', type: 'text', required: true, example: 'AKIAZ7Q2XLM4NV8P3R6T', hint: 'AK 本身也算标识，泄露即可被用于撞库探测' },
      { key: 'secret_access_key', label: 'Secret Access Key', type: 'password', required: true, secret: true, example: 'wJalrXUtnFEMI7K9bPxRfiCYEXAMPLEKEY' },
      { key: 'region', label: '区域', type: 'select', options: [{ value: 'ap-southeast-1', label: 'ap-southeast-1（新加坡）' }, { value: 'us-east-1', label: 'us-east-1' }, { value: 'cn-north-1', label: 'cn-north-1' }, { value: 'eu-west-1', label: 'eu-west-1' }], example: 'ap-southeast-1' },
      { key: 'bucket', label: '默认 Bucket', type: 'text', example: 'nanzi-agent-artifacts', advanced: true },
      { key: 'endpoint', label: '自定义 Endpoint', type: 'url', example: '', advanced: true, hint: '用 S3 兼容存储时填，官方 S3 留空' }
    ]
  },
  {
    id: 'aliyun-oss', group: '云与存储', icon: '🗄', name: '阿里云 OSS',
    desc: '对象存储、图片与附件落盘',
    fields: [
      { key: 'access_key_id', label: 'AccessKey ID', type: 'text', required: true, example: 'LTAI5tQ8xN3vK2mP7rTf' },
      { key: 'access_key_secret', label: 'AccessKey Secret', type: 'password', required: true, secret: true, example: '9cF2mQxLw7VbN4kR8sTzP1yH' },
      { key: 'endpoint', label: 'Endpoint', type: 'text', required: true, example: 'oss-cn-hangzhou.aliyuncs.com' },
      { key: 'bucket', label: 'Bucket', type: 'text', required: true, example: 'nanzi-kb-files' },
      { key: 'internal', label: '走内网 Endpoint', type: 'switch', example: true, advanced: true }
    ]
  },
  {
    id: 'cos', group: '云与存储', icon: '🧊', name: '腾讯云 COS',
    desc: '对象存储与 CDN 回源',
    fields: [
      { key: 'secret_id', label: 'SecretId', type: 'text', required: true, example: 'AKIDz8krbsJ5yKBZQpn74WFkmLPx3gnP' },
      { key: 'secret_key', label: 'SecretKey', type: 'password', required: true, secret: true, example: 'Gu5t9xGARNpq86cd98joQYCN3VbWzPq' },
      { key: 'region', label: '地域', type: 'select', options: [{ value: 'ap-guangzhou', label: '广州' }, { value: 'ap-shanghai', label: '上海' }, { value: 'ap-beijing', label: '北京' }], example: 'ap-guangzhou' },
      { key: 'bucket', label: 'Bucket', type: 'text', required: true, example: 'nanzi-assets-1250000001' }
    ]
  },
  {
    id: 'minio', group: '云与存储', icon: '📦', name: 'MinIO / S3 兼容',
    desc: '私有化部署的对象存储',
    fields: [
      { key: 'endpoint', label: 'Endpoint', type: 'url', required: true, example: 'https://minio.nanzi.ai:9000', hint: '含协议头，别只填域名' },
      { key: 'access_key', label: 'Access Key', type: 'text', required: true, example: 'nanzi-minio-agent' },
      { key: 'secret_key', label: 'Secret Key', type: 'password', required: true, secret: true, example: 'm1n10#Agent2026' },
      { key: 'bucket', label: 'Bucket', type: 'text', example: 'agent-files' },
      { key: 'region', label: 'Region', type: 'text', example: 'us-east-1', advanced: true },
      { key: 'path_style', label: '强制 path-style', type: 'switch', example: true, advanced: true }
    ]
  },

  /* ── 消息通知 ─────────────────────────────────────── */
  {
    id: 'wecom', group: '消息通知', icon: '💬', name: '企业微信群机器人',
    desc: '任务失败、审批待办推到群里',
    fields: [
      { key: 'webhook_url', label: 'Webhook 地址', type: 'password', required: true, secret: true, full: true,
        example: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=693a91f6-7xxx-4bc4-97a0-0ec2sifa5aaa',
        hint: '地址里的 key 就是凭证本身，所以整条都按敏感处理' },
      { key: 'bot_name', label: '机器人名字', type: 'text', example: '南孜告警', advanced: true },
      { key: 'mention_mobile', label: '@ 手机号', type: 'text', example: '', advanced: true, hint: '多个用逗号分隔' }
    ]
  },
  {
    id: 'dingtalk', group: '消息通知', icon: '🔔', name: '钉钉群机器人',
    desc: '支持加签的群消息推送',
    fields: [
      { key: 'webhook_url', label: 'Webhook 地址', type: 'password', required: true, secret: true, full: true,
        example: 'https://oapi.dingtalk.com/robot/send?access_token=a1b2c3d4e5f6' },
      { key: 'sign_secret', label: '加签 Secret', type: 'password', secret: true, example: 'SECa1b2c3d4e5f6g7h8', hint: '开了「加签」才需要填' },
      { key: 'at_mobiles', label: '@ 手机号', type: 'text', example: '', advanced: true }
    ]
  },
  {
    id: 'feishu', group: '消息通知', icon: '🕊', name: '飞书群机器人',
    desc: '卡片消息、值班提醒',
    fields: [
      { key: 'webhook_url', label: 'Webhook 地址', type: 'password', required: true, secret: true, full: true,
        example: 'https://open.feishu.cn/open-apis/bot/v2/hook/8f3d2c1a-9b7e-4f6d' },
      { key: 'sign_secret', label: '签名校验 Secret', type: 'password', secret: true, example: '', advanced: true }
    ]
  },
  {
    id: 'slack', group: '消息通知', icon: '🟣', name: 'Slack Bot',
    desc: '海外团队频道通知',
    fields: [
      { key: 'bot_token', label: 'Bot Token', type: 'password', required: true, secret: true, example: 'xoxb-3901-4821-aBcDeFgHiJkLmNoP' },
      { key: 'default_channel', label: '默认频道', type: 'text', example: '#ops-alerts', advanced: true }
    ]
  },

  /* ── 通用鉴权 ─────────────────────────────────────── */
  {
    id: 'bearer', group: '通用鉴权', icon: '🔑', name: 'Bearer Token',
    desc: '最常见的第三方 API 令牌',
    fields: [
      { key: 'token', label: 'Token', type: 'password', required: true, secret: true, full: true, example: 'ghp_9f2cKd7LmQw4Xz8Vb3Nt6Ry1Ha5Je0Pu2S7g' },
      { key: 'header_name', label: '请求头名', type: 'text', example: 'Authorization', advanced: true },
      { key: 'prefix', label: '值前缀', type: 'text', example: 'Bearer', advanced: true, hint: '拼成 Authorization: Bearer <token>' }
    ]
  },
  {
    id: 'api-key', group: '通用鉴权', icon: '🏷', name: 'API Key（自定义头）',
    desc: 'Jira / Confluence / 内部网关这类 X-API-Key',
    fields: [
      { key: 'key_name', label: 'Header / 参数名', type: 'text', required: true, example: 'X-API-Key' },
      { key: 'key_value', label: 'Key 值', type: 'password', required: true, secret: true, full: true, example: 'nz_live_7Kx2mQ9pT4vR8bN3' },
      { key: 'in_query', label: '作为 URL 参数发送', type: 'switch', example: false, advanced: true },
      { key: 'base_url', label: '默认 Base URL', type: 'url', example: 'https://jira.nanzi.ai', advanced: true }
    ]
  },
  {
    id: 'basic', group: '通用鉴权', icon: '👤', name: 'Basic Auth',
    desc: 'HTTP 基础认证，老系统对接',
    fields: [
      { key: 'username', label: '用户名', type: 'text', required: true, example: 'svc_nanzi' },
      { key: 'password', label: '密码', type: 'password', required: true, secret: true, example: 'B4sic!Auth2026' },
      { key: 'realm', label: 'Realm 说明', type: 'text', example: '', advanced: true }
    ]
  },
  {
    id: 'oauth2', group: '通用鉴权', icon: '🔄', name: 'OAuth2 客户端',
    desc: 'client_credentials 换 token，适合服务间调用',
    fields: [
      { key: 'client_id', label: 'Client ID', type: 'text', required: true, example: 'nanzi-agent-prod' },
      { key: 'client_secret', label: 'Client Secret', type: 'password', required: true, secret: true, example: 'cS3cr3t-9f2b-4d1e-8a7c-5e6f0b1a2c3d' },
      { key: 'token_url', label: 'Token 地址', type: 'url', required: true, example: 'https://sso.nanzi.ai/oauth2/token' },
      { key: 'grant_type', label: '授权类型', type: 'select', options: [{ value: 'client_credentials', label: 'client_credentials' }, { value: 'authorization_code', label: 'authorization_code' }], example: 'client_credentials' },
      { key: 'scopes', label: 'Scopes', type: 'text', example: 'tools.read tools.invoke', advanced: true },
      { key: 'audience', label: 'Audience', type: 'text', example: 'https://api.nanzi.ai', advanced: true }
    ]
  },

  /* ── 密钥与证书 ───────────────────────────────────── */
  {
    id: 'ssh-key', group: '密钥与证书', icon: '🔐', name: 'SSH 私钥登录',
    desc: '跳板机执行、远程运维脚本',
    fields: [
      { key: 'username', label: '登录用户', type: 'text', required: true, example: 'ops' },
      { key: 'private_key', label: '私钥（PEM / OpenSSH 整段）', type: 'textarea', full: true, required: true, secret: true,
        example: '-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtz\n-----END OPENSSH PRIVATE KEY-----',
        hint: '分行原样保存，不会被 trim；粘贴时别把首尾的 BEGIN/END 行漏掉' },
      { key: 'passphrase', label: '私钥口令', type: 'password', secret: true, advanced: true },
      { key: 'known_hosts_entry', label: '固定 host key', type: 'text', example: 'jump.nanzi.ai ssh-ed25519 AAAAC3Nza…', advanced: true }
    ]
  },
  {
    id: 'tls-cert', group: '密钥与证书', icon: '📜', name: 'TLS 客户端证书（mTLS）',
    desc: '双向认证的私有网关调用',
    fields: [
      { key: 'client_cert', label: '客户端证书', type: 'textarea', full: true, required: true, example: '-----BEGIN CERTIFICATE-----\nMIIDdzCCAl+gAwIBAgIEbG9jYWwwDQYJKoZIhvcNAQELBQAw\n-----END CERTIFICATE-----' },
      { key: 'client_key', label: '客户端私钥', type: 'textarea', full: true, required: true, secret: true, example: '-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----' },
      { key: 'ca_cert', label: 'CA 证书链', type: 'textarea', full: true, advanced: true, example: '' }
    ]
  },
  {
    id: 'gcp-sa', group: '密钥与证书', icon: '🎫', name: 'Google 服务账号 JSON',
    desc: 'GCP / Vertex AI / BigQuery 调用',
    fields: [
      { key: 'project_id', label: 'Project ID', type: 'text', required: true, example: 'nanzi-agent-prod' },
      { key: 'client_email', label: '服务账号邮箱', type: 'text', required: true, example: 'agent-runner@nanzi-agent-prod.iam.gserviceaccount.com' },
      { key: 'private_key', label: 'private_key（PEM 整段）', type: 'textarea', full: true, required: true, secret: true,
        example: '-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ\n-----END PRIVATE KEY-----' },
      { key: 'private_key_id', label: 'private_key_id', type: 'text', example: 'a1b2c3d4e5f6a7b8c9d0', advanced: true },
      { key: 'token_uri', label: 'Token URI', type: 'text', example: 'https://oauth2.googleapis.com/token', advanced: true }
    ]
  },

  /* ── 基础设施 ─────────────────────────────────────── */
  {
    id: 'kafka', group: '基础设施', icon: '🚇', name: 'Kafka（SASL）',
    desc: '事件流生产 / 消费',
    fields: [
      { key: 'bootstrap_servers', label: 'Bootstrap Servers', type: 'text', required: true, full: true, example: 'kafka-1.nanzi.ai:9092,kafka-2.nanzi.ai:9092' },
      { key: 'security_protocol', label: '协议', type: 'select', options: [{ value: 'SASL_SSL', label: 'SASL_SSL' }, { value: 'SASL_PLAINTEXT', label: 'SASL_PLAINTEXT' }, { value: 'PLAINTEXT', label: 'PLAINTEXT' }], example: 'SASL_SSL' },
      { key: 'sasl_mechanism', label: 'SASL 机制', type: 'select', options: [{ value: 'PLAIN', label: 'PLAIN' }, { value: 'SCRAM-SHA-256', label: 'SCRAM-SHA-256' }, { value: 'SCRAM-SHA-512', label: 'SCRAM-SHA-512' }], example: 'SCRAM-SHA-256' },
      { key: 'username', label: '用户名', type: 'text', required: true, example: 'agent-producer' },
      { key: 'password', label: '密码', type: 'password', required: true, secret: true, example: 'K4fka#Agent' },
      { key: 'group_id', label: '消费组', type: 'text', example: 'agent-workers', advanced: true }
    ]
  },
  {
    id: 'rabbitmq', group: '基础设施', icon: '🐇', name: 'RabbitMQ',
    desc: '任务分发、延迟队列',
    fields: [
      { key: 'host', label: '主机', type: 'text', required: true, example: 'mq.nanzi.ai' },
      { key: 'port', label: '端口', type: 'number', required: true, example: 5672 },
      { key: 'username', label: '用户名', type: 'text', required: true, example: 'agent_task' },
      { key: 'password', label: '密码', type: 'password', required: true, secret: true, example: 'R4bbit#Task' },
      { key: 'vhost', label: 'vhost', type: 'text', example: '/agents', advanced: true },
      { key: 'use_tls', label: '启用 TLS', type: 'switch', example: true, advanced: true }
    ]
  },
  {
    id: 'proxy', group: '基础设施', icon: '🌐', name: '出站代理',
    desc: '访问外网时的代理认证',
    fields: [
      { key: 'url', label: '代理地址', type: 'url', required: true, example: 'http://proxy.nanzi.ai:3128', hint: '含协议头与端口' },
      { key: 'username', label: '用户名', type: 'text', example: 'agent_out' },
      { key: 'password', label: '密码', type: 'password', secret: true, example: 'Pr0xy#2026' },
      { key: 'no_proxy', label: 'no_proxy', type: 'text', example: 'localhost,127.0.0.1,*.internal', advanced: true }
    ]
  },

  /* ── 开发与 AI ────────────────────────────────────── */
  {
    id: 'openai', group: '开发与 AI', icon: '🤖', name: 'OpenAI 兼容 API',
    desc: 'DeepSeek / 通义 / vLLM 等兼容端点',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', required: true, secret: true, full: true, example: 'sk-nanzi-9f2c7d4b1e8a6350c2d9' },
      { key: 'base_url', label: 'Base URL', type: 'url', required: true, example: 'https://api.deepseek.com/v1' },
      { key: 'model', label: '默认模型', type: 'text', example: 'deepseek-chat' },
      { key: 'org_id', label: 'Organization', type: 'text', example: '', advanced: true },
      { key: 'timeout_s', label: '超时（秒）', type: 'number', example: 60, advanced: true }
    ]
  },
  {
    id: 'custom', group: '开发与 AI', icon: '{}', name: '自定义 JSON',
    desc: '没有合适模板时，直接手写对象结构',
    raw: true,
    fields: []
  }
]

/** 模板选择器的分组 tab：首项固定「全部」，其余按模板出现顺序去重 */
export const CREDENTIAL_TEMPLATE_GROUPS: string[] = ['全部', ...Array.from(new Set(CREDENTIAL_TEMPLATES.map((t) => t.group)))]

/** 自定义 JSON 模板：无固定字段，直接手编对象 */
export const CREDENTIAL_JSON_TEMPLATE_ID = 'custom'

export function findCredentialTemplate(id: string | null | undefined): CredentialTemplate | null {
  if (!id) return null
  return CREDENTIAL_TEMPLATES.find((t) => t.id === id) || null
}

export function credentialTemplateLabel(id: string | null | undefined): string {
  return findCredentialTemplate(id)?.name || '结构化'
}
