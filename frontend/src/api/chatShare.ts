/**
 * 会话在线分享的 HTTP 层。
 *
 * 两条链路刻意分开：
 * - 所有者接口走 `/api/portal/chat-shares`，用全局 axios 实例（同源 Cookie 自动携带，
 *   嵌入场景由调用方通过 `getHeaders` 注入 Bearer / X-API-Key）；
 * - 访客接口走 `/api/v1/share/*`，用**独立 axios 实例**（`visitorClient`）。
 *
 * 为什么不能用默认的 `axios` 或共享的 `request`：`main.ts` 在 axios **默认实例**
 * 上挂了全局响应拦截器，任何 401 都会清 `localStorage` 并整页 `location.href='/login'`
 * （只对 `/embed/` 与 `X-Ignore-Auth-Redirect` 放行）。密码输错正是 401——若走默认
 * 实例，分享页刚把「密码不正确」渲染出来就被跳去登录页，用户永远回不到分享内容。
 * `axios.create()` 出来的实例不继承默认实例的拦截器，这里必须用它。
 */
import axios from 'axios';

import request from '../utils/axios';
import type { StandardResponse } from './common';

/**
 * 访客专用实例：刻意不带任何响应拦截器。
 *
 * 保留 `withCredentials` 是为了「分享人自己打开自己的链接免密码」——后端据此读
 * `portal_session` Cookie 判断 viewer 是否为 owner。
 */
const visitorClient = axios.create({
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
});

export interface ShareExpiryPayload {
  mode: 'preset' | 'custom' | 'permanent';
  preset?: string;
  value?: number;
  unit?: 'hour' | 'day';
}

export interface CreateSharePayload {
  conversation_id: string;
  access_mode: 'public' | 'secret';
  secret?: string;
  expiry: ShareExpiryPayload;
}

/** 所有者列表行：`secret` 是回显的明文密码，公开分享为 null。 */
export interface ShareListItem {
  share_id: string;
  url: string;
  title: string;
  agent_name: string;
  access_mode: 'public' | 'secret';
  secret: string | null;
  status: 'active' | 'expired' | 'revoked';
  expires_at: string | null;
  created_at: string | null;
  revoked_at: string | null;
  view_count: number;
  last_viewed_at: string | null;
  turn_count: number;
  message_count: number;
}

export interface ShareListResponse {
  total: number;
  page: number;
  page_size: number;
  items: ShareListItem[];
}

/** 访客元信息：只有标题 / 归属 / 状态，**不含任何消息内容**。 */
export interface ShareMeta {
  title: string;
  agent_name: string;
  access_mode: 'public' | 'secret';
  status: 'active' | 'expired' | 'revoked';
  expires_at: string | null;
  remaining_seconds: number | null;
  message_count: number;
  turn_count: number;
  created_at: string | null;
  owner_display: string;
}

export interface ShareMessage {
  role: 'user' | 'assistant';
  content: string;
  created_at: string | null;
}

export interface ShareContent {
  title: string;
  agent_name: string;
  created_at: string | null;
  messages: ShareMessage[];
}

export interface ShareUnlockResult {
  /** 短期访问令牌，只能放在 `X-Share-Token` 请求头里，不进 URL。 */
  token: string;
  expires_in: number;
}

/** 鉴权头注入点：门户页不传（走 Cookie），嵌入页传自带的 Bearer / X-API-Key。 */
export type AuthHeaders = () => Record<string, string> | undefined;

function ownerHeaders(getHeaders?: AuthHeaders): Record<string, string> {
  return { ...(getHeaders?.() || {}) };
}

/** 创建分享（所有者）。 */
export async function createConversationShare(
  payload: CreateSharePayload,
  getHeaders?: AuthHeaders,
): Promise<ShareListItem> {
  const resp = await request.post<StandardResponse<ShareListItem>>(
    '/api/portal/chat-shares',
    payload,
    { headers: ownerHeaders(getHeaders) },
  );
  return resp.data.data;
}

/** 「我分享的」分页列表（所有者）。 */
export async function listConversationShares(
  params: { page?: number; page_size?: number; status?: string } = {},
  getHeaders?: AuthHeaders,
): Promise<ShareListResponse> {
  const resp = await request.get<StandardResponse<ShareListResponse>>(
    '/api/portal/chat-shares',
    { params, headers: ownerHeaders(getHeaders) },
  );
  return resp.data.data;
}

/** 作废分享（所有者，幂等：重复作废仍返回 true）。 */
export async function revokeConversationShare(
  shareId: string,
  getHeaders?: AuthHeaders,
): Promise<boolean> {
  const resp = await request.post<StandardResponse<{ revoked?: boolean }>>(
    `/api/portal/chat-shares/${encodeURIComponent(shareId)}/revoke`,
    {},
    { headers: ownerHeaders(getHeaders) },
  );
  return Boolean(resp.data.data?.revoked);
}

/** 访客读取元信息：不计数，用于判断是否需要密码。 */
export async function fetchShareMeta(shareId: string): Promise<ShareMeta> {
  const resp = await visitorClient.get<StandardResponse<ShareMeta>>(
    `/api/v1/share/${encodeURIComponent(shareId)}`,
  );
  return resp.data.data;
}

/** 访客用访问密码换短期令牌（可能 401 密码错 / 429 尝试过多 / 404 已失效）。 */
export async function unlockShare(
  shareId: string,
  secret: string,
): Promise<ShareUnlockResult> {
  const resp = await visitorClient.post<StandardResponse<ShareUnlockResult>>(
    `/api/v1/share/${encodeURIComponent(shareId)}/unlock`,
    { secret },
  );
  return resp.data.data;
}

/** 访客读取冻结快照正文（成功即 +1 访问计数）。 */
export async function fetchShareContent(
  shareId: string,
  token?: string,
): Promise<ShareContent> {
  const resp = await visitorClient.get<StandardResponse<ShareContent>>(
    `/api/v1/share/${encodeURIComponent(shareId)}/content`,
    {
      // 令牌走请求头而不是 query：避免被复制进浏览器历史、Referer 与访问日志
      headers: token ? { 'X-Share-Token': token } : {},
    },
  );
  return resp.data.data;
}
