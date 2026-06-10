import ast
import logging
import json
import re
from app.services.ai.tools.tool_compat import tool
from typing import Any, Optional, List, Union
from app.services.ai.ragflow_client import RagFlowClient
from app.services.config_service import ConfigService
from app.services.metadata_rag_service import MetadataRagService

logger = logging.getLogger(__name__)

from app.core.context import get_current_agent_config, get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.permission_service import PermissionService

# 32 位 hex，与 RAGFlow dataset id 常见格式一致
_DATASET_ID_RE = re.compile(r"^[a-fA-F0-9]{32}$")


def normalize_dataset_ids(raw: Union[str, List[Any], None]) -> List[str]:
    """
    将工具参数 / 配置中的 dataset_ids 规范为纯 ID 列表。
    兼容：逗号分隔、JSON 数组、Python 单引号列表、多余引号与方括号。
    """
    if raw is None:
        return []

    items: List[Any]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            parsed: Any = None
            # 优先 Python 单引号列表（模型侧更稳定）；再尝试 JSON 双引号
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
            items = parsed if isinstance(parsed, list) else [text]
        else:
            items = text.split(",")
    else:
        items = [raw]

    result: List[str] = []
    for item in items:
        token = str(item).strip().strip("[]\"' \t")
        if not token:
            continue
        if _DATASET_ID_RE.match(token):
            result.append(token)
            continue
        # 从混杂字符串中提取合法 ID（如 LLM 传入带括号的整段）
        for match in _DATASET_ID_RE.findall(token):
            if match not in result:
                result.append(match)
    return result


