/**
 * 技能广场接口（只读）。
 *
 * 只有两个 GET：目录与详情。没有「安装」接口——安装是把提示词复制到会话里，
 * 由智能体在沙箱执行 npx 后再用 `import_skill_from_sandbox` 收进技能库，
 * 所以这里永远不会写任何东西。
 */
import type { AxiosError } from 'axios'

import axios from '@/utils/axios'
import type { SkillGalleryDetail, SkillGalleryList } from '@/types/skillGallery'

const BASE = '/api/portal'

function messageOf(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    const raw = error.message
    // 未经包装的 axios 错误对用户没有信息量，交给 fallback
    if (!/^Request failed with status code \d+$/.test(raw)) return raw
  }
  const detail = (error as AxiosError<{ detail?: string }>)?.response?.data?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

export async function fetchSkillGallery(): Promise<SkillGalleryList> {
  try {
    const resp = await axios.get(`${BASE}/skills/gallery`)
    const data = resp.data?.data ?? resp.data
    return {
      skills: Array.isArray(data?.skills) ? data.skills : [],
      categories: Array.isArray(data?.categories) ? data.categories : [],
      source_note: typeof data?.source_note === 'string' ? data.source_note : '',
    }
  } catch (error) {
    throw new Error(messageOf(error, '读取技能广场失败'))
  }
}

export async function fetchSkillGalleryDetail(skillId: string): Promise<SkillGalleryDetail> {
  try {
    const resp = await axios.get(`${BASE}/skills/gallery/${encodeURIComponent(skillId)}`)
    const data = resp.data?.data ?? resp.data
    if (!data || typeof data !== 'object') throw new Error('技能详情为空')
    return data as SkillGalleryDetail
  } catch (error) {
    throw new Error(messageOf(error, '读取技能详情失败'))
  }
}

/**
 * 上报技能广场行为事件（打开详情 / 复制提示词 / 复制平台版）。
 *
 * 只落后端结构化日志，不建表；失败必须静默——埋点不能影响用户复制安装提示词。
 * 事件名走后端白名单，写错会 422，所以这里用联合类型在编译期挡一道。
 */
export type SkillGalleryEventName = 'detail_open' | 'prompt_copy' | 'platform_prompt_copy'

export async function reportSkillGalleryEvent(
  event: SkillGalleryEventName,
  skillId: string,
  scope: '' | 'personal' | 'global' = '',
): Promise<void> {
  try {
    await axios.post(`${BASE}/skills/gallery/events`, {
      event,
      skill_id: skillId,
      scope,
    })
  } catch {
    // 埋点失败不上浮：用户读到的是「已复制」，不是「上报失败」。
  }
}
