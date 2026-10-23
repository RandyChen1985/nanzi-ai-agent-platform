import json
import logging
import time

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
    AIModelDiscoverResponse,
    AIModelOption,
    AIModelTestRequest,
    AIModelUpdate,
    AIModelResponse,
    ModelDiagnosticStep,
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
from app.utils.provider_diagnostics import (
    describe_http_failure,
    describe_invalid_response,
    describe_sdk_failure,
    describe_transport_failure,
    redact_secrets,
)
import uuid

router = APIRouter()
logger = logging.getLogger(__name__)


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _log_reason(text: str, limit: int = 160) -> str:
    """Collapse a multi-line diagnostic into one log line without leaking secrets."""

    stripped = (text or "").strip()
    if not stripped:
        return ""
    return redact_secrets(stripped.splitlines()[0])[:limit]


class _StepRecorder:
    """采集一次供应商调用的分步耗时，供前端渲染「测试过程」时间线。

    每个步骤的 ``elapsed_ms`` 是「从上一步到这里」的耗时，因此「请求供应商」
    那条就是真实网络耗时——安装方据此能判断慢在连接、鉴权还是解析。
    """

    def __init__(self) -> None:
        self._steps: list[dict] = []
        self._mark = time.perf_counter()

    def add(self, name: str, *, status: str = "ok", detail: str | None = None) -> None:
        now = time.perf_counter()
        self._steps.append(
            {
                "name": name,
                "status": status,
                "detail": _log_reason(detail) if detail else None,
                "elapsed_ms": int((now - self._mark) * 1000),
            }
        )
        self._mark = now

    def snapshot(self) -> list[dict]:
        return list(self._steps)


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
    scope: str = None,
    db: AsyncSession = Depends(get_db_session),
    user: Dict = Depends(get_current_user)
):
    """List all AI models

    scope=chat：聊天输入框专用，按当前用户的模型白名单收窄（未配置白名单 = 不限制）。
    不传 scope 的管理端调用保持全量，因此版本配置候选 / 系统配置页 / 模型管理页不受影响。
    """
    query = select(AIModel)
    if not include_inactive:
        query = query.where(AIModel.is_active == True)
    if type:
        query = query.where(AIModel.type == type)

    if scope == "chat" and str(user.get("role") or "") != "admin":
        from app.services.permission_service import PermissionService

        allowed_model_ids = await PermissionService(db).get_chat_allowed_model_ids(user.get("id"))
        if allowed_model_ids is not None:
            query = query.where(AIModel.model_id.in_(sorted(allowed_model_ids)))

    result = await db.execute(query)
    return [AIModelResponse.from_orm_custom(m) for m in result.scalars().all()]


