/**
 * 资源广场与资源访问申请接口。
 *
 * 统一在这里提取后端 `detail` 文案：后端用 400 + detail 表达业务规则
 * （「你已经提交过申请，正在等待审批」这类），直接把 error.message
 * 抛给弹窗会显示 "Request failed with status code 400"。
 */
import type { AxiosError } from 'axios'

import axios from '@/utils/axios'
import type {
  AccessRequestItem,
  DatasetReadonly,
  KnowledgeReadonly,
  SquareApplyPayload,
  SquareDatasetPage,
  SquareKnowledgePage,
  ResourceType,
} from '@/types/resourceSquare'

const BASE = '/api/portal'

function messageOf(error: unknown, fallback: string): string {
  const detail = (error as AxiosError<{ detail?: string }>)?.response?.data?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

function statusOf(error: unknown): number | undefined {
  return (error as AxiosError)?.response?.status
}

export { messageOf as resourceSquareErrorMessage, statusOf as resourceSquareErrorStatus }

export async function fetchSquareKnowledge(params: {
  keyword?: string
  visibility?: string
  status?: string
  page?: number
  size?: number
}): Promise<SquareKnowledgePage> {
  try {
    const resp = await axios.get(`${BASE}/resource-square/knowledge`, { params })
    return resp.data.data as SquareKnowledgePage
  } catch (error) {
    throw new Error(messageOf(error, '读取知识库列表失败'))
  }
}

export async function fetchSquareDatasets(params: {
  keyword?: string
  status?: string
  page?: number
  size?: number
}): Promise<SquareDatasetPage> {
  try {
    const resp = await axios.get(`${BASE}/resource-square/datasets`, { params })
    return resp.data.data as SquareDatasetPage
  } catch (error) {
    throw new Error(messageOf(error, '读取数据集列表失败'))
  }
}

export async function applyResourceAccess(
  payload: SquareApplyPayload,
): Promise<{ id: number; status: string }> {
  try {
    const resp = await axios.post(`${BASE}/access-requests`, payload)
    return resp.data.data as { id: number; status: string }
  } catch (error) {
    throw new Error(messageOf(error, '提交申请失败'))
  }
}

export async function cancelAccessRequest(requestId: number): Promise<void> {
  await axios.post(`${BASE}/access-requests/${requestId}/cancel`)
}

export async function fetchMyAccessRequests(resourceType?: ResourceType): Promise<AccessRequestItem[]> {
  const resp = await axios.get(`${BASE}/access-requests/mine`, {
    params: resourceType ? { resource_type: resourceType } : {},
  })
  return (resp.data.data?.items || []) as AccessRequestItem[]
}

export async function fetchPendingAccessRequests(
  resourceType?: ResourceType | 'all',
): Promise<AccessRequestItem[]> {
  const resp = await axios.get(`${BASE}/access-requests/pending`, {
    params: resourceType && resourceType !== 'all' ? { resource_type: resourceType } : {},
  })
  return (resp.data.data?.items || []) as AccessRequestItem[]
}

export async function fetchReviewedAccessRequests(
  resourceType?: ResourceType | 'all',
): Promise<AccessRequestItem[]> {
  const resp = await axios.get(`${BASE}/access-requests/reviews`, {
    params: resourceType && resourceType !== 'all' ? { resource_type: resourceType } : {},
  })
  return (resp.data.data?.items || []) as AccessRequestItem[]
}

export async function decideAccessRequest(
  requestId: number,
  action: 'approve' | 'reject',
  comment?: string,
): Promise<void> {
  try {
    await axios.post(`${BASE}/access-requests/${requestId}/${action}`, {
      comment: comment?.trim() || null,
    })
  } catch (error) {
    throw new Error(messageOf(error, action === 'approve' ? '审批失败' : '驳回失败'))
  }
}

export async function fetchKnowledgeReadonly(
  datasetId: string,
  page = 1,
  size = 100,
): Promise<KnowledgeReadonly> {
  const resp = await axios.get(
    `${BASE}/resource-square/knowledge/${encodeURIComponent(datasetId)}`,
    { params: { page, size } },
  )
  const raw = (resp.data?.data ?? {}) as Partial<KnowledgeReadonly>
  // 模板里直接读 data.documents.length：少一个数组字段就是渲染期 TypeError，
  // 而渲染期抛错不走 catch —— 页面只剩面包屑、正文全白，最难查的一种坏法。
  // 这里把数组字段补齐，前端只负责画，不负责猜后端给没给。
  return {
    dataset_id: raw.dataset_id ?? datasetId,
    name: raw.name ?? '',
    description: raw.description ?? null,
    visibility: raw.visibility ?? 'private',
    owner: raw.owner ?? null,
    tags: Array.isArray(raw.tags) ? raw.tags : [],
    updated_at: raw.updated_at ?? null,
    documents: Array.isArray(raw.documents) ? raw.documents : [],
    documents_error: raw.documents_error === true,
    page: raw.page ?? page,
    size: raw.size ?? size,
  }
}

export async function fetchDatasetReadonly(datasetId: number): Promise<DatasetReadonly> {
  const resp = await axios.get(`${BASE}/resource-square/datasets/${datasetId}`)
  const raw = (resp.data?.data ?? {}) as Partial<DatasetReadonly>
  return {
    id: raw.id ?? datasetId,
    name: raw.name ?? '',
    display_name: raw.display_name ?? raw.name ?? '',
    description: raw.description ?? null,
    data_source: raw.data_source ?? null,
    tags: Array.isArray(raw.tags) ? raw.tags : [],
    quality_score: raw.quality_score ?? null,
    updated_at: raw.updated_at ?? null,
    relationship_count: raw.relationship_count ?? 0,
    tables: Array.isArray(raw.tables) ? raw.tables : [],
    metrics: Array.isArray(raw.metrics) ? raw.metrics : [],
  }
}
