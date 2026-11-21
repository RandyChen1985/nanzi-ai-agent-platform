/**
 * 工作区文件在线分享的 HTTP 层。
 *
 * 与会话分享（`chatShare.ts`）同一套分工：
 * - 所有者接口走 `/api/portal/file-shares`，用全局 `request`（同源 Cookie 自动携带）；
 * - 访客接口走 `/api/v1/share/file/*`，用**独立 axios 实例**。
 *
 * 为什么访客接口不能复用默认 axios：`main.ts` 在默认实例上挂了全局 401 拦截器，会把页面
 * 直接跳去 `/login`。密码输错正是 401——若走默认实例，落地页刚显示「密码不正确」就被
 * 弹走，访客再也回不到下载页。
 *
 * 下载**不用 axios**：`downloadSharedFileUrl()` 只拼 URL，交给浏览器直接打开（响应带
 * `Content-Disposition: attachment`，浏览器会当下载处理）。走 axios 反而要把整个文件读进
 * 内存再拼 blob，大文件必崩。
 */
import axios from 'axios';

import request from '../utils/axios';
import type { StandardResponse } from './common';

/** 访客专用实例：刻意不带任何响应拦截器。 */
const visitorClient = axios.create({
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
  // 保留 Cookie：分享人自己打开自己的链接时后端据此免密码
  withCredentials: true,
});

export interface ShareExpiryPayload {
  mode: 'preset' | 'custom' | 'permanent';
  preset?: string;
  value?: number;
  unit?: 'hour' | 'day';
}

export interface CreateFileSharePayload {
  /**
   * 工作区路径。与 `artifact_id` 二选一。
   *
   * 产物（生成的图片/导出件）落盘在 `<user_key>/generated/` 下，但工具交给前端的只有
   * `download_url`，路径从没出过后端——所以产物场景改传 ID，由后端反查路径。
   */
  path?: string;
  /** 产物 ID（download_url 里的 32 位 hex）。 */
  artifact_id?: string;
  access_mode: 'public' | 'secret';
  secret?: string;
  expiry?: ShareExpiryPayload;
}

export interface FileShareItem {
  share_id: string;
  file_name: string;
  file_ext: string;
  file_size: number;
  mime_type: string;
  access_mode: 'public' | 'secret';
  /** 明文密码：创建响应里一定会带，列表接口能解密就带，解不开为 null。 */
  secret: string | null;
  status: 'active' | 'expired' | 'revoked';
  url: string;
  expires_at: string | null;
  created_at: string | null;
  revoked_at: string | null;
  download_count: number;
  last_downloaded_at: string | null;
}

export interface FileShareListResponse {
  total: number;
  items: FileShareItem[];
}

export interface FileShareMeta {
  share_id: string;
  file_name: string;
  file_ext: string;
  file_size: number;
  mime_type: string;
  access_mode: 'public' | 'secret';
  status: 'active' | 'expired' | 'revoked';
  expires_at: string | null;
  created_at: string | null;
  owner_display: string;
  view_count: number;
  download_count: number;
  /**
   * 呈现方式由后端决定（`classify_preview_kind`），前端不重复一套扩展名清单。
   * 两边各写一套「哪些算文本」，迟早出现「列表说能看、打开变下载」。
   */
  preview_kind: FilePreviewKind;
}

/** html 进沙箱 iframe；svg/image 用 <img>；pdf 用 iframe；text 拉文本渲染；download 只能下载。 */
export type FilePreviewKind = 'html' | 'svg' | 'image' | 'pdf' | 'text' | 'download';

export interface FileShareTextResult {
  content: string;
  truncated: boolean;
  max_bytes: number;
  file: FileShareMeta;
}

export interface FileShareUnlockResult {
  token: string;
  expires_in: number;
}

export type AuthHeaders = () => Record<string, string> | undefined;

function ownerHeaders(getHeaders?: AuthHeaders): Record<string, string> {
  return { ...(getHeaders?.() || {}) };
}

/** 创建文件分享（所有者）。 */
export async function createFileShare(
  payload: CreateFileSharePayload,
  getHeaders?: AuthHeaders,
): Promise<FileShareItem> {
  const resp = await request.post<StandardResponse<FileShareItem>>(
    '/api/portal/file-shares',
    payload,
    { headers: ownerHeaders(getHeaders) },
  );
  return resp.data.data;
}

/** 我分享出去的文件链接（所有者）。 */
export async function listFileShares(
  params: { include_revoked?: boolean } = {},
  getHeaders?: AuthHeaders,
): Promise<FileShareListResponse> {
  const resp = await request.get<StandardResponse<FileShareListResponse>>(
    '/api/portal/file-shares',
    { params, headers: ownerHeaders(getHeaders) },
  );
  return resp.data.data;
}

/** 作废文件分享（所有者，幂等）。 */
export async function revokeFileShare(
  shareId: string,
  getHeaders?: AuthHeaders,
): Promise<boolean> {
  const resp = await request.post<StandardResponse<{ revoked?: boolean }>>(
    `/api/portal/file-shares/${encodeURIComponent(shareId)}/revoke`,
    {},
    { headers: ownerHeaders(getHeaders) },
  );
  return Boolean(resp.data.data?.revoked);
}

/** 访客读取元信息：不计数，用于判断是否需要密码。 */
export async function fetchFileShareMeta(shareId: string): Promise<FileShareMeta> {
  const resp = await visitorClient.get<StandardResponse<FileShareMeta>>(
    `/api/v1/share/file/${encodeURIComponent(shareId)}`,
  );
  return resp.data.data;
}

/**
 * 拉取文本内容供页内渲染。
 *
 * 走 JSON 而不是直接读原始字节：能拿到 `truncated` 标记，也避免浏览器把 `.md`
 * 当纯文本附件下载。超过 2 MB 后端只返回前 2 MB 并置位 `truncated`。
 */
export async function fetchFileShareText(
  shareId: string,
  token?: string,
): Promise<FileShareTextResult> {
  const resp = await visitorClient.get<StandardResponse<FileShareTextResult>>(
    `/api/v1/share/file/${encodeURIComponent(shareId)}/text`,
    { headers: token ? { 'X-Share-Token': token } : {} },
  );
  return resp.data.data;
}

/**
 * 页内渲染的来源地址（html / 图片 / pdf 的 iframe 或 <img> 用它）。
 *
 * 令牌只能走 query：`<iframe src>` 与 `<img src>` 都无法自定义请求头。
 */
export function sharedFileRawUrl(shareId: string, token?: string): string {
  const base = `/api/v1/share/file/${encodeURIComponent(shareId)}/raw`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

/** 访客用访问密码换短期令牌（可能 401 密码错 / 429 尝试过多 / 404 已失效）。 */
export async function unlockFileShare(
  shareId: string,
  secret: string,
): Promise<FileShareUnlockResult> {
  const resp = await visitorClient.post<StandardResponse<FileShareUnlockResult>>(
    `/api/v1/share/file/${encodeURIComponent(shareId)}/unlock`,
    { secret },
  );
  return resp.data.data;
}

/**
 * 拼下载地址，交给浏览器直接打开。
 *
 * 令牌走 query 而不是请求头：浏览器直接打开链接时无法自定义请求头，而下载接口已经支持
 * `?token=`。代价是令牌会进入浏览器历史——令牌本身就是短期（≤12 小时）且绑 share_id 的，
 * 这个取舍与会话分享「令牌走请求头」不同，是因为下载场景根本没有得选。
 */
export function downloadSharedFileUrl(shareId: string, token?: string): string {
  const base = `/api/v1/share/file/${encodeURIComponent(shareId)}/download`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}
