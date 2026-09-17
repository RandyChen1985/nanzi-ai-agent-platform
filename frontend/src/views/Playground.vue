<script setup lang="ts">
import { ApiReference } from '@scalar/api-reference'
import '@scalar/api-reference/style.css'
import { computed, ref, onMounted } from 'vue'

// 门户凭据由同源 HttpOnly Cookie 承载，前端不再从 localStorage 读取。
// token 为空时，Scalar 的 Try-it 请求仍会携带同源 Cookie。
const apiKey = ref('')
const specContent = ref<any>(null)
const authError = ref(false)

onMounted(async () => {
  try {
    const response = await fetch('/openapi.json')
    if (!response.ok) throw new Error('Failed to fetch openapi.json')
    const spec = await response.json()
    
    // Filter paths to only include those starting with /api/v1
    const filteredPaths: Record<string, any> = {}
    if (spec.paths) {
      Object.keys(spec.paths).forEach(path => {
        if (path.startsWith('/api/v1')) {
          filteredPaths[path] = spec.paths[path]
        }
      })
      spec.paths = filteredPaths
    }
    
    specContent.value = spec
  } catch (e) {
    console.error('Failed to load or filter openapi.json', e)
    // If we get a 401/403 here, it might mean the session is actually invalid
    authError.value = true
  }
})

const configuration = computed(() => ({
  spec: {
    content: specContent.value,
  },
  authentication: {
    preferredSecurityScheme: 'APIKeyHeader',
    apiKey: {
      token: apiKey.value
    }
  },
  theme: 'purple',
  hideDownloadButton: true
} as const))

const goToLogin = () => {
  localStorage.removeItem('api_key')
  localStorage.removeItem('user_info')
  window.location.href = '/login'
}
</script>

<template>
  <div class="bg-white rounded-lg shadow min-h-full flex flex-col">
    <!-- Auth Error State -->
    <div v-if="authError" class="flex-1 flex flex-col items-center justify-center p-12 text-center">
      <div class="w-16 h-16 bg-red-50 text-red-500 rounded-full flex items-center justify-center mb-6 shadow-sm border border-red-100">
        <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m0 4h.01M5.071 19.071A9 9 0 1118.929 5.071M12 9v4m-7.071 3.071L12 12V9" />
        </svg>
      </div>
      <h3 class="text-lg font-bold text-gray-800 mb-2">登录已超时或凭证失效</h3>
      <p class="text-sm text-gray-500 max-w-sm mb-8 leading-relaxed">
        为了保障您的数据安全，API 调试功能需要有效的验证凭证。请尝试重新登录系统后再进行操作。
      </p>
      <button 
        @click="goToLogin"
        class="px-6 py-2.5 bg-primary text-white text-sm font-bold rounded-xl hover:bg-primary-dark transition-all shadow-lg shadow-primary/20 flex items-center"
      >
        <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
        </svg>
        重新登录系统
      </button>
    </div>

    <!-- loading State -->
    <div v-else-if="!specContent" class="flex-1 flex flex-col items-center justify-center p-12 text-center text-gray-400">
      <div class="w-10 h-10 border-4 border-gray-100 border-t-primary rounded-full animate-spin mb-4"></div>
      <p class="text-sm font-medium">正在解析 API 定义文件...</p>
    </div>

    <!-- Normal Content -->
    <ApiReference v-else :configuration="configuration" />
  </div>
</template>

<style scoped>
/* Removed h-full and overflow-hidden to allow natural scrolling within the dashboard content area */

/* Localization Overrides */

/* "Test Request" Button Styling */
:deep(.show-api-client-button) {
  background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%) !important;
  color: white !important;
  border-radius: 12px !important;
  font-weight: 700 !important;
  box-shadow: 0 4px 15px -3px rgba(99, 102, 241, 0.3) !important;
  border: none !important;
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1) !important;
  padding: 10px 20px !important;
}

:deep(.show-api-client-button:hover) {
  background: linear-gradient(135deg, #4f46e5 0%, #9333ea 100%) !important;
  transform: translateY(-1px) scale(1.02);
  box-shadow: 0 10px 20px -5px rgba(99, 102, 241, 0.4) !important;
}

:deep(.show-api-client-button span) {
  font-size: 0 !important;
}
:deep(.show-api-client-button span::after) {
  content: '调试接口';
  font-size: 14px;
}

/* "Body" Header */
:deep(.request-body-title) {
    font-size: 0 !important;
}
:deep(.request-body-title::after) {
    content: '请求体';
    font-size: 13px;
    font-weight: 500;
}

/* Generic Headers (Responses, Parameters) - This is tricky as classes are generic */
/* Attempting to target via text content is impossible directly in CSS. */
/* We will target specific containers where we know the order or structure */

/* Responses Header often has specific classes */
/* .text-c-1.mt-3.mb-3.leading-\[1\.45\].font-medium */
/* Since we can't be precise without unique classes, we will skip riskier generic overrides */
/* But "Operation" title sections might be targetable */

:deep(.scalar-card-header-title) {
  font-size: 0 !important;
}
:deep(.scalar-card-header-title::after) {
  content: '接口定义'; /* Or 'Operations' translated */
  font-size: 1rem;
}
</style>
