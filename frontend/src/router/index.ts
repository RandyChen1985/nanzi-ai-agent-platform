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
      component: Login
    },
    {
      path: '/no-permission',
      name: 'NoPermission',
      component: NoPermission
    },
    {
      path: '/embed',
      component: () => import('../layouts/EmbedLayout.vue'),
      children: [
        {
          path: 'chat',
          name: 'EmbedChat',
          component: () => import('../views/EmbedChat.vue'),
          meta: { public: true } 
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
          meta: { perm: 'menu:dashboard' }
        },
        {
          path: 'users',
          name: 'Users',
          component: Users,
          meta: { perm: 'menu:system:users' }
        },
        {
          path: 'roles',
          name: 'Roles',
          component: () => import('../views/Roles.vue'),
          meta: { perm: 'menu:system:roles' }
        },
        {
          path: 'system',
          name: 'System',
          component: SystemConfig,
          meta: { perm: 'menu:system:config' }
        },
        {
          path: 'audit',
          name: 'Audit',
          component: AuditLogs,
          meta: { perm: 'menu:system:audit' }
        },
        {
          path: 'token-stats',
          name: 'TokenStats',
          component: () => import('../views/TokenStats.vue'),
          meta: { perm: 'menu:system:audit' }
        },
        {
          path: 'chat-logs',
          name: 'ChatLogs',
          component: ChatLogs,
          meta: { perm: 'menu:chat_logs' }
        },
        {
          path: 'playground',
          name: 'Playground',
          component: Playground,
          meta: { perm: 'menu:playground' }
        },
        {
          path: 'agent-debug',
          name: 'AgentDebug',
          component: AgentDebug,
          meta: { perm: 'menu:agent_debug' }
        },
        {
          path: 'widget-debug',
          name: 'WidgetDebugger',
          component: () => import('../views/WidgetDebugger.vue'),
          meta: { perm: 'menu:widget_debug' }
        },
        {
          path: 'personal',
          name: 'PersonalCenter',
          component: PersonalCenter
        },
        {
          path: 'metadata',
          name: 'Metadata',
          component: MetadataDatasets,
          meta: { perm: 'menu:metadata' }
        },
        {
          path: 'metadata/:id',
          name: 'MetadataTables',
          component: MetadataTables,
          meta: { perm: 'menu:metadata' }
        },
        {
          path: 'data-sources',
          name: 'DataSourceManagement',
          component: () => import('../views/DataSourceManagement.vue'),
          meta: { perm: 'menu:data_sources' }
        },
        {
          path: 'agent-management',
          name: 'AgentManagement',
          component: AgentManagement,
          meta: { perm: 'menu:agent_management' }
        },
        {
          path: 'chatbi-examples',
          name: 'ChatBIExampleManagement',
          component: () => import('../views/ChatBIExampleManagement.vue'),
          meta: { perm: 'menu:chatbi_examples' }
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
          meta: { perm: 'menu:prompts' }
        },
        {
          path: 'skills',
          name: 'SkillsManagement',
          component: () => import('../views/SkillsManagement.vue'),
          meta: { perm: 'menu:skills_management', title: '技能工作台' }
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
          meta: { perm: 'menu:task_center' }
        },
        {
          path: 'chat',
          name: 'AIChat',
          component: () => import('../views/Chat.vue'),
          meta: { perm: 'menu:ai_chat' }
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
      
      // 1. 如果没有任何菜单权限，强制跳转到无权限提示页
      if (userMenus.length === 0) {
        next({ name: 'NoPermission' })
        return
      }

      // 2. 校验目标路由权限
      const requiredPerm = to.meta.perm
      if (requiredPerm && !userMenus.includes(requiredPerm)) {
        console.warn(`[Guard] Access denied to ${to.path}. Missing ${requiredPerm}`)
        // 如果访问首页没权限，尝试重定向到第一个有权限的菜单
        if (to.name === 'Overview') {
           // Simple redirect logic or stay on current / just error
           next({ name: 'NoPermission' }) 
        } else {
           next({ name: 'Overview' }) // Fallback to overview or show toast
        }
        return
      }
    } catch (e) {
      console.error("Router guard parse error", e)
    }
  }

  next()
})

export default router
