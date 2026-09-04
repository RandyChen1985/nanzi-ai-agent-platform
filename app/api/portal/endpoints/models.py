import json
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict
from urllib.parse import urlparse, urlsplit, urlunsplit
import httpx
from sqlalchemy import Boolean, Column, MetaData, String, Table, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.dependencies import require_admin, get_current_user, require_permission
from app.core.orm import get_db_session
from app.models.ai_model import AIModel
from app.models.agent import AIAgent, AIAgentVersion
from app.schemas.ai_model import (
    AIModelCreate,
    AIModelDiscoverRequest,
    AIModelOption,
    AIModelTestRequest,
    AIModelUpdate,
    AIModelResponse,
    normalize_supported_reasoning_efforts,
    normalize_legacy_reasoning_effort,
    normalize_legacy_supported_reasoning_efforts,
    validate_reasoning_configuration,
)
from app.utils.model_credentials import encrypt_model_api_key, decrypt_model_api_key
from app.utils.model_providers import (
    azure_openai_request_config,
    default_model_api_base_url,
    resolve_model_api_base_url,
)
import uuid

router = APIRouter()


def _serialize_reasoning_efforts(value: list[str]) -> str:
    return json.dumps(
        normalize_supported_reasoning_efforts(value),
        ensure_ascii=False,
        separators=(",", ":"),
    )

_SYSTEM_CONFIGS_TABLE = Table(
    "system_configs",
    MetaData(),
    Column("key", String(255)),
    Column("value", Text),
    Column("is_secret", Boolean),
)


async def _ensure_model_id_available(
    db: AsyncSession,
    model_id: str,
    *,
    exclude_id: str | None = None,
) -> None:
    query = select(AIModel.id).where(AIModel.model_id == model_id)
    if exclude_id:
        query = query.where(AIModel.id != exclude_id)
    result = await db.execute(query)
    if result.first() is not None:
        raise HTTPException(status_code=409, detail=f"model_id 已存在：{model_id}")

@router.get("", response_model=List[AIModelResponse])
async def list_models(
    type: str = None,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(get_current_user)
):
    """List all AI models"""
    query = select(AIModel)
    if not include_inactive:
        query = query.where(AIModel.is_active == True)
    if type:
        query = query.where(AIModel.type == type)
    
    result = await db.execute(query)
    return [AIModelResponse.from_orm_custom(m) for m in result.scalars().all()]


@router.post("/discover", response_model=List[AIModelOption])
async def discover_models(
    request: AIModelDiscoverRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """Discover model IDs through a provider's OpenAI-compatible /models API."""
    if request.provider == "azure":
        raise HTTPException(
            status_code=400,
            detail="Azure OpenAI 不支持通用模型列表发现，请手工填写部署名称（model_id）。",
        )

    api_key = (request.api_key or "").strip()
    if not api_key and request.model_config_id:
        existing = await db.get(AIModel, request.model_config_id)
        if existing:
            api_key = decrypt_model_api_key(existing.api_key) or ""

    base_url = resolve_model_api_base_url(request.provider, request.api_base_url)
    if not base_url:
        raise HTTPException(status_code=400, detail="请填写 API Base URL")

    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="API Base URL 必须是有效的 HTTP/HTTPS 地址")

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request_url = f"{base_url.rstrip('/')}/models"
    request_params = None
    if request.provider == "dashscope":
        # DashScope's OpenAI-compatible chat endpoint does not expose the
        # standard /v1/models route. Its official model catalog is served by
        # the deployments API on the same regional host.
        request_url = f"{parsed.scheme}://{parsed.netloc}/api/v1/deployments/models"
        request_params = {"page_no": 1, "page_size": 100, "version": "v1.0", "model_source": "base"}
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            if request_params:
                response = await client.get(request_url, headers=headers, params=request_params)
            else:
                response = await client.get(request_url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            detail = "供应商鉴权失败，请检查 API Key。"
        else:
            detail = f"供应商模型列表请求失败（HTTP {status_code}）。"
        raise HTTPException(status_code=502, detail=detail) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"无法获取供应商模型列表：{exc}") from exc

    if request.provider == "dashscope" and isinstance(payload, dict):
        raw_models = payload.get("output", {}).get("models", [])
    else:
        raw_models = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        raise HTTPException(status_code=502, detail="供应商返回的模型列表格式无法识别")

    options: list[AIModelOption] = []
    for item in raw_models:
        if isinstance(item, str):
            model_id = item.strip()
            model_name = model_id
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("model") or item.get("model_name") or "").strip()
            model_name = str(item.get("name") or model_id).strip()
        else:
            continue
        if model_id:
            options.append(AIModelOption(model_id=model_id, name=model_name or model_id))

    return options