@tool
async def search_knowledge_base(query: str, dataset_ids: Optional[str] = None) -> str:
    """
    Search for documents, manuals, and regulations in the Knowledge Base (RAGFlow).

    Args:
        query: The search keywords or question.
        dataset_ids: (Optional) One or more RAGFlow dataset IDs (32-char hex each).
            Preferred formats:
            - Single plain ID: 4525d66cec7111f0a3d00242ac120006
            - Multiple IDs (MUST use single-quoted Python list):
              ['id1','id2']  — correct
              Do NOT use double-quoted JSON like ["id1","id2"].
            Comma-separated without brackets also works: id1,id2
            If omitted, uses the agent's configured datasets or system default.
    """
    client = RagFlowClient()

    logger.info(f"[KnowledgeTool] Called with query='{query}', explicit_ids='{dataset_ids}'")

    # 1. Resolve Dataset IDs
    target_datasets: List[str] = []

    if dataset_ids:
        target_datasets = normalize_dataset_ids(dataset_ids)
        logger.info(f"[KnowledgeTool] Used explicit dataset_ids (normalized): {target_datasets}")
    else:
        context_datasets = get_current_agent_config("dataset_ids")
        if context_datasets:
            target_datasets = normalize_dataset_ids(context_datasets)

        if not target_datasets:
            default_ids_str = await ConfigService.get("ragflow_dataset_ids")
            if default_ids_str:
                target_datasets = normalize_dataset_ids(default_ids_str)
            logger.info(f"[KnowledgeTool] Fallback to system default: {target_datasets}")

    if dataset_ids and not target_datasets:
        return (
            "[Tool Error] Invalid dataset_ids. Use a plain 32-char ID, "
            "comma-separated IDs, or a single-quoted list like "
            "['4525d66cec7111f0a3d00242ac120006'] — do not use [\"...\"]."
        )

    if not target_datasets:
        logger.warning("[KnowledgeTool] No datasets configured.")
        return "[System Warning] No knowledge base datasets configured. Please contact admin to set 'ragflow_dataset_ids'."

    ctx = get_current_agent_context()
    if ctx and ctx.user_id and not ctx.is_admin:
        user_name = (ctx.user_dimensions or {}).get("user_name")
        async with AsyncSessionLocal() as session:
            perm = PermissionService(session)
            before = list(target_datasets)
            target_datasets = await perm.filter_knowledge_dataset_ids(
                int(ctx.user_id),
                user_name,
                target_datasets,
            )
            denied = [d for d in before if d not in target_datasets]
            if denied:
                logger.warning(
                    "[KnowledgeTool] Removed datasets without permission: %s",
                    denied,
                )
        if not target_datasets:
            return (
                "[Tool Error] No permission to search the requested knowledge base. "
                "You may only use datasets assigned to you or created by yourself."
            )

    # 2. Resolve Parameters (Threshold & Weight)
    # Priority: Agent Engine Config > System Config > Hardcoded Default
    
    # Default values
    threshold = 0.2
    vector_weight = 0.3
    
    # Fetch from Agent Context
    engine_config = get_current_agent_config("engine_config")
    
    # Check if we have engine_config in context (might be null if simple local agent)
    # The 'get_current_agent_config' helper might just return top-level keys. 
    # Let's check how context works. Usually we store specific keys. 
    # If not found, we can try to fetch agent_id and look up, but context should have it if set correctly.
    # Assuming 'engine_config' might NOT be in the simplified context dict. 
    # However, 'dataset_ids' was there. Let's look at agent_service.py 'set_agent_context' again.
    
    # Re-reading agent_service.py: set_agent_context({...}) sets: agent_id, agent_name, dataset_ids, engine_type.
    # It does NOT set full engine_config! So we can't get it from context directly unless we change agent_service.
    # BUT, we can read system config first.
    
    # To get Agent-specific threshold without changing context structure too much, we could:
    # A) Change agent_service.py to inject these into context.
    # B) (Preferred for cleaner separation) Fetch config if needed? No, context is best.
    
    # Let's assume for now I will rely on System Config defaults first, 
    # AND I will look for injected variables if I change agent_service.
    # Wait, the Plan said: "Read ... from the current agent's engine_config."
    
    # Let's look at get_current_agent_config implementation or usage. 
    # Since I cannot modify 'context.py' easily without seeing it, I'll update 'agent_service.py' to inject these values.
    # OR, I will try to fetch them from System Config now, and update agent_service later.
    
    # Let's first get System Configs
    sys_threshold = await ConfigService.get("ragflow_similarity_threshold")
    sys_weight = await ConfigService.get("ragflow_vector_weight")
    
    if sys_threshold:
        try:
            threshold = float(sys_threshold)
        except:
            pass
            
    if sys_weight:
        try:
            vector_weight = float(sys_weight)
        except:
            pass
            
    # Now check Agent Config overrides (passed via Context)
    # I need to update agent_service.py to pass these.
    agent_threshold = get_current_agent_config("ragflow_threshold")
    agent_weight = get_current_agent_config("ragflow_vector_weight")
    
    if agent_threshold is not None:
         try:
             threshold = float(agent_threshold)
         except:
             pass
             
    if agent_weight is not None:
         try:
             vector_weight = float(agent_weight)
         except:
             pass

    logger.info(f"[KnowledgeTool] Using params: threshold={threshold}, vector_weight={vector_weight}")

    try:
        chunks = await client.retrieve(
            query, 
            target_datasets, 
            similarity_threshold=threshold,
            vector_similarity_weight=vector_weight
        )
        
        if not chunks:
            return "No relevant information found in the knowledge base."
            
        # --- [NEW: Process for Inline Citations] ---
        # 1. Assign sequential IDs to chunks for easier LLM referencing
        # 2. Format a clear prompt instruction for the LLM
        formatted_context = "I found the following information in the knowledge base. Please provide a detailed answer based ON THESE DOCUMENTS ONLY. \n"
        formatted_context += "CRITICAL: For every statement you make based on a document, append its reference as [ID:n] at the end of the sentence.\n\n"
        
        for i, chunk in enumerate(chunks):
            ref_id = str(i + 1)
            # Inject the simple ID into the chunk object so frontend can match it later
            chunk["id"] = ref_id
            
            doc_name = chunk.get("doc_name") or chunk.get("document_name") or "Unknown Document"
            content = chunk.get("content", "").strip()
            
            formatted_context += f"--- [ID:{ref_id}] Source: {doc_name} ---\n{content}\n\n"

        # Return a structured JSON string. The Executor will unpack this.
        # 'content' is what the LLM will see.
        # 'citations' is the metadata for the frontend.
        result = {
            "content": formatted_context,
            "citations": chunks
        }
        return json.dumps(result, ensure_ascii=False)
        
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Knowledge Search Failed: {e}", exc_info=True)
        if MetadataRagService._is_service_unavailable(err_msg):
            return MetadataRagService.knowledge_unavailable_hint(err_msg)
        return f"[Tool Error] Failed to search knowledge base: {err_msg}"
