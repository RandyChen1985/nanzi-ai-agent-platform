import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from '@/App.vue'
import router from '@/router'

import axios from 'axios'

// 门户认证统一依赖同源 HttpOnly Cookie（admin_token），不再从 localStorage 注入 X-API-Key：
// 凭据不进 JS 可读的存储，即使发生 XSS 也无法带走会话凭据。
// 显式传入 X-API-Key / Authorization 的调用方不受影响（本文件不做任何覆盖）。
// 嵌入场景的凭据由 EmbedChat 自行注入。

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
