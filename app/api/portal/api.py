from fastapi import APIRouter, Depends
from app.core.dependencies import require_admin, require_api_key
from app.api.portal.endpoints import auth, audit, management, keys, dashboard, system, chat, metadata, agents, prompts, slash_commands, health, models, tools, ragflow, roles, mcp, changelog, chat_feedback, chatbi_examples, skills, memory, saved_reports, portal_prefs, quota, notifications, inbox, data_portal

portal_router = APIRouter()

# 1. 认证 (Auth)
portal_router.include_router(auth.router, prefix="/auth", tags=["认证"])

# 2. 仪表盘 (Dashboard)
portal_router.include_router(dashboard.router, prefix="/dashboard", tags=["仪表盘"], dependencies=[Depends(require_api_key)])

# 3. 智能体管理 (Agents)
portal_router.include_router(agents.router, prefix="/agents", tags=["智能体管理"], dependencies=[Depends(require_api_key)])

# 4. 智能体对话 (Chat)
portal_router.include_router(chat.router, prefix="/chat", tags=["智能体对话"], dependencies=[Depends(require_api_key)])
portal_router.include_router(chat_feedback.router, prefix="/chat", tags=["反馈收集"], dependencies=[Depends(require_api_key)])

# 4.1 ChatBI 经验库 (Examples)
portal_router.include_router(chatbi_examples.router, prefix="/chatbi-examples", tags=["ChatBI经验库"], dependencies=[Depends(require_api_key)])

# 5. 审计日志 (Audit)
portal_router.include_router(audit.router, prefix="/audit", tags=["审计日志"], dependencies=[Depends(require_api_key)])

# 6. 元数据管理 (Metadata)
portal_router.include_router(metadata.router, prefix="/metadata", tags=["元数据管理"], dependencies=[Depends(require_api_key)])

# 7. 系统配置 (System)
portal_router.include_router(system.router, prefix="/system", tags=["系统配置"], dependencies=[Depends(require_api_key)])

# 8. API 密钥 (Keys)
portal_router.include_router(keys.router, prefix="/keys", tags=["API密钥"], dependencies=[Depends(require_api_key)])

# 8.1 用户管理 (Management)
portal_router.include_router(management.router, prefix="/management", tags=["用户管理"], dependencies=[Depends(require_api_key)])

# 8.2 角色管理 (Roles)
portal_router.include_router(roles.router, prefix="/roles", tags=["角色管理"], dependencies=[Depends(require_api_key)])

# 9. 提示词工程 (需要登录)
portal_router.include_router(prompts.router, prefix="/prompts", tags=["提示词管理"], dependencies=[Depends(require_api_key)])

# 10. 快捷指令 (需要登录)
portal_router.include_router(slash_commands.router, prefix="/slash-commands", tags=["快捷指令"], dependencies=[Depends(require_api_key)])

# 11. 健康检查 (需要登录)
portal_router.include_router(health.router, prefix="/health", tags=["健康检查"], dependencies=[Depends(require_api_key)])

# 12. 模型管理 (Model Management)
portal_router.include_router(models.router, prefix="/models", tags=["模型管理"], dependencies=[Depends(require_api_key)])

# 13. 工具管理 (Tool Management)
portal_router.include_router(tools.router, prefix="/tools", tags=["工具管理"], dependencies=[Depends(require_api_key)])

# 14. RAGFlow 代理 (RAGFlow Proxy)
portal_router.include_router(ragflow.router, prefix="/ragflow", tags=["RAGFlow代理"], dependencies=[Depends(require_api_key)])

# 15. MCP 管理 (MCP Management)
portal_router.include_router(mcp.router, prefix="/mcp", tags=["MCP管理"], dependencies=[Depends(require_api_key)])

# 16. 变更日志 (Changelog)
portal_router.include_router(changelog.router, prefix="/changelog", tags=["变更日志"], dependencies=[Depends(require_api_key)])

# 17. 智能体技能管理 (Skills Management)
portal_router.include_router(skills.router, prefix="/skills", tags=["技能管理"], dependencies=[Depends(require_api_key)])

# 18. 记忆管理中心 (Memory Management)
portal_router.include_router(memory.router, prefix="/memory", tags=["记忆管理"], dependencies=[Depends(require_api_key)])

# 19. 黄金 SQL 暂存报表 (Saved Reports)
portal_router.include_router(saved_reports.router, prefix="/saved-reports", tags=["暂存报表"], dependencies=[Depends(require_api_key)])
portal_router.include_router(data_portal.router, prefix="/data-portal", tags=["数据门户"], dependencies=[Depends(require_api_key)])

# 20. 数据门户个人偏好 (Portal Preferences)
portal_router.include_router(portal_prefs.router, prefix="/portal-prefs", tags=["门户偏好"], dependencies=[Depends(require_api_key)])

# 21. Token 额度 (Quota)
portal_router.include_router(quota.router, prefix="/quota", tags=["Token额度"], dependencies=[Depends(require_api_key)])

# 22. 个人中心消息通知 (Notifications)
portal_router.include_router(notifications.router, prefix="/notifications", tags=["消息通知"], dependencies=[Depends(require_api_key)])

# 23. 站内消息中心
portal_router.include_router(inbox.router, prefix="/inbox", tags=["站内消息"], dependencies=[Depends(require_api_key)])
