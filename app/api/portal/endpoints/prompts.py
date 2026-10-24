from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional, Dict, Any

from app.core.dependencies import require_api_key, get_current_user, require_permission
from app.schemas.prompt import (
    PromptMetadata, PromptDetail, PromptSource, 
    PromptTestRequest, PromptTestResponse, PromptSaveRequest,
    PromptOptimizeResponse
)
from app.services.ai.prompt_ops.prompt_service import PromptService

router = APIRouter()

@router.get("/", response_model=List[PromptMetadata])
async def list_prompts(user: Dict[str, Any] = Depends(get_current_user)):
    """获取提示词元数据列表 (系统级 + 当前用户可见的智能体级)"""
    return await PromptService.get_all_prompts(user)

@router.get("/detail", response_model=PromptDetail)
async def get_prompt_detail(
    source: PromptSource, 
    target_id: str, 
    version: Optional[int] = None,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """获取特定提示词的内容详情（智能体提示词按可见性校验）"""
    try:
        return await PromptService.get_prompt_detail(source, target_id, version, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

@router.post("/test", response_model=PromptTestResponse)
async def test_prompt(
    data: PromptTestRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """在 Playground 中测试提示词 (变量注入 + LLM 推理)"""
    try:
        return await PromptService.test_prompt(data.content, data.variables, data.user_input, data.model)
    except Exception as e:
        import logging
        logging.error(f"Prompt test failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Test failed: {str(e)}")

@router.post("/save", dependencies=[Depends(require_permission("element", "element:prompts:edit"))])
async def save_prompt(
    data: PromptSaveRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """保存提示词变更 (具有编辑权限)"""
    try:
        changed = await PromptService.save_prompt(
            data.source,
            data.target_id,
            data.content,
            data.version_note,
            user_name=user.get("user_name", "unknown"),
            # 归属校验靠它：不是自己的智能体（且非管理员）在这里被拒
            user=user,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not changed["changed"]:
        return {"status": "unchanged", "message": "No changes detected", "version_id": None}

    # version_id 让前端发布刚保存的这一版，而不是"列表里第一个草稿"
    return {"status": "success", "version_id": changed["version_id"]}

@router.post("/optimize", response_model=PromptOptimizeResponse, dependencies=[Depends(require_permission("element", "element:prompts:optimize"))])
async def optimize_prompt(
    data: Dict[str, str],
    user: Dict[str, Any] = Depends(get_current_user)
):
    """请求 AI 对提示词进行 8 个维度的优化建议 (具有优化权限)"""
    content = data.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="Missing content")
    
    try:
        return await PromptService.optimize_prompt(content)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"AI 润色结果解析失败，请重试：{e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize/agent-editor", response_model=PromptOptimizeResponse)
async def optimize_agent_editor_prompt(
    data: Dict[str, str],
    user: Dict[str, Any] = Depends(get_current_user),
):
    """为已登录的智能体版本编辑器提供润色；不要求提示词工坊专属权限。"""
    content = data.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="Missing content")

    try:
        return await PromptService.optimize_prompt(content)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"AI 润色结果解析失败，请重试：{e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize/task-instruction", response_model=PromptOptimizeResponse)
async def optimize_task_instruction(
    data: Dict[str, str],
    user: Dict[str, Any] = Depends(get_current_user),
):
    """为任务中心的执行指令提供 AI 润色；仅要求登录，不要求提示词工坊权限。"""
    content = data.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="Missing content")

    try:
        return await PromptService.optimize_task_instruction(content)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"AI 润色结果解析失败，请重试：{e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/history")
async def get_prompt_history(
    source: PromptSource,
    target_id: str,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """获取提示词的变更历史 (System 查 Audit Log, Agent 查 Versions)"""
    if source == PromptSource.SYSTEM_CONFIG:
        # 复用系统配置审计逻辑
        from app.services.config_service import ConfigService
        return await ConfigService.get_config_history(target_id)
    else:
        # Agent 级 Prompts: 将版本记录转换为审计日志格式返回
        return await PromptService.get_agent_prompt_history(target_id)
