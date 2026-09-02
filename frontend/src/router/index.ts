import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import Dashboard from '../views/Dashboard.vue'
import Overview from '../views/Overview.vue'
import AuditLogs from '../views/AuditLogs.vue'
import Playground from '../views/Playground.vue'
import AgentDebug from '../views/AgentDebug.vue'

import Users from '../views/Users.vue'
import SystemConfig from '../views/SystemConfig.vue'
import PersonalCenter from '../views/PersonalCenter.vue'
import MetadataDatasets from '../views/MetadataDatasets.vue'
import MetadataTables from '../views/MetadataTables.vue'
import AgentManagement from '../views/AgentManagement.vue'
import PromptStudio from '../views/PromptStudio.vue'
import ChatLogs from '../views/ChatLogs.vue'

import NoPermission from '../views/NoPermission.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: Login,
      meta: { title: '登录' }
    },
    {
      path: '/no-permission',
      name: 'NoPermission',
      component: NoPermission,
      meta: { title: '无权限' }
    },
    {
      path: '/embed',
      component: () => import('../layouts/EmbedLayout.vue'),
      children: [
        {
          path: 'chat',
          name: 'EmbedChat',
          component: () => import('../views/EmbedChat.vue'),
          meta: { public: true, title: '嵌入式对话' } 
        }
      ]
    },
    {
      path: '/dashboard',
      component: Dashboard,
      children: [
        {
          path: '',
          name: 'Overview',
          component: Overview,
          meta: { title: '概览', perm: 'menu:dashboard' }
        },
        {
          path: 'users',
          name: 'Users',
          component: Users,
          meta: { perm: 'menu:system:users', title: '用户管理' }
        },
        {
          path: 'roles',
          name: 'Roles',
          component: () => import('../views/Roles.vue'),
          meta: { perm: 'menu:system:roles', title: '角色管理' }
        },
        {
          path: 'system',
          name: 'System',
          component: SystemConfig,
          meta: { perm: 'menu:system:config', title: '系统配置' }
        },
        {
          path: 'audit',
          name: 'Audit',
          component: AuditLogs,
          meta: { perm: 'menu:system:audit', title: '审计日志' }
        },
        {
          path: 'token-stats',
          name: 'TokenStats',
          component: () => import('../views/TokenStats.vue'),
          meta: { perm: 'menu:system:audit', title: 'Token 统计' }
        },
        {
          path: 'chat-logs',
          name: 'ChatLogs',
          component: ChatLogs,
          meta: { perm: 'menu:chat_logs', title: '聊天日志' }
        },
        {
          path: 'playground',
          name: 'Playground',
          component: Playground,
          meta: { perm: 'menu:playground', title: '接口调试台' }
        },
        {
          path: 'agent-debug',
          name: 'AgentDebug',
          component: AgentDebug,
          meta: { perm: 'menu:agent_debug', title: '智能体调试' }
        },
        {
          path: 'widget-debug',
          name: 'WidgetDebugger',
          component: () => import('../views/WidgetDebugger.vue'),
          meta: { perm: 'menu:widget_debug', title: '组件调试台' }
        },
        {
          path: 'workbench',
          name: 'PersonalWorkbench',
          component: () => import('../views/PersonalWorkbench.vue'),
          meta: { title: '我的工作台' }
        },
        {
          path: 'personal',
          name: 'PersonalCenter',
          component: PersonalCenter,
          meta: { title: '个人中心' }
        },
        {
          path: 'metadata',
          name: 'Metadata',
          component: MetadataDatasets,
          meta: { perm: 'menu:metadata', title: '元数据管理' }
        },
        {
          path: 'metadata/:id',
          name: 'MetadataTables',
          component: MetadataTables,
          meta: { perm: 'menu:metadata', title: '表详情' }
        },
        {
          path: 'data-sources',
          name: 'DataSourceManagement',
          component: () => import('../views/DataSourceManagement.vue'),
          meta: { perm: 'menu:data_sources', title: '数据源管理' }
        },
        {
          path: 'agent-management',
          name: 'AgentManagement',
          component: AgentManagement,
          meta: { perm: 'menu:agent_management', title: '智能体中心' }
        },
        {
          path: 'scenario-templates',
          name: 'ScenarioTemplates',
          component: () => import('../views/ScenarioTemplates.vue'),
          meta: { perm: 'menu:ai_chat', title: '场景模板' }
        },
        {
          path: 'scenario-templates/:templateId',
          name: 'ScenarioTemplateDetail',
          component: () => import('../views/ScenarioTemplateDetail.vue'),
          meta: { perm: 'menu:ai_chat', title: '模板详情' }
        },
        {
          path: 'scenario-templates/:templateId/install',
          name: 'ScenarioTemplateInstall',
          component: () => import('../views/ScenarioTemplateInstall.vue'),
          meta: { perm: 'menu:agent_management', title: '交付向导' }
        },
        {
          path: 'examples',
          name: 'ExampleManagement',
          component: () => import('../views/ExampleManagement.vue'),
          meta: { perm: 'menu:chatbi_examples', title: '案例集管理' }
        },
        {
          path: 'knowledge-bases',
          name: 'KnowledgeBaseManagement',
          component: () => import('../views/KnowledgeBaseManagement.vue'),
          meta: { perm: 'menu:knowledge_management', title: '知识库管理' }
        },
        {
          path: 'knowledge-retrieval-test',
          name: 'KnowledgeRetrievalTest',
          component: () => import('../views/KnowledgeRetrievalTest.vue'),
          meta: { perm: 'menu:knowledge_retrieval_test', title: '检索测试' }
        },
        {
          path: 'knowledge-metrics',
          name: 'KnowledgeMetrics',
          component: () => import('../views/KnowledgeMetrics.vue'),
          meta: { perm: 'menu:knowledge_management', title: '运营分析' }
        },
        {
          path: 'prompts',
          name: 'PromptStudio',
          component: PromptStudio,
          meta: { perm: 'menu:prompts', title: '提示词工坊' }
        },
        {
          path: 'skills',
          name: 'SkillsManagement',
          component: () => import('../views/SkillsManagement.vue'),
          meta: { perm: 'menu:skills_management', title: '技能工作台' }
        },
        {
          path: 'mcp',
          name: 'McpManagement',
          component: () => import('../views/McpManagement.vue'),
          meta: { perm: 'menu:mcp_management', title: 'MCP 工具集' }
        },
        {
          path: 'mcp-service',
          name: 'McpServiceDesk',
          component: () => import('../views/McpServiceDesk.vue'),
          meta: { perm: 'menu:mcp_service', title: 'MCP 服务台' }
        },
        {
          path: 'memory',
          name: 'MemoryManagement',
          component: () => import('../views/MemoryManagement.vue'),
          meta: { perm: 'menu:memory_management', title: '记忆工作台' }
        },

        {
          path: 'tasks',
          name: 'TaskCenter',
          component: () => import('../views/TaskCenter.vue'),
          meta: { perm: 'menu:task_center', title: '任务调度台' }
        },
        {
          path: 'chat',
          name: 'AIChat',
          component: () => import('../views/Chat.vue'),
          meta: { perm: 'menu:ai_chat', title: '智能助手' }
        }
      ]
    },
    {
      path: '/',
      redirect: '/dashboard'
    }
  ]
})

