/**
 * 凭据使用结果的展示口径。
 *
 * 这层映射曾经有三份：个人端明细、管理端明细、统计面板。三处对同一个
 * `denied_by_scenario` 分别写成「场景未启用」和「场景不符」，个人端还漏了
 * `denied_disabled`。纯展示口径没有分叉的理由，收敛到这里。
 */

export const RESULT_LABEL: Record<string, string> = {
  success: '成功',
  denied_by_binding: '未绑定',
  denied_by_grant: '未授权',
  denied_by_host: '域名不符',
  denied_by_scenario: '场景未启用',
  denied_by_script_policy: '脚本注入被关',
  denied_by_user_pref: '你已关闭该场景',
  denied_by_platform_policy: '平台已关闭该场景',
  denied_disabled: '已停用',
  denied_expired: '已过期',
  // 历史流水里出现过不带前缀的写法，留着才能正常显示老记录
  disabled: '已停用',
  expired: '已过期',
}

/** 翻成人话；认不出的就去掉 `denied_by_` 前缀原样展示，别显示空白。 */
export const resultLabel = (result: string): string =>
  RESULT_LABEL[result] || String(result || '').replace(/^denied_by_/, '') || result
