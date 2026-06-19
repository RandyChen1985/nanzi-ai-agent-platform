import logging
import re
from typing import Optional, Dict
from app.schemas.agent import ChatConfig
from app.core.llm.client import get_llm
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

class AgentConfigProvider:
    """
    Handles LLM instantiation and environment configuration for Agents.
    """
    
    @staticmethod
    async def get_configured_llm(
        streaming: bool = True, 
        config: Optional[ChatConfig] = None,
        model_override: Optional[str] = None,
        temp_override: Optional[float] = None
    ):
        """
        Instantiates an AgentScope LLM based on system config, agent-specific overrides, or runtime overrides.
        
        Priority:
        1. Runtime Override (model_override/temp_override from Tool Runtime Config)
        2. Debug Options (User session debug)
        3. Agent Config (ChatConfig)
        4. System Defaults
        """
        # Fetch dynamic config from DB
        llm_config = await ConfigService.get_all_from_db()
        
        def get_val(key, default):
            return llm_config.get(key, {}).get("value") or default

        # Check Debug Context Overrides
        from app.core.context import get_debug_option
        
        # 1. Model Name Priority
        debug_model = get_debug_option("model")
        
        if model_override:
            model = model_override
        elif debug_model:
            model = debug_model
        elif config and config.model_name:
            model = config.model_name
        else:
            model = get_val("llm_model_name", "deepseek-chat")

        # 2. Temperature Priority
        debug_temp = get_debug_option("temperature")
        
        if temp_override is not None:
            temperature = float(temp_override)
        elif debug_temp is not None:
             temperature = float(debug_temp)
        elif config and config.temperature is not None:
             temperature = float(config.temperature)
        else:
             temp_str = get_val("llm_temperature", None)
             temperature = float(temp_str) if temp_str is not None else 0.0
             
        api_key = get_val("llm_api_key", None)
        base_url = get_val("llm_base_url", None)

        # 3. Model Management Registry Lookup
        # If the selected 'model' string corresponds to an entry in ai_models table,
        # use its specific credentials if available.
        # This allows per-model API keys/BaseURLs.
        try:
            from app.core.orm import AsyncSessionLocal
            from app.models.ai_model import AIModel
            from sqlalchemy import select, or_
            
            async with AsyncSessionLocal() as session:
                # Search for active model matching name or model_id
                stmt = select(AIModel).where(
                    AIModel.is_active == True,
                    or_(AIModel.model_id == model, AIModel.name == model)
                )
                result = await session.execute(stmt)
                ai_model = result.scalars().first()
                
                if ai_model:
                    # Found a registered model, verify if we should override credentials
                    if ai_model.api_key:
                        api_key = ai_model.api_key
                    if ai_model.api_base_url:
                        base_url = ai_model.api_base_url
                    
                    # Update the model string to the actual model_id required by the provider
                    # (e.g. user selected "My GPT4", acts as "gpt-4o")
                    model = ai_model.model_id
                    
        except Exception as e:
            logger.warning(f"Failed to lookup model registry: {e}")

        return get_llm(
            streaming=streaming,
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=temperature
        )

    @staticmethod
    async def get_synthesis_llm(
        streaming: bool = True, 
        config: Optional[ChatConfig] = None
    ):
        """
        Instantiates the LLM specifically for the Synthesis (final response) phase.
        
        Fallback Logic:
        1. Synthesis-specific config (synthesis_model_name)
        2. Primary Agent config (model_name)
        3. System Defaults
        """
        if config and config.synthesis_model_name:
            # Use synthesis-specific overrides
            return await AgentConfigProvider.get_configured_llm(
                streaming=streaming,
                config=config,
                model_override=config.synthesis_model_name,
                temp_override=config.synthesis_temperature
            )
        
        # Default fallback to primary model
        return await AgentConfigProvider.get_configured_llm(
            streaming=streaming,
            config=config
        )

    @staticmethod
    async def get_fallback_llm(
        streaming: bool = True,
        config: Optional[ChatConfig] = None,
        exclude_model: Optional[str] = None,
    ):
        """Fallback native model for AgentScope ModelConfig: system default only."""
        try:
            llm_config = await ConfigService.get_all_from_db()
        except Exception:
            llm_config = {}

        def get_val(key, default=None):
            return llm_config.get(key, {}).get("value") or default

        candidate = get_val("llm_model_name")
        if not candidate or (exclude_model and candidate == exclude_model):
            return None
        try:
            return await AgentConfigProvider.get_configured_llm(
                streaming=streaming,
                config=config,
                model_override=candidate,
            )
        except Exception:
            return None

    @staticmethod
    async def _generate_dataset_menu_content(user_id: Optional[int] = None, is_admin: bool = False) -> str:
        """
        Internal method to generate the dataset menu string from DB, filtered by permissions and status.
        """
        menu = "Available Datasets (Look for Table terms to find relevant data):\n"
        try:
            from app.core.orm import AsyncSessionLocal
            from app.models.metadata import MetaDataset
            from app.services.metadata_service import MetadataService
            from sqlalchemy.orm import selectinload

            async with AsyncSessionLocal() as session:
                # 使用 MetadataService.search_datasets 进行权限和状态过滤 (status=1 为启用)
                datasets = await MetadataService.search_datasets(
                    session,
                    query=None,
                    user_id=user_id,
                    is_admin=is_admin,
                    status=1 # 仅限启用状态
                )
            
            if not datasets:
                return menu + "  (No authorized datasets available)"
            else:
                for ds in datasets:
                    name = getattr(ds, "name", "unknown")
                    display_name = str(getattr(ds, "display_name", "") or "").strip()
                    desc = getattr(ds, "description", "No description")
                    tags = getattr(ds, "tags", [])

                    tag_str = f" [{', '.join(tags)}]" if isinstance(tags, list) and tags else ""
                    menu += f"- Dataset: {name}{tag_str}\n"
                    if display_name and display_name != name:
                        menu += f"  Display Name: {display_name}\n"
                    menu += f"  Description: {desc}\n"

                    active_tables = [
                        tbl for tbl in (getattr(ds, "tables", None) or [])
                        if getattr(tbl, "status", 1) != 0
                    ]
                    table_terms: list[str] = []
                    for tbl in active_tables:
                        term = str(getattr(tbl, "term", None) or getattr(tbl, "physical_name", "") or "").strip()
                        if term:
                            table_terms.append(term)
                    if table_terms:
                        menu += f"  Includes Tables: {', '.join(table_terms)}\n"
                        menu += "  Table Details:\n"
                        for tbl in active_tables:
                            term = str(getattr(tbl, "term", None) or getattr(tbl, "physical_name", "") or "").strip()
                            if not term:
                                continue
                            table_desc = re.sub(
                                r"\s+",
                                " ",
                                str(getattr(tbl, "description", "") or "").strip(),
                            )
                            if table_desc:
                                menu += f"    - {term}: {table_desc}\n"
                            else:
                                menu += f"    - {term}\n"

                    metrics = [
                        m for m in (getattr(ds, "metrics", None) or [])
                        if getattr(m, "display_name", None) or getattr(m, "name", None)
                    ]
                    if metrics:
                        metric_labels = [
                            str(getattr(m, "display_name", None) or getattr(m, "name", "")).strip()
                            for m in metrics
                        ]
                        metric_labels = [label for label in metric_labels if label]
                        if metric_labels:
                            menu += f"  Metrics: {', '.join(metric_labels)}\n"

                    menu += "\n"
                return menu
        except Exception as e:
            logger.error(f"Failed to load dataset menu internally: {e}")
            return menu + f"  (System Error: Failed to load dataset menu)"

    @staticmethod
    async def get_dataset_menu(user_id: Optional[int] = None, is_admin: bool = False) -> str:
        """
        Fetches authorized datasets to assist LLM reasoning. Cached via Redis per user.
        """
        from app.core.redis import get_redis
        redis = await get_redis()
        
        # 1. Try Cache (按用户隔离，admin 共享一个 key)
        cache_key = f"agent:dataset_menu:{'admin' if is_admin else user_id or 'anon'}"
        try:
            if redis:
                cached_menu = await redis.get(cache_key)
                if cached_menu:
                    return cached_menu
        except Exception as e:
            logger.warning(f"Redis error for dataset menu: {e}")

        # 2. Cache Miss: Fetch from DB
        content = await AgentConfigProvider._generate_dataset_menu_content(user_id, is_admin)

        # 3. Save to Cache (TTL: 7 days)
        try:
            if redis:
                await redis.set(cache_key, content, ex=604800)
        except Exception as e:
            logger.warning(f"Redis set error: {e}")
        
        return content

    @staticmethod
    async def refresh_dataset_menu():
        """
        Force regenerate the dataset menu and update Redis cache.
        Should be called when datasets or tables are modified.
        """
        from app.core.redis import get_redis
        from app.services.dataset_navigation_service import DatasetNavigationService

        try:
            redis = await get_redis()
            if redis:
                async for key in redis.scan_iter(match="agent:dataset_menu:*", count=200):
                    await redis.delete(key)
                await redis.delete("agent:dataset_menu")
            await DatasetNavigationService.bump_navigation_cache_generation()
            logger.info("Dataset menu and navigation caches invalidated.")
            # 异步启动后台预热任务，温和预热最近活跃用户的门户缓存
            import asyncio
            asyncio.create_task(DatasetNavigationService.warm_up_navigation_caches_background())
        except Exception as e:
            logger.error(f"Failed to refresh dataset menu cache: {e}")
