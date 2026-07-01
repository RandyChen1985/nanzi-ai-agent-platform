import api from '@/utils/axios'
import { ref } from 'vue'
import {
  DEFAULT_BRANDING,
  DEFAULT_REPO_URL,
  type PublicBranding,
} from '@/constants/branding'

const branding = ref<PublicBranding>({ ...DEFAULT_BRANDING })
let loadPromise: Promise<PublicBranding> | null = null

function applyFavicon(iconUrl: string) {
  const href = iconUrl || DEFAULT_BRANDING.icon_url
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (!link) {
    link = document.createElement('link')
    link.rel = 'icon'
    document.head.appendChild(link)
  }
  link.href = href
}

export function applyDocumentTitle(pageTitle?: string) {
  const name = branding.value.product_name || DEFAULT_BRANDING.product_name
  document.title = pageTitle ? `${pageTitle} - ${name}` : name
}

export function resolveRepoUrl(): string {
  if (branding.value.hide_version_link) return ''
  return DEFAULT_REPO_URL
}

export async function loadBranding(force = false): Promise<PublicBranding> {
  if (!force && loadPromise) return loadPromise

  loadPromise = (async () => {
    try {
      const res = await api.get('/api/portal/auth/branding')
      const data = res.data?.data ?? res.data
      branding.value = { ...DEFAULT_BRANDING, ...data }
    } catch {
      branding.value = { ...DEFAULT_BRANDING }
    }
    applyFavicon(branding.value.icon_url)
    applyDocumentTitle()
    return branding.value
  })()

  return loadPromise
}

export function useBranding() {
  return {
    branding,
    loadBranding,
    applyDocumentTitle,
    applyFavicon,
    resolveRepoUrl,
  }
}
