from fastapi import APIRouter, Depends
from app.core.dependencies import require_api_key, verify_v1_api_access
from app.api.v1.endpoints import chat, schema, users, tasks, chatbi, fs

# API Key + `verify_v1_api_access`（`chatbi.public_router` 单独挂在下方，不经此依赖）
v1_secured = APIRouter(dependencies=[Depends(require_api_key), Depends(verify_v1_api_access)])

v1_secured.include_router(chat.router, prefix="/chat", tags=["V1 智能体对话"])
v1_secured.include_router(users.router, prefix="/users", tags=["V1 用户服务"])
v1_secured.include_router(schema.router, tags=["V1 Schema服务"])  # Rename tag for clarity
v1_secured.include_router(tasks.router, prefix="/tasks", tags=["V1 定时任务"])
v1_secured.include_router(chatbi.router, prefix="/chatbi", tags=["V1 ChatBI"])
v1_secured.include_router(fs.router, prefix="/chat/fs", tags=["V1 文件系统浏览器"])

v1_router = APIRouter()
v1_router.include_router(v1_secured)
v1_router.include_router(chatbi.public_router, prefix="/chatbi", tags=["V1 ChatBI"])

