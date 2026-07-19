/** Parse create_skills success marker from tool output. */

export type SkillCreatedInfo = {
  skill_id: string
  scope: 'personal' | 'global'
  name: string
}

const MARKER_RE = /NANZI_SKILL_CREATED:(\{.*?\})(?:\s|$)/

export function parseSkillCreatedMarker(text: string | null | undefined): SkillCreatedInfo | null {
  if (!text) return null
  const match = MARKER_RE.exec(String(text))
  if (!match) return null
  try {
    const payload = JSON.parse(match[1])
    const skillId = String(payload.skill_id || '').trim()
    if (!skillId) return null
    const scope = String(payload.scope || 'personal').toLowerCase() === 'global' ? 'global' : 'personal'
    return {
      skill_id: skillId,
      scope,
      name: String(payload.name || skillId),
    }
  } catch {
    return null
  }
}

export function personalSkillsEditUrl(skillId: string): string {
  const params = new URLSearchParams({ tab: 'skills', skill_id: skillId })
  return `/dashboard/personal?${params.toString()}`
}