router.beforeEach((to: any, _from: any, next: any) => {
  const userInfoStr = localStorage.getItem('user_info')
  const isAuthenticated = !!userInfoStr
  
  if (to.meta.public) {
    next()
    return
  }

  if (to.name !== 'Login' && !isAuthenticated) {
    next({ name: 'Login' })
    return
  }

  if (isAuthenticated && to.name !== 'NoPermission' && to.name !== 'Login') {
    try {
      const userInfo = JSON.parse(userInfoStr!)
      
      // Admin bypass
      if (userInfo.role === 'admin') {
        next()
        return
      }

      const userMenus = userInfo.permissions?.menus || []
      const isPublicWorkbench = to.name === 'PersonalWorkbench'
      
      // 1. 本地会话没有任何菜单权限 → 无权限页（「尝试刷新」会重新拉取 /me）
      if (userMenus.length === 0 && !isPublicWorkbench) {
        next({ name: 'NoPermission' })
        return
      }

      // 2. 缺目标页权限时落到第一个有权限的页面，避免误送无权限页
      const requiredPerm = to.meta.perm
      if (requiredPerm && !userMenus.includes(requiredPerm)) {
        console.warn(`[Guard] Access denied to ${to.path}. Missing ${requiredPerm}`)
        const fallback = resolveFirstAllowedRoute(userMenus)
        if (fallback.name === to.name) {
          next({ name: 'NoPermission' })
        } else {
          next(fallback)
        }
        return
      }
    } catch (e) {
      console.error("Router guard parse error", e)
    }
  }

  next()
})

/** 菜单权限 → 默认落地路由（按业务优先级） */
const MENU_HOME_CANDIDATES: Array<{ perm: string; name: string }> = [
  { perm: 'menu:ai_chat', name: 'PersonalWorkbench' },
  { perm: 'menu:dashboard', name: 'Overview' },
  { perm: 'menu:agent_management', name: 'AgentManagement' },
  { perm: 'menu:skills_management', name: 'SkillsManagement' },
  { perm: 'menu:mcp_management', name: 'McpManagement' },
  { perm: 'menu:mcp_service', name: 'McpServiceDesk' },
  { perm: 'menu:chat_logs', name: 'ChatLogs' },
  { perm: 'menu:metadata', name: 'Metadata' },
  { perm: 'menu:task_center', name: 'TaskCenter' },
]

const resolveFirstAllowedRoute = (userMenus: string[]) => {
  for (const item of MENU_HOME_CANDIDATES) {
    if (userMenus.includes(item.perm)) {
      return { name: item.name }
    }
  }
  return { name: 'PersonalCenter' }
}

export default router