@router.post("/discover", response_model=AIModelDiscoverResponse)
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

    recorder = _StepRecorder()
    recorder.add(
        "读取配置",
        detail=f"provider={request.provider}，API Key {'已配置' if api_key else '未配置'}",
    )

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

    recorder.add("解析请求地址", detail=request_url)
    logger.info(
        "[model.discover] start provider=%s url=%s key=%s",
        request.provider,
        redact_secrets(request_url),
        "已配置" if api_key else "未配置",
    )
    started_at = time.perf_counter()

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
            recorder.add("请求供应商", detail=f"HTTP {response.status_code}")

            if response.status_code >= 400:
                detail_text = describe_http_failure(
                    method="GET",
                    request_url=request_url,
                    status_code=response.status_code,
                    body=response.text,
                )
                if not api_key and response.status_code in {401, 403}:
                    detail_text = (
                        f"{detail_text}\n注意：本次请求没有携带 API Key——表单留空，"
                        "且未能从已保存的配置中解出密钥（更换过 SECRET_KEY 或迁移环境后旧密钥会失效，需要重新填写）。"
                    )
                recorder.add(
                    "供应商响应",
                    status="failed",
                    detail=_log_reason(detail_text),
                )
                logger.warning(
                    "[model.discover] failed provider=%s url=%s status=%s elapsed_ms=%s reason=%s",
                    request.provider,
                    redact_secrets(request_url),
                    response.status_code,
                    _elapsed_ms(started_at),
                    _log_reason(detail_text),
                )
                raise HTTPException(
                    status_code=502,
                    detail={"message": detail_text, "steps": recorder.snapshot()},
                )

            try:
                payload = response.json()
            except ValueError as exc:
                invalid_detail = describe_invalid_response(
                    method="GET",
                    request_url=request_url,
                    body=response.text,
                    content_type=response.headers.get("content-type"),
                )
                recorder.add(
                    "解析返回内容",
                    status="failed",
                    detail="返回内容不是合法 JSON",
                )
                logger.error(
                    "[model.discover] failed provider=%s url=%s status=%s elapsed_ms=%s reason=%s",
                    request.provider,
                    redact_secrets(request_url),
                    response.status_code,
                    _elapsed_ms(started_at),
                    _log_reason(invalid_detail),
                )
                raise HTTPException(
                    status_code=502,
                    detail={"message": invalid_detail, "steps": recorder.snapshot()},
                ) from exc
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        transport_detail = describe_transport_failure(
            method="GET",
            request_url=request_url,
            exc=exc,
        )
        recorder.add(
            "请求供应商",
            status="failed",
            detail=f"{type(exc).__name__}: {exc}".rstrip(": "),
        )
        logger.error(
            "[model.discover] failed provider=%s url=%s elapsed_ms=%s reason=%s",
            request.provider,
            redact_secrets(request_url),
            _elapsed_ms(started_at),
            _log_reason(f"{type(exc).__name__}: {exc}"),
        )
        raise HTTPException(
            status_code=502,
            detail={"message": transport_detail, "steps": recorder.snapshot()},
        ) from exc

    if request.provider == "dashscope" and isinstance(payload, dict):
        raw_models = payload.get("output", {}).get("models", [])
    else:
        raw_models = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        recorder.add("解析返回内容", status="failed", detail="模型列表格式无法识别")
        raise HTTPException(
            status_code=502,
            detail={
                "message": "供应商返回的模型列表格式无法识别",
                "steps": recorder.snapshot(),
            },
        )

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

    recorder.add("解析返回内容", detail=f"{len(options)} 个模型")
    logger.info(
        "[model.discover] ok provider=%s url=%s models=%s elapsed_ms=%s",
        request.provider,
        redact_secrets(request_url),
        len(options),
        _elapsed_ms(started_at),
    )
    return {"models": options, "steps": recorder.snapshot()}

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
        temperature=model_obj.temperature,
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
    temperature: float | None = None,
):
    recorder = _StepRecorder()
    recorder.add(
        "读取配置",
        detail=(
            f"provider={provider or '默认'}，model={model_id}，"
            f"API Key {'已配置' if api_key else '未配置'}"
        ),
    )
    logger.info(
        "[model.test] start model=%s type=%s provider=%s base_url=%s key=%s",
        model_id,
        model_type,
        provider,
        redact_secrets((api_base_url or "").strip()) or "（默认）",
        "已配置" if api_key else "未配置",
    )
    started_at = time.perf_counter()
    try:
        if model_type == "embedding":
            recorder.add(
                "准备 Embedding 请求",
                detail=redact_secrets((api_base_url or "").strip()) or "使用供应商默认地址",
            )
            result = await _test_embedding_connection(
                provider=provider,
                model_id=model_id,
                api_key=api_key,
                api_base_url=api_base_url,
            )
            recorder.add("收到响应", detail=str(result.get("response") or ""))
            logger.info(
                "[model.test] ok model=%s type=embedding elapsed_ms=%s",
                model_id,
                _elapsed_ms(started_at),
            )
            return {**result, "steps": recorder.snapshot()}

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
            temperature=temperature,
            source="model.test",
        )
        recorder.add(
            "创建 LLM 客户端",
            detail=redact_secrets((api_base_url or "").strip()) or "使用供应商默认地址",
        )

        if not llm:
            recorder.add("创建 LLM 客户端", status="failed", detail="无法创建 LLM 实例")
            logger.error("[model.test] failed model=%s reason=无法创建 LLM 实例", model_id)
            return {
                "status": "error",
                "message": "无法创建 LLM 实例，请检查配置。",
                "steps": recorder.snapshot(),
            }

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
        recorder.add("收到响应", detail=_log_reason(response, 60))

        logger.info(
            "[model.test] ok model=%s type=%s elapsed_ms=%s",
            model_id,
            model_type,
            _elapsed_ms(started_at),
        )
        return {
            "status": "success", 
            "message": "连接成功", 
            "response": response[:100],
            "steps": recorder.snapshot(),
        }
    except ValueError as e:
        # 配置类错误（Base URL 缺失、供应商返回格式不符）本身已是可读中文，
        # 直接透出比套一层「调用失败」更有用。
        recorder.add("执行请求", status="failed", detail=str(e))
        logger.warning(
            "[model.test] failed model=%s elapsed_ms=%s reason=%s",
            model_id,
            _elapsed_ms(started_at),
            _log_reason(str(e)),
        )
        return {"status": "error", "message": str(e), "steps": recorder.snapshot()}
    except Exception as e:
        recorder.add(f"{type(e).__name__}", status="failed", detail=str(e) or "异常消息为空")
        logger.error(
            "[model.test] failed model=%s type=%s elapsed_ms=%s reason=%s",
            model_id,
            type(e).__name__,
            _elapsed_ms(started_at),
            _log_reason(str(e)) or "（异常消息为空）",
        )
        return {
            "status": "error",
            "message": describe_sdk_failure(e),
            "steps": recorder.snapshot(),
        }


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
        temperature=request.temperature,
    )
