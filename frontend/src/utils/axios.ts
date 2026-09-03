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

    // 只有当请求头中不存在 API 凭据相关字段时，才自动从 localStorage 添加
    const hasAuth = config.headers['X-API-Key'] || config.headers['Authorization'];
    
    if (!hasAuth) {
      // 1. 尝试读取 API Key (用于管理后台)
      const apiKey = localStorage.getItem('api_key')
      if (apiKey && config.headers) {
        config.headers['X-API-Key'] = apiKey
      }
      
      // 2. 尝试读取 JWT Token (用于 EmbedChat 集成模式)
      const token = localStorage.getItem('yovole_token') || localStorage.getItem('admin_token')
      if (token && config.headers && !config.headers['X-API-Key']) {
        config.headers['Authorization'] = `Bearer ${token}`
      }
    }
    
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
          // 未授权，清除本地存储并跳转登录
          localStorage.removeItem('api_key')
          localStorage.removeItem('user_info')
          localStorage.removeItem('admin_token')
          localStorage.removeItem('yovole_token')
          document.cookie = 'admin_token=; path=/; max-age=0; samesite=lax'
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
