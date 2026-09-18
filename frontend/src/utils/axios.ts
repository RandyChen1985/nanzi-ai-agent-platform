/**
 * Axios 全局配置
 * 统一处理请求/响应拦截、错误处理
 */
import axios from 'axios'
import type { AxiosError, InternalAxiosRequestConfig } from 'axios'

// 创建 axios instance
const instance = axios.create({
  // 不需要 baseURL，Vite 代理会自动转发 /api 请求到后端
  timeout: 60000, // 默认提高到 1 分钟，复杂任务请在请求中手动覆盖
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true, // Auto send cookies
})

// 请求拦截器
instance.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // FormData 必须由浏览器自动带 multipart boundary；全局 application/json 会导致 file 字段丢失
    if (config.data instanceof FormData && config.headers) {
      delete config.headers['Content-Type']
    }

    // 门户认证统一依赖同源 HttpOnly Cookie（portal_session），不再从 localStorage 注入任何凭据：
    // 凭据不进 JS 可读的存储，即使发生 XSS 也无法带走会话凭据。
    // 嵌入场景的显式 Bearer 令牌由其自身按需注入（EmbedChat / BrowserPanel）。
    return config
  },
  (error: AxiosError) => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
instance.interceptors.response.use(
  (response) => {
    return response
  },
  (error: AxiosError) => {
    // 统一错误处理
    if (error.response) {
      const status = error.response.status
      const data: any = error.response.data
      
      switch (status) {
        case 401:
          // In embedded mode or explicit probe request, we don't want to force redirect or clear critical tokens immediately
          if (
            window.location.pathname.startsWith('/embed/') ||
            window.location.pathname.includes('EmbedChat') ||
            (error.config?.url?.startsWith('/mcp/') && !error.config?.url?.includes('/portal/')) ||
            error.config?.headers?.['X-Ignore-Auth-Redirect']
          ) {
            console.warn("[Auth] 401 unauthorized suppressed for embed or probe request.");
            break;
          }
          // 未授权，清除本地存储并跳转登录。
          //
          // 与 main.ts 的 401 拦截器同理：localStorage 清理只针对前端历史遗留副本，
          // 不构成登出；门户会话 Cookie 是 HttpOnly，document.cookie 无法增删，故不在此处
          // 尝试删 Cookie（会话失效只能由后端 logout 完成）。
          localStorage.removeItem('api_key')
          localStorage.removeItem('user_info')
          // 'admin_token' 是历史遗留的 localStorage 键名（非当前 Cookie 名），保留原样
          localStorage.removeItem('admin_token')
          localStorage.removeItem('yovole_token')
          window.location.href = '/login'
          break;
          
        case 403:
          console.error('权限不足:', data.detail || '您没有权限执行此操作')
          break
          
        case 404:
          console.error('资源未找到:', data.detail || '请求的资源不存在')
          break
          
        case 422:
          // 验证错误
          console.error('验证错误:', data.detail || '请求参数不正确')
          break
          
        case 500:
          console.error('服务器错误:', data.detail || '服务器内部错误，请稍后重试')
          break
          
        default:
          console.error('请求失败:', data.detail || `请求失败 (${status})`)
      }
    } else if (error.request) {
      // 请求已发出但没有收到响应
      console.error('网络错误:', '网络连接失败，请检查网络设置')
    } else {
      // 其他错误
      console.error('请求错误:', error.message)
    }
    
    return Promise.reject(error)
  }
)

export default instance
