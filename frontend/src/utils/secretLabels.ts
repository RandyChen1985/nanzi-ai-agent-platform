/**
 * 凭据使用结果的展示口径。
 *
 * 这层映射曾经有三份：个人端明细、管理端明细、统计面板。三处对同一个
 * `denied_by_scenario` 分别写成「场景未启用」和「场景不符」，个人端还漏了
 * `denied_disabled`。纯展示口径没有分叉的理由，收敛到这里。
 */

export const RESULT_LABEL: Record<string, string> = {
  success: '成功',
  // 规则未命中但回退到了同一层级的固定凭据：调用是成功的，但配置里有该修的东西。
  // 混进「成功」会让「配错了却一直能跑」的规则永远没人发现。
  success_fallback: '成功（规则未命中，已回退固定凭据）',
  denied_by_binding: '未绑定',
  denied_by_grant: '未授权',
  denied_by_host: '域名不符',
  denied_by_scenario: '场景未启用',
  denied_by_script_policy: '脚本注入被关',
  denied_by_user_pref: '你已关闭该场景',
  denied_by_platform_policy: '平台已关闭该场景',
  denied_disabled: '已停用',
  denied_expired: '已过期',
  // 规则没匹配上（动态凭据定位）：和「没绑定」是两回事，别混成同一个词
  rule_miss: '规则未命中',
  // 凭据值转不成参数声明的类型（端口写成「四百六十五」这种）
  type_mismatch: '类型不符',
  // 下面 7 条是「引用语法 / 取字段」这一类的失败。文案与后端
  // `app/services/secrets/injection.py::DENIAL_REASONS` 逐字一致：同一件事在
  // 调用错误信息里是中文、在流水页却是英文码，排查的人会对不上号。
  // 刻意都不复用「凭据没填」——那会让人去重新绑一次，而真正要改的是引用里的
  // key 或字段名。
  reference_mismatch: '引用里的凭据与这个槽位绑定的不是同一条',
  reference_field_missing: '引用指向的字段在凭据里不存在',
  reference_not_json: '凭据值是单值，不能按字段取用',
  reference_bad_syntax: '引用语法写错了',
  reference_unbound: '这个请求头里的凭据引用没有绑定',
  credential_unbound: '这个请求头里的凭据占位符没有绑定',
  structured_without_path: '这条凭据是结构化的，但没指定取哪个字段',
  reference_unresolved: '凭据取到了，但没取出要注入的值',
  // 历史流水里出现过不带前缀的写法，留着才能正常显示老记录
  disabled: '已停用',
  expired: '已过期',
}

/** 翻成人话；认不出的就去掉 `denied_by_` 前缀原样展示，别显示空白。 */
export const resultLabel = (result: string): string =>
  RESULT_LABEL[result] || String(result || '').replace(/^denied_by_/, '') || result
