/**
 * 已安装徽标的统一文案。
 *
 * 面板卡片与详情抽屉显示的是同一个状态（`installed`），文案必须一致——之前两处
 * 各写一套（「已在你的技能库」/「已在你的个人技能库」），同一状态两种说法。
 *
 * 口径：只说明「库里有同名技能」。不声称来源一致，也不声称是最新版本
 * （判定是按目录名比对，见后端 `skill_gallery.py` 顶部说明）。
 */
import type { SkillInstalledScope } from '@/types/skillGallery'

export function installedLabel(scope: SkillInstalledScope | undefined): string {
  if (scope === 'personal') return '已在你的个人技能库'
  if (scope === 'global') return '平台技能库已收录'
  return ''
}
