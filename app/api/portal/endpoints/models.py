from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin, get_current_user, require_permission
from app.core.orm import get_db_session
from app.models.ai_model import AIModel
from app.schemas.ai_model import AIModelCreate, AIModelUpdate, AIModelResponse
import uuid

router = APIRouter()

@router.get("", response_model=List[AIModelResponse])
async def list_models(
    type: str = None,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(get_current_user)
):
    """List all AI models"""
    query = select(AIModel).where(AIModel.is_active == True)
    if type:
        query = query.where(AIModel.type == type)
    
    result = await db.execute(query)
    return [AIModelResponse.from_orm_custom(m) for m in result.scalars().all()]

@router.post("", response_model=AIModelResponse)
async def create_model(
    model_in: AIModelCreate,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Create a new AI model"""
    # ... (rest of the function)
    new_model = AIModel(
        id=str(uuid.uuid4()),
        **model_in.dict()
    )
    db.add(new_model)
    await db.commit()
    await db.refresh(new_model)
    return AIModelResponse.from_orm_custom(new_model)

@router.put("/{model_id}", response_model=AIModelResponse)
async def update_model(
    model_id: str,
    model_in: AIModelUpdate,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Update an AI model"""
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalars().first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    update_data = model_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(model, field, value)
        
    await db.commit()
    await db.refresh(model)
    return AIModelResponse.from_orm_custom(model)

@router.delete("/{model_id}")
async def delete_model(
    model_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Soft delete an AI model"""
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalars().first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    model.is_active = False # Soft delete
    await db.commit()
    return {"status": "success", "message": "Model deleted"}

@router.post("/{model_id}/test")
async def test_model(
    model_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Test model connectivity and credentials"""
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model_obj = result.scalars().first()
    if not model_obj:
        raise HTTPException(status_code=404, detail="Model not found")

    from app.core.llm.client import get_llm_async
    from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
    from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
    try:
        # Create LLM instance with these specific credentials
        llm = await get_llm_async(
            model=model_obj.model_id,
            api_key=model_obj.api_key,
            base_url=model_obj.api_base_url,
            temperature=0,
            max_retries=1
        )
        
        if not llm:
            return {"status": "error", "message": "无法创建 LLM 实例，请检查配置。"}

        # Simple ping-style check
        import asyncio
        chat_client = chat_client_from_handle(llm)
        response = await asyncio.wait_for(
            chat_client.generate_text(
                [
                    RuntimeMessage(
                        role="user",
                        content=[RuntimeContentBlock(type="text", text="say 'pong'")],
                    )
                ]
            ),
            timeout=15.0,
        )
        
        return {
            "status": "success", 
            "message": "连接成功", 
            "response": response[:100]
        }
    except Exception as e:
        import logging
        logging.error(f"Model test failed: {str(e)}")
        return {"status": "error", "message": f"连接失败: {str(e)}"}