@router.post("", response_model=AIModelResponse)
async def create_model(
    model_in: AIModelCreate,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Create a new AI model"""
    await _ensure_model_id_available(db, model_in.model_id)
    data = model_in.model_dump()
    data["api_base_url"] = resolve_model_api_base_url(
        data.get("provider"), data.get("api_base_url")
    )
    api_key = data.pop("api_key", None)
    if api_key is not None:
        data["api_key"] = encrypt_model_api_key(api_key)
    data["supported_reasoning_efforts"] = _serialize_reasoning_efforts(
        data["supported_reasoning_efforts"]
    )

    new_model = AIModel(
        id=str(uuid.uuid4()),
        **data,
    )
    db.add(new_model)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "model_id" in str(exc).lower() or "uq_ai_models_model_id" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"model_id 已存在：{model_in.model_id}") from exc
        raise
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
    
    update_data = model_in.model_dump(exclude_unset=True)
    if "supported_reasoning_efforts" in update_data and update_data["supported_reasoning_efforts"] is None:
        raise HTTPException(status_code=422, detail="supported_reasoning_efforts cannot be null")
    if "reasoning_effort" in update_data or "supported_reasoning_efforts" in update_data:
        current_supported = normalize_legacy_supported_reasoning_efforts(
            model.supported_reasoning_efforts
        )
        effective_reasoning_effort = normalize_legacy_reasoning_effort(update_data.get(
            "reasoning_effort",
            model.reasoning_effort,
        ))
        effective_supported = update_data.get(
            "supported_reasoning_efforts",
            current_supported,
        )
        normalized_supported = validate_reasoning_configuration(
            effective_reasoning_effort,
            effective_supported,
        )
        if "supported_reasoning_efforts" in update_data:
            update_data["supported_reasoning_efforts"] = _serialize_reasoning_efforts(
                normalized_supported
            )
    if "model_id" in update_data:
        await _ensure_model_id_available(db, update_data["model_id"], exclude_id=model_id)
    if "provider" in update_data:
        # Preserve an existing custom gateway. If the current URL is a known
        # provider preset, switch it along with the provider.
        if "api_base_url" in update_data:
            update_data["api_base_url"] = resolve_model_api_base_url(
                update_data["provider"], update_data["api_base_url"]
            )
        else:
            current_url = (model.api_base_url or "").strip()
            current_default = default_model_api_base_url(model.provider)
            if not current_url or current_url == current_default:
                update_data["api_base_url"] = default_model_api_base_url(
                    update_data["provider"]
                )
    elif "api_base_url" in update_data:
        update_data["api_base_url"] = resolve_model_api_base_url(
            model.provider, update_data["api_base_url"]
        )
    if "api_key" in update_data:
        update_data["api_key"] = encrypt_model_api_key(update_data["api_key"])

    for field, value in update_data.items():
        setattr(model, field, value)
        
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "model_id" in str(exc).lower() or "uq_ai_models_model_id" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"model_id 已存在：{model_in.model_id}") from exc
        raise
    await db.refresh(model)
    return AIModelResponse.from_orm_custom(model)


def _build_agent_model_reference(version: AIAgentVersion, agent: AIAgent, slots: list[str]) -> dict:
    """Build a stable, navigable model-usage record for an agent version."""

    return {
        "kind": "agent_version",
        "key": ",".join(slots),
        "label": f"智能体「{agent.display_name or agent.name}」v{version.version_number}",
        "detail": "、".join(slots),
        "agent_id": str(agent.id),
        "agent_name": str(agent.name),
        "version_id": str(version.id),
        "version_number": version.version_number,
        "version_status": str(version.status or "DRAFT"),
        "agent_enabled": bool(agent.is_enabled),
    }


def _should_include_agent_model_reference(version: AIAgentVersion) -> bool:
    """Keep draft and published references; archived versions are historical only."""

    return str(version.status or "DRAFT").upper() != "ARCHIVED"


async def _collect_model_references(db: AsyncSession, model: AIModel) -> list[dict]:
    """Return runtime configuration rows that still point at this model."""

    identifiers = {str(model.model_id).strip()}
    if model.name:
        identifiers.add(str(model.name).strip())
    references: list[dict[str, str]] = []

    config_result = await db.execute(
        select(_SYSTEM_CONFIGS_TABLE.c.key, _SYSTEM_CONFIGS_TABLE.c.value).where(
            _SYSTEM_CONFIGS_TABLE.c.key.in_(["llm_model_name", "embed_model_name", "multimodal_model_name"]),
            _SYSTEM_CONFIGS_TABLE.c.value.in_(identifiers),
        )
    )
    config_labels = {
        "llm_model_name": "系统默认 LLM 模型",
        "embed_model_name": "系统默认 Embedding 模型",
        "multimodal_model_name": "系统默认多模态模型",
    }
    for key, value in config_result.all():
        references.append({
            "kind": "system_config",
            "key": str(key),
            "label": config_labels.get(str(key), str(key)),
            "detail": str(value),
            "config_key": str(key),
        })

    version_result = await db.execute(
        select(AIAgentVersion, AIAgent)
        .join(AIAgent, AIAgent.id == AIAgentVersion.agent_id)
        .where(
            (AIAgentVersion.model_name.in_(identifiers))
            | (AIAgentVersion.synthesis_model_name.in_(identifiers))
        )
    )
    for version, agent in version_result.all():
        if not _should_include_agent_model_reference(version):
            continue
        slots = []
        if version.model_name in identifiers:
            slots.append("主模型")
        if version.synthesis_model_name in identifiers:
            slots.append("合成模型")
        references.append(_build_agent_model_reference(version, agent, slots))
    return references


@router.get("/{model_id}/references")
async def model_references(
    model_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """List system and agent configurations affected by disabling this model."""

    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalars().first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    return await _collect_model_references(db, model)


async def _delete_model_record(db: AsyncSession, model: AIModel) -> None:
    """Permanently remove a model registry row."""

    await db.delete(model)
    await db.commit()


@router.delete("/{model_id}")
async def delete_model(
    model_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save"))
):
    """Permanently delete an AI model registry row."""
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalars().first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    await _delete_model_record(db, model)
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

    return await _test_model_connection(
        model_id=model_obj.model_id,
        model_type=model_obj.type,
        provider=model_obj.provider,
        api_key=decrypt_model_api_key(model_obj.api_key),
        api_base_url=model_obj.api_base_url,
        context_size=model_obj.context_size,
        max_output_tokens=model_obj.max_output_tokens,
    )


async def _test_model_connection(
    *,
    model_id: str,
    model_type: str,
    provider: str | None = None,
    api_key: str | None = None,
    api_base_url: str | None = None,
    context_size: int | None = None,
    max_output_tokens: int | None = None,
):
    try:
        if model_type == "embedding":
            return await _test_embedding_connection(
                provider=provider,
                model_id=model_id,
                api_key=api_key,
                api_base_url=api_base_url,
            )

        from app.core.llm.client import get_llm_async
        from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
        from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage

        llm = await get_llm_async(
            model=model_id,
            api_key=api_key,
            base_url=api_base_url,
            provider=provider,
            context_size=context_size,
            max_output_tokens=max_output_tokens,
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


def _embedding_request_url(
    *,
    provider: str | None,
    model_id: str,
    api_base_url: str | None,
) -> tuple[str, dict[str, str]]:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "azure":
        chat_url, api_version = azure_openai_request_config(api_base_url, model_id)
        return f"{chat_url.rstrip('/')}/embeddings", {
            "api-key": "__azure__",
            "x-api-version": api_version,
        }

    base_url = (api_base_url or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("请填写 API Base URL")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("API Base URL 必须是有效的 HTTP/HTTPS 地址")

    from app.utils.model_providers import normalize_embedding_endpoint

    return normalize_embedding_endpoint(base_url), {}


async def _test_embedding_connection(
    *,
    provider: str | None,
    model_id: str,
    api_key: str | None,
    api_base_url: str | None,
):
    request_url, special_headers = _embedding_request_url(
        provider=provider,
        model_id=model_id,
        api_base_url=api_base_url,
    )
    headers = {}
    if special_headers.get("api-key") == "__azure__":
        headers["api-key"] = api_key or ""
        api_version = special_headers["x-api-version"]
        separator = "&" if "?" in request_url else "?"
        request_url = f"{request_url}{separator}api-version={api_version}"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        response = await client.post(
            request_url,
            headers=headers,
            json={"model": model_id, "input": "ping"},
        )
        response.raise_for_status()
        payload = response.json()

    data = payload.get("data") if isinstance(payload, dict) else None
    embedding = data[0].get("embedding") if isinstance(data, list) and data else None
    if not isinstance(embedding, list) or not embedding:
        raise ValueError("供应商未返回有效 embedding")
    return {
        "status": "success",
        "message": "连接成功",
        "response": f"Embedding 维度: {len(embedding)}",
    }


@router.post("/test-config")
async def test_model_config(
    request: AIModelTestRequest,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """Test a model configuration from the add/edit form without saving it."""
    api_key = (request.api_key or "").strip() or None
    if not api_key and request.model_config_id:
        existing = await db.get(AIModel, request.model_config_id)
        if existing:
            api_key = decrypt_model_api_key(existing.api_key) or None

    return await _test_model_connection(
        model_id=request.model_id,
        model_type=request.type,
        provider=request.provider,
        api_key=api_key,
        api_base_url=resolve_model_api_base_url(request.provider, request.api_base_url),
        context_size=request.context_size,
        max_output_tokens=request.max_output_tokens,
    )
