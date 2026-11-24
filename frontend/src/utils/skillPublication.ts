/**
 * 「我的技能」提交平台审核的文案与状态口径。
 *
 * 抽成纯函数是为了可测：这层文案要如实说清三件事，任何一条漏了都会让作者做出错误判断——
 *   ① 提交的是**个人目录当前内容**的快照（不是他以为的"某个版本"）；
 *   ② 通过后平台版号会变成几（`PUBLISHED` 时是 v(N+1)，首次上架是 v1）；
 *   ③ 提交后还能撤销（卡片上会出现「撤销审核」）。
 *
 * 另外两处历史缺陷也在这里收口：
 *   - `REVOKED`（管理员下架）此前落进「提交为平台技能」分支，但后端会复用同一条
 *     publication 记录、版本号继续 +1 —— 文案让人以为"从头申请"，实际是"重新上架"。
 *   - `PUBLISHED` 且有 `pending_version`（徽标显示「新版审核中」）时，按钮仍显示「提交新版本」，
 *     点下去后端按「已有不同版本在审核中」返回 409。是否处于待审必须把 `pending_version`
 *     一起算进来，不能只看 `publication_status`。
 */

export interface SkillSubmissionLike {
  id?: string | null
  path?: string | null
  publication_status?: string | null
  current_public_version?: number | null
  pending_version?: number | null
  last_review_comment?: string | null
}

export interface SkillSubmissionCopy {
  title: string
  message: string
}

/** 已有待审版本：`PENDING`，或已上架但又提交了一版（`publication_status` 仍是 `PUBLISHED`）。 */
export function isSkillSubmissionPending(
  skill: SkillSubmissionLike | null | undefined,
): boolean {
  if (!skill) return false
  return (
    skill.publication_status === 'PENDING' || Number(skill.pending_version || 0) > 0
  )
}

/** 卡片按钮文案。`PENDING` 由界面渲染成「撤销审核」，不走这里。 */
export function skillPublicationActionLabel(status?: string | null): string {
  if (status === 'PUBLISHED' || status === 'REJECTED') return '提交新版本'
  if (status === 'REVOKED') return '重新提交上架'
  return '提交为平台技能'
}

/**
 * 提交前的确认弹窗文案。
 *
 * 多行正文（`ConfirmModal` 用 `whitespace-pre-line` 渲染）：第一行说清"提交什么"，
 * 第二行说清"通过后变成什么版本"，第三行说清"可以撤销"。
 */
export function buildSkillSubmissionConfirm(
  skill: SkillSubmissionLike | null | undefined,
): SkillSubmissionCopy {
  const id = String(skill?.id || '').trim()
  const path = String(skill?.path || id || '').trim() || id
  const status = String(skill?.publication_status || '')
  const current = Number(skill?.current_public_version || 0)
  const next = current + 1
  const comment = String(skill?.last_review_comment || '').trim()
  const source = `当前个人技能目录（${path}）`

  const lines: string[] = []
  if (status === 'REVOKED') {
    lines.push(
      comment ? `该技能当前已下架，下架原因：${comment}。` : '该技能当前已下架。',
    )
    lines.push(`将把${source}打包成快照重新提交审核。`)
    lines.push(`管理员通过后平台版将重新上架为 v${next}。`)
  } else if (status === 'REJECTED') {
    if (comment) lines.push(`上次审核被驳回：${comment}。`)
    lines.push(`将把${source}打包成快照重新提交审核。`)
    lines.push(`管理员通过后平台版将更新为 v${next}。`)
  } else if (current > 0) {
    lines.push(`将把${source}打包成快照提交审核。`)
    lines.push(
      `管理员通过后平台版将从 v${current} 升级为 v${next}；审核期间线上仍是 v${current}。`,
    )
  } else {
    lines.push(`将把${source}打包成快照提交管理员审核。`)
    lines.push('管理员通过后该技能会作为平台技能上架（v1）。')
  }
  lines.push('个人源文件与本地编辑不受影响；提交后可在卡片上点「撤销审核」。')

  const title =
    status === 'REVOKED'
      ? '重新提交上架审核'
      : status === 'REJECTED'
        ? '重新提交审核'
        : current > 0
          ? '提交新版本审核'
          : '提交为平台技能审核'

  return { title, message: lines.join('\n') }
}

/** 提交成功的提示：已经有过审核链路的，说「新版本」比「已提交」更准确。 */
export function skillSubmissionSuccessToast(status?: string | null): string {
  if (status === 'REVOKED') return '已重新提交，等待管理员审核'
  if (status === 'PUBLISHED' || status === 'REJECTED') return '新版本已提交管理员审核'
  return '已提交管理员审核'
}
