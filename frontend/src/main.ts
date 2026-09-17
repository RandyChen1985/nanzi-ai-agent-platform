import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from '@/App.vue'
import router from '@/router'

import axios from 'axios'

// 管理后台页面（含各类抽屉/弹窗组件）会直接使用全局 axios 实例。
// 这里统一补上 X-API-Key，避免这些请求只依赖 admin_token Cookie 而在会话边界返回 401。
// 嵌入页面的凭据由 EmbedChat 自行注入，此处不介入。
axios.interceptors.request.use((config) => {
  if (!config.headers) {
    config.headers = {} as any
  }
  const hasAuth = config.headers['X-API-Key'] || config.headers['Authorization']
  if (!hasAuth && !window.location.pathname.startsWith('/embed/')) {
    const apiKey = localStorage.getItem('api_key')
    if (apiKey) {
      config.headers['X-API-Key'] = apiKey
    }
  }
  return config
})

// Global Axios Interceptor for 401 Unauthorized
axios.interceptors.response.use(
  response => response,
  error => {
    if (error.response && error.response.status === 401) {
      // In embedded mode or mcp probe/playground, we don't want to force redirect to login
      // instead, we let the component handle the state (e.g. showing "No Permission")
      if (
        window.location.pathname.startsWith('/embed/') ||
        (error.config?.url?.startsWith('/mcp/') && !error.config?.url?.includes('/portal/')) ||
        error.config?.headers?.['X-Ignore-Auth-Redirect']
      ) {
        console.warn("[Auth] 401 error suppressed for embed or probe request");
        return Promise.reject(error);
      }

      // Clear local storage and redirect to login
      localStorage.removeItem('api_key')
      localStorage.removeItem('user_info')
      localStorage.removeItem('admin_token')
      localStorage.removeItem('yovole_token')
      document.cookie = 'admin_token=; path=/; max-age=0; samesite=lax'
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 注册全局权限指令 v-has-perm
app.directive('has-perm', {
  mounted(el, binding) {
    const { value } = binding;
    if (!value) return;

    const userInfoStr = localStorage.getItem('user_info');
    
    const applyDisabled = () => {
      el.disabled = true;
      el.classList.add('opacity-50', 'cursor-not-allowed', 'filter', 'grayscale-[0.5]');
      el.title = '暂无操作权限';
      // 阻止所有点击事件
      el.style.pointerEvents = 'none';
    };

    if (!userInfoStr) {
      applyDisabled();
      return;
    }

    try {
      const userInfo = JSON.parse(userInfoStr);
      if (userInfo.role === 'admin') return;

      // Backwards compatibility: check for flat list first, then legacy elements object
      const permissions = userInfo.permissions || [];
      const hasPermission = Array.isArray(permissions) 
        ? permissions.includes(value)
        : permissions.elements?.includes(value);

      if (!hasPermission) {
        applyDisabled();
      }
    } catch (e) {
      applyDisabled();
    }
  }
})

app.mount('#app')

// 拉取平台时区（公开配置），供任务中心等展示对齐业务时区
void import('@/utils/axios')
  .then(({ default: api }) => api.get('/api/portal/auth/config/public'))
  .then((response) => {
    const tz = response.data?.data?.platform_timezone
    if (tz) {
      return import('@/utils/platformTimezone').then(({ setPlatformTimezone }) => {
        setPlatformTimezone(tz)
      })
    }
  })
  .catch(() => {
    /* ignore bootstrap failures */
  })
